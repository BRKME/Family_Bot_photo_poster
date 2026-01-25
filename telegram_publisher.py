"""
Модуль для публикации фотографий в Telegram
"""
import requests
from typing import List, Dict
import time
import html
import logging
import random
from datetime import datetime

logger = logging.getLogger(__name__)


class TelegramPublisher:
    BASE_URL = 'https://api.telegram.org/bot{token}/{method}'
    REQUEST_TIMEOUT = 30
    MAX_CAPTION_LENGTH = 1024
    RATE_LIMIT_INTERVAL = 0.05
    
    FAMILY_NAMES = ["Мама", "Марта", "Аркаша", "Папа", "Лилу"]
    
    RANDOM_QUESTIONS = [
        "Кто вспомнит, где это было? 🤔",
        "Узнаёте место? 🗺️",
        "Помните этот день? 📸",
        "Что за событие? 🎉",
        "Где мы тут были? 🌍",
        "Что за место? 🏛️",
    ]
    
    QUESTIONS_WITH_NAMES = [
        "Узнаёте {name} на фото? 😊",
        "Где были {name1} и {name2}? 🚗",
        "Помните эту поездку с {name}? 🌍",
        "Кто был вместе с {name}? 👥",
        "Что делала {name}? 🤔",
        "{name}, помнишь этот момент? 📸",
        "Где это {name1} с {name2}? 🗺️",
    ]
    
    def __init__(self, token: str, chat_id: str):
        if not token or len(token) < 20:
            raise ValueError("Некорректный токен Telegram бота")
        if not chat_id:
            raise ValueError("Chat ID не может быть пустым")
        
        self.token = token
        self.chat_id = chat_id
        self.last_request_time = None
        
        self._masked_token = f"{token[:10]}...{token[-4:]}" if len(token) > 14 else "***"
        logger.info(f"✅ TelegramPublisher инициализирован (токен: {self._masked_token}, chat: {chat_id})")
    
    def _get_random_question(self) -> str:
        use_names = random.choice([True, False])
        
        if use_names:
            question_template = random.choice(self.QUESTIONS_WITH_NAMES)
            
            if '{name1}' in question_template and '{name2}' in question_template:
                names = random.sample(self.FAMILY_NAMES, 2)
                return question_template.format(name1=names[0], name2=names[1])
            else:
                name = random.choice(self.FAMILY_NAMES)
                return question_template.format(name=name)
        else:
            return random.choice(self.RANDOM_QUESTIONS)
    
    def _rate_limit(self):
        if self.last_request_time:
            elapsed = (datetime.now() - self.last_request_time).total_seconds()
            if elapsed < self.RATE_LIMIT_INTERVAL:
                sleep_time = self.RATE_LIMIT_INTERVAL - elapsed
                logger.debug(f"⏱️ Rate limit: ожидание {sleep_time:.3f}s")
                time.sleep(sleep_time)
        self.last_request_time = datetime.now()
    
    def send_message(self, text: str) -> bool:
        self._rate_limit()
        
        url = self.BASE_URL.format(token=self.token, method='sendMessage')
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
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
            return False
    
    def publish_photos(self, photos: List[Dict], date_str: str) -> bool:
        if not photos:
            logger.warning("⚠️ Нет фото для публикации")
            return False
        
        max_photos_per_group = 10
        success = True
        
        for i in range(0, len(photos), max_photos_per_group):
            photo_group = photos[i:i + max_photos_per_group]
            
            if len(photo_group) == 1:
                result = self._send_single_photo(photo_group[0], date_str)
            else:
                result = self._send_media_group(photo_group, date_str if i == 0 else None)
            
            if not result:
                success = False
                logger.error(f"❌ Ошибка при публикации группы {i // max_photos_per_group + 1}")
            
            if i + max_photos_per_group < len(photos):
                time.sleep(1)
        
        return success
    
    def _send_single_photo(self, photo: Dict, caption: str) -> bool:
        self._rate_limit()
        
        download_url = photo.get('download_url')
        if not download_url:
            logger.warning(f"⚠️ Нет URL для скачивания: {photo.get('name', 'unknown')}")
            return False
        
        url = self.BASE_URL.format(token=self.token, method='sendPhoto')
        
        year = photo.get('year', '')
        random_question = self._get_random_question()
        full_caption = f"📅 {caption}\n\n{random_question}\n\n{year} год" if year else f"📅 {caption}\n\n{random_question}"
        
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
        self._rate_limit()
        
        url = self.BASE_URL.format(token=self.token, method='sendMediaGroup')
        
        media = []
        for idx, photo in enumerate(photos):
            download_url = photo.get('download_url')
            
            if not download_url:
                logger.warning(f"⚠️ Пропуск фото без URL: {photo.get('name', 'unknown')}")
                continue
            
            year = photo.get('year', '')
            
            if idx == 0 and caption:
                random_question = self._get_random_question()
                photo_caption = f"📅 {caption}\n\n{random_question}\n\n{year}"
            else:
                photo_caption = f"{year}"
            
            media_item = {
                'type': 'photo',
                'media': download_url,
                'caption': photo_caption
            }
            
            media.append(media_item)
        
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
