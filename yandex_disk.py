"""
Модуль для работы с Яндекс.Диск API
"""
import requests
from datetime import datetime
from typing import List, Dict, Optional
import re
import logging

logger = logging.getLogger(__name__)


class YandexDiskClient:
    BASE_URL = 'https://cloud-api.yandex.net/v1/disk'
    REQUEST_TIMEOUT = 30
    
    def __init__(self, token: str):
        if not token or len(token) < 20:
            raise ValueError("Некорректный токен Яндекс.Диска")
        
        self.token = token
        self.headers = {
            'Authorization': f'OAuth {token}',
            'Content-Type': 'application/json'
        }
        
        self._masked_token = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
        logger.info(f"✅ YandexDiskClient инициализирован (токен: {self._masked_token})")
    
    def find_photos_by_date(self, day: int, month: int) -> List[Dict]:
        if not 1 <= day <= 31:
            raise ValueError(f"День должен быть 1-31, получено: {day}")
        if not 1 <= month <= 12:
            raise ValueError(f"Месяц должен быть 1-12, получено: {month}")
        
        photos = []
        offset = 0
        limit = 1000
        total_processed = 0
        
        logger.info(f"🔍 Начинаем поиск фото за {day}.{month:02d}")
        
        while True:
            url = f'{self.BASE_URL}/resources/files'
            params = {
                'media_type': 'image',
                'limit': limit,
                'offset': offset
            }
            
            try:
                logger.debug(f"📡 Запрос к API: offset={offset}, limit={limit}")
                response = requests.get(
                    url, 
                    headers=self.headers, 
                    params=params, 
                    timeout=self.REQUEST_TIMEOUT
                )
                response.raise_for_status()
                data = response.json()
                
                items = data.get('items', [])
                if not items:
                    logger.info(f"📊 Больше файлов нет (обработано {total_processed})")
                    break
                
                total_processed += len(items)
                logger.info(f"📊 Обработано {total_processed} файлов...")
                
                for item in items:
                    photo_date = self._extract_date(item)
                    
                    if photo_date and photo_date.day == day and photo_date.month == month:
                        download_url = item.get('file')
                        
                        if not download_url:
                            logger.warning(f"⚠️ Нет URL для скачивания: {item['name']}")
                            continue
                        
                        photos.append({
                            'name': item['name'],
                            'path': item['path'],
                            'download_url': download_url,
                            'created': item.get('created'),
                            'modified': item.get('modified'),
                            'date': photo_date,
                            'year': photo_date.year,
                            'size': item.get('size', 0)
                        })
                
                if len(items) < limit:
                    logger.info(f"✅ Достигнута последняя страница (всего обработано {total_processed})")
                    break
                
                offset += limit
                
            except requests.exceptions.Timeout:
                logger.error(f"⏱️ Timeout при запросе к Яндекс.Диску (offset={offset})")
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Ошибка при запросе к Яндекс.Диску: {e}")
                break
        
        photos.sort(key=lambda x: x['year'])
        logger.info(f"✅ Найдено {len(photos)} фото за {day}.{month:02d} из {total_processed} файлов")
        
        return photos
    
    def _extract_date(self, item: Dict) -> Optional[datetime]:
        exif = item.get('exif', {})
        if exif.get('date_time'):
            try:
                return datetime.strptime(exif['date_time'], '%Y:%m:%d %H:%M:%S')
            except (ValueError, TypeError):
                pass
        
        date_from_path = self._extract_date_from_path(item.get('path', ''))
        if date_from_path:
            return date_from_path
        
        date_from_name = self._extract_date_from_filename(item.get('name', ''))
        if date_from_name:
            return date_from_name
        
        for date_field in ['created', 'modified']:
            if item.get(date_field):
                try:
                    date_str = item[date_field].split('+')[0].split('.')[0].replace('Z', '')
                    return datetime.fromisoformat(date_str)
                except (ValueError, TypeError):
                    pass
        
        return None
    
    def _extract_date_from_path(self, path: str) -> Optional[datetime]:
        if not path:
            return None
        
        months = {'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'мая': 5, 'июн': 6,
                  'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12}
        
        match = re.search(r'(\d{1,2})\s+([а-я]+)\s+(\d{4})', path, re.I)
        if match:
            day, month_name, year = match.groups()
            for prefix, num in months.items():
                if month_name.lower().startswith(prefix):
                    try:
                        return datetime(int(year), num, int(day))
                    except ValueError:
                        return None
        
        return None
    
    def _extract_date_from_filename(self, filename: str) -> Optional[datetime]:
        patterns = [
            r'\b(\d{4})-(\d{2})-(\d{2})\b',
            r'\b(\d{4})(\d{2})(\d{2})\b',
            r'\b(\d{2})\.(\d{2})\.(\d{4})\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                try:
                    groups = match.groups()
                    if len(groups[0]) == 4:
                        year, month, day = groups
                    else:
                        day, month, year = groups
                    
                    date = datetime(int(year), int(month), int(day))
                    current_year = datetime.now().year
                    if 1990 <= date.year <= current_year:
                        return date
                    
                except (ValueError, IndexError):
                    continue
        
        return None
