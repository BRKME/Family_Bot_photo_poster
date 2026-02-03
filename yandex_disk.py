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
        
        logger.info(f"🔍 Начинаем поиск фото за {day}.{month:02d}")
        
        photos = []
        photos.extend(self._search_in_files_api(day, month))
        photos.extend(self._search_in_folder('/Фотокамера', day, month))
        photos.extend(self._search_in_photounlim(day, month))
        
        photos = list({p['path']: p for p in photos}.values())
        photos.sort(key=lambda x: x['year'])
        
        logger.info(f"✅ Итого найдено {len(photos)} уникальных фото за {day}.{month:02d}")
        
        return photos
    
    def _search_in_files_api(self, day: int, month: int) -> List[Dict]:
        photos = []
        offset = 0
        limit = 1000
        total_processed = 0
        
        while True:
            url = f'{self.BASE_URL}/resources/files'
            params = {
                'media_type': 'image',
                'limit': limit,
                'offset': offset
            }
            
            try:
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
                    break
                
                total_processed += len(items)
                logger.info(f"📊 Обработано {total_processed} файлов...")
                
                for item in items:
                    # Пропускаем видео файлы
                    name = item.get('name', '').lower()
                    if name.endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv')):
                        continue
                    
                    photo_date = self._extract_date(item)
                    
                    if photo_date and photo_date.day == day and photo_date.month == month:
                        download_url = item.get('file')
                        
                        if not download_url:
                            logger.warning(f"⚠️ Нет URL для скачивания: {item['name']}")
                            continue
                        
                        logger.info(f"✅ Найдено: {item['name']} → {photo_date.strftime('%Y-%m-%d')}")
                        
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
                    logger.info(f"✅ Основные папки: обработано {total_processed} файлов")
                    break
                
                offset += limit
                
            except requests.exceptions.Timeout:
                logger.error(f"⏱️ Timeout при запросе к Яндекс.Диску (offset={offset})")
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Ошибка при запросе к Яндекс.Диску: {e}")
                break
        
        if photos:
            logger.info(f"✅ Основные папки: найдено {len(photos)} фото")
        return photos
    
    def _search_in_photounlim(self, day: int, month: int) -> List[Dict]:
        photos = []
        offset = 0
        limit = 1000
        total_processed = 0
        
        while True:
            url = f'{self.BASE_URL}/resources'
            params = {
                'path': '/photounlim',
                'limit': limit,
                'offset': offset,
                'fields': 'items.name,items.path,items.file,items.created,items.modified,items.exif,items.size,items.type'
            }
            
            try:
                response = requests.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=self.REQUEST_TIMEOUT
                )
                
                if response.status_code == 404:
                    logger.debug(f"⚠️ Папка /photounlim не найдена")
                    break
                
                if response.status_code == 403:
                    logger.debug(f"⚠️ Нет доступа к /photounlim")
                    break
                
                response.raise_for_status()
                data = response.json()
                
                items = data.get('_embedded', {}).get('items', [])
                if not items:
                    break
                
                total_processed += len(items)
                logger.info(f"📊 Фотопоток: обработано {total_processed} файлов...")
                
                for item in items:
                    if item.get('type') != 'file':
                        continue
                    
                    # Пропускаем видео файлы
                    name = item.get('name', '').lower()
                    if name.endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv')):
                        continue
                    
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
                    logger.info(f"✅ Фотопоток: обработано {total_processed} файлов")
                    break
                
                offset += limit
                
            except requests.exceptions.Timeout:
                logger.error(f"⏱️ Timeout при запросе к Фотопотоку")
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Ошибка при запросе к Фотопотоку: {e}")
                break
        
        if photos:
            logger.info(f"✅ Фотопоток: найдено {len(photos)} фото")
        return photos
    
    def _search_in_folder(self, folder_path: str, day: int, month: int) -> List[Dict]:
        logger.info(f"🔍 Поиск в папке {folder_path}...")
        
        photos = []
        offset = 0
        limit = 1000
        total_processed = 0
        
        while True:
            url = f'{self.BASE_URL}/resources'
            params = {
                'path': folder_path,
                'limit': limit,
                'offset': offset,
                'fields': 'items.name,items.path,items.file,items.created,items.modified,items.exif,items.size,items.type'
            }
            
            try:
                response = requests.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=self.REQUEST_TIMEOUT
                )
                
                if response.status_code == 404:
                    logger.warning(f"⚠️ Папка {folder_path} не найдена")
                    break
                
                response.raise_for_status()
                data = response.json()
                
                items = data.get('_embedded', {}).get('items', [])
                if not items:
                    break
                
                total_processed += len(items)
                logger.info(f"📊 {folder_path}: обработано {total_processed} файлов...")
                
                for item in items:
                    if item.get('type') != 'file':
                        continue
                    
                    # Пропускаем видео файлы
                    name = item.get('name', '').lower()
                    if name.endswith(('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv')):
                        continue
                    
                    photo_date = self._extract_date(item)
                    
                    if photo_date and photo_date.day == day and photo_date.month == month:
                        download_url = item.get('file')
                        
                        if not download_url:
                            logger.warning(f"⚠️ Нет URL для скачивания: {item['name']}")
                            continue
                        
                        logger.info(f"✅ {folder_path}: {item['name']} → {photo_date.strftime('%Y-%m-%d')}")
                        
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
                    logger.info(f"✅ {folder_path}: обработано {total_processed} файлов")
                    break
                
                offset += limit
                
            except requests.exceptions.Timeout:
                logger.error(f"⏱️ Timeout при запросе к {folder_path}")
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Ошибка при запросе к {folder_path}: {e}")
                break
        
        logger.info(f"✅ {folder_path}: найдено {len(photos)} фото")
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
