"""
Модуль для публикации фотографий в Telegram
"""
import requests
from typing import List, Dict
import time
import html
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class TelegramPublisher:
    """Класс для публикации в Telegram группу"""
    
    BASE_URL = 'https://api.telegram.org/bot{token}/{method}'
    REQUEST_TIMEOUT = 30  # секунды
    MAX_CAPTION_LENGTH = 1024  # лимит Telegram
    RATE_LIMIT_INTERVAL = 0.05  # 50ms между запросами (безопасный лимит)
    
    def __init__(self, token: str, chat_id: str):
        """
        Инициализация publisher'а
        
        Args:
            token: Токен Telegram бота
            chat_id: ID чата/группы для публикации
            
        Raises:
            ValueError: Если токен или chat_id некорректны
        """
        if not token or len(token) < 20:
            raise ValueError("Некорректный токен Telegram бота")
        if not chat_id:
            raise ValueError("Chat ID не может быть пустым")
        
        self.token = token
        self.chat_id = chat_id
        self.last_request_time = None
        
        # Для логов используем замаскированный токен
        self._masked_token = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
        logger.info(f"✅ TelegramPublisher инициализирован (токен: {self._masked_token}, chat: {chat_id})")
    
    def _rate_limit(self):
        """Применяет rate limiting между запросами к Telegram API"""
        if self.last_request_time:
            elapsed = (datetime.now() - self.last_request_time).total_seconds()
            if elapsed < self.RATE_LIMIT_INTERVAL:
                sleep_time = self.RATE_LIMIT_INTERVAL - elapsed
                logger.debug(f"⏱️ Rate limit: ожидание {sleep_time:.3f}s")
                time.sleep(sleep_time)
        self.last_request_time = datetime.now()
    
    def send_message(self, text: str) -> bool:
        """
        Отправка текстового сообщения
        
        Args:
            text: Текст сообщения
        
        Returns:
            True если успешно, False если ошибка
        """
        self._rate_limit()
        
        url = self.BASE_URL.format(token=self.token, method='sendMessage')
        
        # Экранируем HTML для безопасности
        safe_text = html.escape(text)
        
        data = {
            'chat_id': self.chat_id,
            'text': safe_text,
            'parse_mode': 'HTML'
        }
        
        try:
            response = requests.post(url, json=data, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            logger.info("✅ Сообщение отправлено")
            return True
        except requests.exceptions.Timeout:
            logger.error("⏱️ Timeout при отправке сообщения")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка отправки сообщения: {e}")
            # Логируем детали ошибки если доступны
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
            return False
    
    def publish_photos(self, photos: List[Dict], date_str: str) -> bool:
        """
        Публикация группы фотографий
        
        Args:
            photos: Список фотографий
            date_str: Строка с датой для подписи
        
        Returns:
            True если все фото опубликованы успешно, False если были ошибки
        """
        if not photos:
            logger.warning("⚠️ Нет фото для публикации")
            return False
        
        # Если фото больше 10, разбиваем на группы (лимит Telegram - 10 медиа в группе)
        max_photos_per_group = 10
        success = True
        
        for i in range(0, len(photos), max_photos_per_group):
            photo_group = photos[i:i + max_photos_per_group]
            
            if len(photo_group) == 1:
                # Одно фото - отправляем как обычное фото с подписью
                result = self._send_single_photo(photo_group[0], date_str)
            else:
                # Несколько фото - отправляем как медиа-группу
                result = self._send_media_group(photo_group, date_str if i == 0 else None)
            
            if not result:
                success = False
                logger.error(f"❌ Ошибка при публикации группы {i // max_photos_per_group + 1}")
            
            # Задержка между группами для соблюдения rate limits
            if i + max_photos_per_group < len(photos):
                time.sleep(1)
        
        return success
    
    def _send_single_photo(self, photo: Dict, caption: str) -> bool:
        """
        Отправка одной фотографии
        
        Args:
            photo: Данные фотографии
            caption: Подпись к фото
        
        Returns:
            True если успешно
        """
        self._rate_limit()
        
        # Проверяем наличие download_url
        download_url = photo.get('download_url')
        if not download_url:
            logger.warning(f"⚠️ Нет URL для скачивания: {photo.get('name', 'unknown')}")
            return False
        
        url = self.BASE_URL.format(token=self.token, method='sendPhoto')
        
        # Формируем подпись
        full_caption = self._format_caption([photo], caption)
        
        data = {
            'chat_id': self.chat_id,
            'photo': download_url,
            'caption': full_caption,
            'parse_mode': 'HTML'
        }
        
        try:
            response = requests.post(url, json=data, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            logger.info(f"✅ Отправлено фото: {photo.get('name', 'unknown')}")
            return True
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ Timeout при отправке фото: {photo.get('name', 'unknown')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка отправки фото {photo.get('name', 'unknown')}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
            return False
    
    def _send_media_group(self, photos: List[Dict], caption: str = None) -> bool:
        """
        Отправка медиа-группы (до 10 фото)
        
        Args:
            photos: Список фотографий
            caption: Подпись (только для первого фото в группе)
        
        Returns:
            True если успешно
        """
        self._rate_limit()
        
        url = self.BASE_URL.format(token=self.token, method='sendMediaGroup')
        
        # Формируем массив медиа
        media = []
        for idx, photo in enumerate(photos):
            download_url = photo.get('download_url')
            
            # Пропускаем фото без URL
            if not download_url:
                logger.warning(f"⚠️ Пропуск фото без URL: {photo.get('name', 'unknown')}")
                continue
            
            media_item = {
                'type': 'photo',
                'media': download_url
            }
            
            # Подпись только к первому фото
            if idx == 0 and caption:
                media_item['caption'] = self._format_caption(photos, caption)
                media_item['parse_mode'] = 'HTML'
            
            media.append(media_item)
        
        # Проверяем что есть хотя бы одно фото для отправки
        if not media:
            logger.error("❌ Нет валидных фото для отправки в медиа-группе")
            return False
        
        data = {
            'chat_id': self.chat_id,
            'media': media
        }
        
        try:
            response = requests.post(url, json=data, timeout=self.REQUEST_TIMEOUT)
            response.raise_for_status()
            logger.info(f"✅ Отправлена медиа-группа из {len(media)} фото")
            return True
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ Timeout при отправке медиа-группы из {len(media)} фото")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка отправки медиа-группы: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
            return False
    
    def _format_caption(self, photos: List[Dict], date_str: str) -> str:
        """
        Форматирование подписи к фото
        
        Args:
            photos: Список фотографий
            date_str: Дата в формате DD.MM.YYYY
        
        Returns:
            Отформатированная подпись
        """
        # Экранируем дату для безопасности
        safe_date = html.escape(date_str)
        
        # Основной заголовок
        caption = f"📅 <b>{safe_date}</b>\n"
        
        # Количество фото
        if len(photos) > 1:
            caption += f"🖼 {len(photos)} фотографий\n"
        
        # Эмодзи в зависимости от года
        year = photos[0]['year']
        current_year = datetime.now().year  # ✅ ИСПРАВЛЕНО: динамическое определение года
        years_ago = current_year - year
        
        if years_ago == 0:
            caption += f"\n📸 Сегодня!"
        elif years_ago == 1:
            caption += f"\n🕐 Год назад"
        elif years_ago < 5:
            caption += f"\n🕑 {years_ago} года назад"
        elif years_ago < 10:
            caption += f"\n🕔 {years_ago} лет назад"
        else:
            caption += f"\n⏳ {years_ago} лет назад"
        
        # Проверяем лимит длины подписи
        if len(caption) > self.MAX_CAPTION_LENGTH:
            logger.warning(f"⚠️ Подпись слишком длинная ({len(caption)}), обрезаем до {self.MAX_CAPTION_LENGTH}")
            caption = caption[:self.MAX_CAPTION_LENGTH - 3] + "..."
        
        return caption
