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
        self._debug_counter = 0
        self._max_debug = 20
    
    def find_photos_by_date(self, day: int, month: int) -> List[Dict]:
        if not 1 <= day <= 31:
            raise ValueError(f"День должен быть 1-31, получено: {day}")
        if not 1 <= month <= 12:
            raise ValueError(f"Месяц должен быть 1-12, получено: {month}")
        
        photos = []
        offset = 0
        limit = 1000
        total_processed = 0
        self._debug_counter = 0
        matches_found = 0
        extensions_seen = {}
        photounlim_count = 0
        
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
                
                if offset == 0:
                    for item in items[:5]:
                        logger.info(f"📁 Пример: {item.get('name', 'N/A')} → путь: {item.get('path', 'N/A')}")
                
                total_processed += len(items)
                logger.info(f"📊 Обработано {total_processed} файлов...")
                
                for item in items:
                    ext = item.get('name', '').split('.')[-1].upper() if '.' in item.get('name', '') else 'NO_EXT'
                    extensions_seen[ext] = extensions_seen.get(ext, 0) + 1
                    
                    if 'photounlim' in item.get('path', ''):
                        photounlim_count += 1
                    
                    photo_date = self._extract_date(item)
                    
                    if photo_date and photo_date.day == day and photo_date.month == month:
                        matches_found += 1
                        logger.info(f"🔍 #{matches_found} Файл с нужной датой: {item['name']} → {photo_date.strftime('%Y-%m-%d')} (путь: {item.get('path', 'N/A')})")
                        
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
        
        logger.info(f"📊 Статистика: найдено {matches_found} файлов с датой {day}.{month:02d}, добавлено {len(photos)} фото (обработано {total_processed} файлов)")
        
        top_extensions = sorted(extensions_seen.items(), key=lambda x: x[1], reverse=True)[:10]
        logger.info(f"📊 Топ расширений: {', '.join([f'{ext}={count}' for ext, count in top_extensions])}")
        logger.info(f"📊 JPEG файлы: {extensions_seen.get('JPEG', 0)}, файлов в photounlim: {photounlim_count}")
        
        return photos
    
    def _extract_date(self, item: Dict) -> Optional[datetime]:
        name = item.get('name', 'unknown')
        show_debug = self._debug_counter < self._max_debug
        
        exif = item.get('exif', {})
        if exif.get('date_time'):
            try:
                date = datetime.strptime(exif['date_time'], '%Y:%m:%d %H:%M:%S')
                if show_debug:
                    logger.debug(f"✅ {name}: EXIF → {date.strftime('%Y-%m-%d')}")
                    self._debug_counter += 1
                return date
            except (ValueError, TypeError):
                pass
        
        date_from_path = self._extract_date_from_path(item.get('path', ''))
        if date_from_path:
            if show_debug:
                logger.debug(f"✅ {name}: Path → {date_from_path.strftime('%Y-%m-%d')}")
                self._debug_counter += 1
            return date_from_path
        
        date_from_name = self._extract_date_from_filename(name)
        if date_from_name:
            if show_debug:
                logger.debug(f"✅ {name}: Filename → {date_from_name.strftime('%Y-%m-%d')}")
                self._debug_counter += 1
            return date_from_name
        
        for date_field in ['created', 'modified']:
            if item.get(date_field):
                try:
                    date_str = item[date_field].split('+')[0].split('.')[0].replace('Z', '')
                    date = datetime.fromisoformat(date_str)
                    if show_debug:
                        logger.debug(f"✅ {name}: {date_field} → {date.strftime('%Y-%m-%d')}")
                        self._debug_counter += 1
                    return date
                except (ValueError, TypeError):
                    pass
        
        if show_debug:
            logger.debug(f"⚠️ {name}: дата не найдена")
            self._debug_counter += 1
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
        
        for idx, pattern in enumerate(patterns):
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
                    else:
                        if self._debug_counter < self._max_debug:
                            logger.debug(f"⚠️ {filename}: год {date.year} вне диапазона 1990-{current_year}")
                            self._debug_counter += 1
                    
                except (ValueError, IndexError) as e:
                    if self._debug_counter < self._max_debug:
                        logger.debug(f"⚠️ {filename}: ошибка парсинга даты из паттерна {idx}: {e}")
                        self._debug_counter += 1
                    continue
        
        return None
