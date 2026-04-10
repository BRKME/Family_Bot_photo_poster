"""
Модуль для публикации фотографий в Telegram
v2.0 - загрузка фото байтами (fix WEBPAGE_CURL_FAILED)
v2.1 - валидация размеров изображений (fix PHOTO_INVALID_DIMENSIONS)
"""
import requests
from typing import List, Dict, Optional, Tuple
import time
import html
import logging
import random
import json
import io
from datetime import datetime
from PIL import Image

logger = logging.getLogger(__name__)


class TelegramPublisher:
    BASE_URL = 'https://api.telegram.org/bot{token}/{method}'
    REQUEST_TIMEOUT = 30
    MAX_CAPTION_LENGTH = 1024
    RATE_LIMIT_INTERVAL = 0.05
    
    FAMILY_NAMES = ["Саша", "Марта", "Аркадий", "Папа", "Лилу"]
    
    # Универсальные вопросы (без имен) - подходят для всего
    RANDOM_QUESTIONS = [
        "Кто вспомнит, что это было? 🤔",
        "Узнаёте? 😊",
        "Помните этот день? 📸",
        "Угадаете, что за событие? 🎉",
        "Где это мы? 🗺️",
        "Ностальгия! 💭",
        "Эх, времечко было! ⏰",
        "Вспоминаем? 🌟",
        "Какие эмоции! 😍",
        "Кто помнит детали? 🔍",
        "Классное было время! ✨",
        "Вот это воспоминания! 🎊",
        "Как быстро летит время! 💫",
        "Угадайте, что было дальше? 😄",
        "Кто помнит эту историю? 📖",
    ]
    
    # Вопросы с одним именем
    QUESTIONS_WITH_ONE_NAME = [
        "Узнаёте {name}? 😊",
        "{name}, помнишь? 📸",
        "Что делал(а) {name}? 🤔",
        "{name} в ударе! 🌟",
        "Как вам {name} тут? 😄",
        "{name} красавица/красавец! 💫",
        "Вот это {name}! 👏",
        "{name}, а помнишь этот момент? 💭",
        "Какое настроение у {name}? 😊",
        "{name} в главной роли! ⭐",
        "Узнали {name}? 👀",
        "{name}, расскажи историю! 📖",
        "Вспоминаем {name}! 🌈",
        "Что задумал(а) {name}? 🎭",
    ]
    
    # Вопросы с двумя именами
    QUESTIONS_WITH_TWO_NAMES = [
        "Где были {name1} и {name2}? 🗺️",
        "{name1} с {name2} - мечта! 💕",
        "Помните, {name1} и {name2}? 😊",
        "{name1} vs {name2} - кто круче? 😄",
        "Команда мечты: {name1} и {name2}! 🌟",
        "{name1} + {name2} = веселье! 🎉",
        "Узнали {name1} и {name2}? 👀",
        "{name1} и {name2} - что вы тут делали? 🎭",
        "{name1}, {name2} - ваши комментарии? 😄",
        "Кто круче: {name1} или {name2}? 🏆",
        "Дуэт года: {name1} и {name2}! 🎪",
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
            # Выбираем: один или два имени
            use_two_names = random.choice([True, False])
            
            if use_two_names:
                question_template = random.choice(self.QUESTIONS_WITH_TWO_NAMES)
                names = random.sample(self.FAMILY_NAMES, 2)
                return question_template.format(name1=names[0], name2=names[1])
            else:
                question_template = random.choice(self.QUESTIONS_WITH_ONE_NAME)
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
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                # Rate limit - получаем retry_after из ответа
                try:
                    error_data = e.response.json()
                    retry_after = error_data.get('parameters', {}).get('retry_after', 5)
                    logger.warning(f"⚠️ Rate limit! Ожидание {retry_after} секунд...")
                    time.sleep(retry_after + 1)  # +1 для гарантии
                    
                    # Повторная попытка
                    response = requests.post(url, json=data, timeout=self.REQUEST_TIMEOUT)
                    response.raise_for_status()
                    logger.info("✅ Сообщение отправлено (после retry)")
                    return True
                except Exception as retry_error:
                    logger.error(f"❌ Ошибка при retry: {retry_error}")
                    return False
            else:
                logger.error(f"❌ HTTP ошибка отправки сообщения: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
                return False
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
            
            # Отправляем текст перед первой группой фото
            if i == 0:
                random_question = self._get_random_question()
                text_message = f"📅 {date_str}\n\n{random_question}"
                if not self.send_message(text_message):
                    logger.warning("⚠️ Не удалось отправить текстовое сообщение")
                time.sleep(2)  # Увеличено для избежания rate limit
            
            if len(photo_group) == 1:
                result = self._send_single_photo(photo_group[0], date_str)
            else:
                result = self._send_media_group(photo_group, include_years=True)
            
            if not result:
                success = False
                logger.error(f"❌ Ошибка при публикации группы {i // max_photos_per_group + 1}")
            
            if i + max_photos_per_group < len(photos):
                time.sleep(2)  # Увеличено для избежания rate limit
        
        return success
    
    def _download_photo(self, url: str, name: str = "photo") -> Optional[bytes]:
        """Скачивает фото по URL и возвращает байты"""
        try:
            response = requests.get(url, timeout=60, stream=True)
            response.raise_for_status()
            data = response.content
            if len(data) < 100:
                logger.warning(f"⚠️ Слишком маленький файл ({len(data)} байт): {name}")
                return None
            logger.debug(f"📥 Скачано {len(data)} байт: {name}")
            return data
        except Exception as e:
            logger.error(f"❌ Ошибка скачивания {name}: {e}")
            return None
    
    def _validate_and_fix_image(self, photo_data: bytes, name: str = "photo") -> Optional[bytes]:
        """
        Проверяет и исправляет размеры изображения для Telegram.
        Telegram требует:
        - Сумма width + height >= 100
        - Ни одна сторона не > 10000 пикселей
        - Соотношение сторон не более 20:1
        """
        try:
            img = Image.open(io.BytesIO(photo_data))
            width, height = img.size
            original_size = (width, height)
            needs_fix = False
            
            # Проверка минимальных размеров
            if width + height < 100:
                logger.warning(f"⚠️ {name}: слишком маленькое ({width}x{height}), пропускаем")
                return None
            
            # Проверка максимальных размеров (10000 px)
            max_side = 10000
            if width > max_side or height > max_side:
                ratio = min(max_side / width, max_side / height)
                width = int(width * ratio)
                height = int(height * ratio)
                needs_fix = True
                logger.info(f"📐 {name}: уменьшаем с {original_size} до ({width}x{height})")
            
            # Проверка соотношения сторон (макс 20:1)
            aspect_ratio = max(width, height) / min(width, height) if min(width, height) > 0 else 999
            if aspect_ratio > 20:
                logger.warning(f"⚠️ {name}: неподдерживаемое соотношение сторон {aspect_ratio:.1f}:1, пропускаем")
                return None
            
            # Применяем изменения если нужно
            if needs_fix:
                img = img.resize((width, height), Image.Resampling.LANCZOS)
                
                # Конвертируем в RGB если нужно (для JPEG)
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=90)
                result = output.getvalue()
                logger.info(f"✅ {name}: исправлено {original_size} → ({width}x{height}), {len(result)} байт")
                return result
            
            return photo_data
            
        except Exception as e:
            logger.error(f"❌ Ошибка валидации {name}: {e}")
            return None
    
    def _send_single_photo(self, photo: Dict, date_str: str) -> bool:
        self._rate_limit()
        
        download_url = photo.get('download_url')
        if not download_url:
            logger.warning(f"⚠️ Нет URL для скачивания: {photo.get('name', 'unknown')}")
            return False
        
        # Скачиваем фото
        photo_data = self._download_photo(download_url, photo.get('name', 'unknown'))
        if not photo_data:
            return False
        
        # Валидация и исправление размеров
        photo_data = self._validate_and_fix_image(photo_data, photo.get('name', 'unknown'))
        if not photo_data:
            return False
        
        url = self.BASE_URL.format(token=self.token, method='sendPhoto')
        
        year = photo.get('year', '')
        caption = f"{year} год" if year else ""
        
        data = {
            'chat_id': self.chat_id,
            'caption': caption
        }
        files = {
            'photo': (photo.get('name', 'photo.jpg'), io.BytesIO(photo_data), 'image/jpeg')
        }
        
        try:
            response = requests.post(url, data=data, files=files, timeout=60)
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
    
    def _send_media_group(self, photos: List[Dict], include_years: bool = True) -> bool:
        self._rate_limit()
        
        url = self.BASE_URL.format(token=self.token, method='sendMediaGroup')
        
        # Скачиваем все фото
        media = []
        files = {}
        for idx, photo in enumerate(photos):
            download_url = photo.get('download_url')
            if not download_url:
                logger.warning(f"⚠️ Пропуск фото без URL: {photo.get('name', 'unknown')}")
                continue
            
            photo_data = self._download_photo(download_url, photo.get('name', 'unknown'))
            if not photo_data:
                continue
            
            # Валидация и исправление размеров
            photo_data = self._validate_and_fix_image(photo_data, photo.get('name', 'unknown'))
            if not photo_data:
                continue
            
            attach_name = f"photo_{idx}"
            year = photo.get('year', '')
            photo_caption = str(year) if include_years and year else ""
            
            media.append({
                'type': 'photo',
                'media': f'attach://{attach_name}',
                'caption': photo_caption
            })
            files[attach_name] = (photo.get('name', f'photo_{idx}.jpg'), io.BytesIO(photo_data), 'image/jpeg')
        
        if not media:
            logger.error("❌ Нет валидных фото для отправки в медиа-группе")
            return False
        
        data = {
            'chat_id': self.chat_id,
            'media': json.dumps(media)
        }
        
        try:
            response = requests.post(url, data=data, files=files, timeout=120)
            response.raise_for_status()
            logger.info(f"✅ Отправлена медиа-группа из {len(media)} фото")
            return True
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                try:
                    error_data = e.response.json()
                    retry_after = error_data.get('parameters', {}).get('retry_after', 5)
                    logger.warning(f"⚠️ Rate limit! Ожидание {retry_after} секунд...")
                    time.sleep(retry_after + 1)
                    
                    # Пересоздаём BytesIO (они уже consumed)
                    for key in files:
                        files[key][1].seek(0)
                    
                    response = requests.post(url, data=data, files=files, timeout=120)
                    response.raise_for_status()
                    logger.info(f"✅ Отправлена медиа-группа из {len(media)} фото (после retry)")
                    return True
                except Exception as retry_error:
                    logger.error(f"❌ Ошибка при retry: {retry_error}")
                    return False
            else:
                logger.error(f"❌ HTTP ошибка отправки медиа-группы: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
                return False
        except requests.exceptions.Timeout:
            logger.error(f"⏱️ Timeout при отправке медиа-группы из {len(media)} фото")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Ошибка отправки медиа-группы: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Status: {e.response.status_code}, Body: {e.response.text[:200]}")
            return False
