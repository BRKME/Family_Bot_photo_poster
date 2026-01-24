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
    """Клиент для работы с Яндекс.Диском"""
    
    BASE_URL = 'https://cloud-api.yandex.net/v1/disk'
    REQUEST_TIMEOUT = 30  # секунды
    
    def __init__(self, token: str):
        """
        Инициализация клиента
        
        Args:
            token: OAuth токен Яндекс.Диска
            
        Raises:
            ValueError: Если токен некорректный
        """
        if not token or len(token) < 20:
            raise ValueError("Некорректный токен Яндекс.Диска")
        
        self.token = token
        self.headers = {
            'Authorization': f'OAuth {token}',
            'Content-Type': 'application/json'
        }
        
        # Для логов используем замаскированный токен
        self._masked_token = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
        logger.info(f"✅ YandexDiskClient инициализирован (токен: {self._masked_token})")
    
    def find_photos_by_date(self, day: int, month: int) -> List[Dict]:
        """
        Поиск фотографий по дате (день и месяц) во всех годах
        
        Args:
            day: День (1-31)
            month: Месяц (1-12)
        
        Returns:
            Список словарей с информацией о фотографиях
            
        Raises:
            ValueError: Если день или месяц некорректны
        """
        # Валидация входных параметров
        if not 1 <= day <= 31:
            raise ValueError(f"День должен быть 1-31, получено: {day}")
        if not 1 <= month <= 12:
            raise ValueError(f"Месяц должен быть 1-12, получено: {month}")
        
        photos = []
        
        # Используем пагинацию для получения всех файлов
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
                
                # Фильтруем по дате
                for item in items:
                    photo_date = self._extract_date(item)
                    
                    if photo_date and photo_date.day == day and photo_date.month == month:
                        download_url = item.get('file')
                        
                        # Проверяем наличие download_url
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
                
                # Проверяем, есть ли еще страницы
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
        
        # Сортируем по году
        photos.sort(key=lambda x: x['year'])
        
        logger.info(f"✅ Найдено {len(photos)} фото за {day}.{month:02d} из {total_processed} файлов")
        
        return photos
    
    def _extract_date(self, item: Dict) -> Optional[datetime]:
        """
        Извлечение даты из метаданных файла
        
        Пробуем несколько источников:
        1. EXIF дата (если есть)
        2. Дата создания файла
        3. Дата из имени файла (паттерны типа 2024-01-15, IMG_20240115 и т.д.)
        
        Args:
            item: Элемент из Яндекс.Диска
        
        Returns:
            datetime объект или None
        """
        # 1. Пробуем получить EXIF дату
        exif = item.get('exif', {})
        if exif.get('date_time'):
            try:
                # EXIF дата в формате "YYYY:MM:DD HH:MM:SS"
                date_str = exif['date_time']
                return datetime.strptime(date_str, '%Y:%m:%d %H:%M:%S')
            except (ValueError, TypeError) as e:
                logger.debug(f"⚠️ Не удалось распарсить EXIF дату: {e}")
        
        # 2. Дата создания или модификации
        for date_field in ['created', 'modified']:
            if item.get(date_field):
                try:
                    # ISO формат: "2024-01-15T10:30:00+00:00"
                    date_str = item[date_field]
                    # Убираем timezone и microseconds для упрощения
                    date_str = date_str.split('+')[0].split('.')[0].replace('Z', '')
                    return datetime.fromisoformat(date_str)
                except (ValueError, TypeError) as e:
                    logger.debug(f"⚠️ Не удалось распарсить дату {date_field}: {e}")
        
        # 3. Пробуем извлечь дату из имени файла
        filename = item.get('name', '')
        date_from_name = self._extract_date_from_filename(filename)
        if date_from_name:
            return date_from_name
        
        return None
    
    def _extract_date_from_filename(self, filename: str) -> Optional[datetime]:
        """
        Извлечение даты из имени файла
        
        Поддерживаемые форматы:
        - 2024-01-15.jpg
        - 20240115_123456.jpg
        - IMG_20240115.jpg
        - Photo 2024-01-15.jpg
        - 15.01.2024.jpg
        
        Args:
            filename: Имя файла
        
        Returns:
            datetime объект или None
        """
        # Паттерны для поиска даты (добавлены word boundaries для точности)
        patterns = [
            r'\b(\d{4})-(\d{2})-(\d{2})\b',  # 2024-01-15
            r'\b(\d{4})(\d{2})(\d{2})\b',     # 20240115
            r'\b(\d{2})\.(\d{2})\.(\d{4})\b', # 15.01.2024
        ]
        
        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                try:
                    groups = match.groups()
                    # Определяем формат
                    if len(groups[0]) == 4:  # YYYY-MM-DD или YYYYMMDD
                        year, month, day = groups
                    else:  # DD.MM.YYYY
                        day, month, year = groups
                    
                    # Валидация даты
                    date = datetime(int(year), int(month), int(day))
                    
                    # Фильтруем очевидно неправильные даты
                    current_year = datetime.now().year
                    if 1990 <= date.year <= current_year:
                        return date
                    
                except (ValueError, IndexError) as e:
                    logger.debug(f"⚠️ Не удалось создать дату из {groups}: {e}")
                    continue
        
        return None
