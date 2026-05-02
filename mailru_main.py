"""
Независимый бот для публикации фотографий из Mail.ru Cloud в Telegram.

Это отдельный entry point, не пересекающийся с Яндекс-флоу:
  • Свой workflow (.github/workflows/publish_memories_mailru.yml)
  • Свои секреты (MAILRU_LOGIN, MAILRU_PASSWORD, MAILRU_FOLDERS,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — последние два общие с Яндекс)
  • Своя логика выбора фото (проще: всё что нашли, до 12 шт. с разбиением по 10)

Если Mail.ru сломается — Яндекс-флоу продолжит работать как ни в чём не бывало.
"""
import io
import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict

import requests
from PIL import Image

from mailru_public import MailRuPublicClient
from mailru_disk import MailRuClient  # legacy fallback (auth-based)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('mailru_bot.log')
    ]
)
logger = logging.getLogger(__name__)


TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'
MAX_PHOTOS = 12          # Сколько максимум публикуем за раз
MEDIA_GROUP_LIMIT = 10   # Лимит Telegram на медиа-группу


def send_message(token: str, chat_id: str, text: str) -> bool:
    url = TELEGRAM_API.format(token=token, method='sendMessage')
    try:
        resp = requests.post(
            url,
            json={'chat_id': chat_id, 'text': text},
            timeout=30,
        )
        if resp.status_code == 429:
            retry_after = resp.json().get('parameters', {}).get('retry_after', 5)
            logger.warning(f"⚠️ Rate limit, ждём {retry_after}s")
            time.sleep(retry_after + 1)
            resp = requests.post(
                url,
                json={'chat_id': chat_id, 'text': text},
                timeout=30,
            )
        resp.raise_for_status()
        logger.info("✅ Сообщение отправлено")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка отправки сообщения: {e}")
        return False


def fix_image(data: bytes, name: str) -> bytes:
    """Чинит размеры изображения под лимиты Telegram (sum >= 100, side <= 10000, ratio <= 20:1).

    Возвращает None если фото невалидное (слишком маленькое или
    с неподходящим соотношением сторон).
    """
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size

        if width + height < 100:
            logger.warning(f"⚠️ {name}: слишком маленькое ({width}x{height})")
            return None

        aspect = max(width, height) / max(min(width, height), 1)
        if aspect > 20:
            logger.warning(f"⚠️ {name}: плохое соотношение сторон {aspect:.1f}:1")
            return None

        max_side = 10000
        if width > max_side or height > max_side:
            ratio = min(max_side / width, max_side / height)
            new_size = (int(width * ratio), int(height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=90)
            logger.info(f"📐 {name}: ужали до {new_size}")
            return out.getvalue()

        return data
    except Exception as e:
        logger.error(f"❌ Ошибка обработки изображения {name}: {e}")
        return None


def send_photo_group(token: str, chat_id: str, photos_with_bytes: List[tuple]) -> bool:
    """photos_with_bytes: список кортежей (photo_dict, bytes)"""
    url = TELEGRAM_API.format(token=token, method='sendMediaGroup')

    media = []
    files = {}
    for idx, (photo, data) in enumerate(photos_with_bytes):
        attach = f'photo_{idx}'
        year = photo.get('year', '')
        media.append({
            'type': 'photo',
            'media': f'attach://{attach}',
            'caption': str(year) if year else '',
        })
        files[attach] = (
            photo.get('name', f'photo_{idx}.jpg'),
            io.BytesIO(data),
            'image/jpeg',
        )

    try:
        resp = requests.post(
            url,
            data={'chat_id': chat_id, 'media': json.dumps(media)},
            files=files,
            timeout=120,
        )
        if resp.status_code == 429:
            retry_after = resp.json().get('parameters', {}).get('retry_after', 5)
            logger.warning(f"⚠️ Rate limit на медиа-группу, ждём {retry_after}s")
            time.sleep(retry_after + 1)
            for k in files:
                files[k][1].seek(0)
            resp = requests.post(
                url,
                data={'chat_id': chat_id, 'media': json.dumps(media)},
                files=files,
                timeout=120,
            )
        resp.raise_for_status()
        logger.info(f"✅ Отправлена медиа-группа из {len(media)} фото")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка отправки медиа-группы: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Body: {e.response.text[:200]}")
        return False


def send_single_photo(token: str, chat_id: str, photo: Dict, data: bytes) -> bool:
    url = TELEGRAM_API.format(token=token, method='sendPhoto')
    year = photo.get('year', '')
    try:
        resp = requests.post(
            url,
            data={'chat_id': chat_id, 'caption': f"{year} год" if year else ''},
            files={'photo': (photo.get('name', 'photo.jpg'),
                             io.BytesIO(data), 'image/jpeg')},
            timeout=60,
        )
        resp.raise_for_status()
        logger.info(f"✅ Отправлено: {photo.get('name')}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка отправки фото: {e}")
        return False


def main():
    try:
        public_link = os.getenv('MAILRU_PUBLIC_LINK')
        login = os.getenv('MAILRU_LOGIN')
        password = os.getenv('MAILRU_PASSWORD')
        cookies = os.getenv('MAILRU_COOKIES')
        folders_raw = os.getenv('MAILRU_FOLDERS', '/')
        tg_token = os.getenv('TELEGRAM_BOT_TOKEN')
        tg_chat = os.getenv('TELEGRAM_CHAT_ID')

        if not (public_link or cookies or (login and password)):
            logger.error("❌ Нужен MAILRU_PUBLIC_LINK (рекомендуется), либо")
            logger.error("   MAILRU_COOKIES, либо MAILRU_LOGIN+MAILRU_PASSWORD")
            sys.exit(1)
        if not all([tg_token, tg_chat]):
            logger.error("❌ TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID обязательны")
            sys.exit(1)

        # Московское время (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        today = datetime.now(moscow_tz)
        day, month = today.day, today.month
        date_str = f"{day}.{month:02d}"

        logger.info(f"🚀 Запуск Mail.ru-бота, дата: {date_str} (МСК {today:%H:%M})")

        # Приоритет: публичная ссылка > cookies > login/password
        if public_link:
            logger.info(f"🔗 Источник: публичная ссылка")
            client = MailRuPublicClient(public_link)
        else:
            folders = [f.strip() for f in folders_raw.split(',') if f.strip()]
            logger.info(f"📂 Папки для скана: {folders}")
            logger.info(f"🔐 Auth: {'cookies' if cookies else 'login/password'}")
            client = MailRuClient(
                login=login, password=password,
                cookies=cookies, scan_folders=folders,
            )
        photos = client.find_photos_by_date(day, month)

        if not photos:
            logger.info(f"📭 [Mail.ru] За {date_str} ничего не найдено")
            # Тихо выходим — не спамим в чат "ничего нет",
            # т.к. параллельно может работать Яндекс-флоу
            return

        # Отбираем не больше MAX_PHOTOS, отдавая приоритет более старым годам
        if len(photos) > MAX_PHOTOS:
            logger.info(f"📊 Найдено {len(photos)}, отбираем {MAX_PHOTOS}")
            # Группируем по годам, берём по равному количеству из каждого
            by_year = {}
            for p in photos:
                by_year.setdefault(p['year'], []).append(p)
            years = sorted(by_year.keys())
            per_year = max(1, MAX_PHOTOS // len(years))
            selected = []
            for y in years:
                selected.extend(by_year[y][:per_year])
                if len(selected) >= MAX_PHOTOS:
                    break
            photos = selected[:MAX_PHOTOS]

        # Текстовое сообщение перед фото
        send_message(tg_token, tg_chat, f"📅 {date_str} (Mail.ru архив)\n\nВспоминаем! 📸")
        time.sleep(1.5)

        # Скачиваем все фото байтами заранее
        photos_with_bytes = []
        for photo in photos:
            data = client.download_photo(photo)
            if not data:
                continue
            data = fix_image(data, photo.get('name', 'unknown'))
            if not data:
                continue
            photos_with_bytes.append((photo, data))

        if not photos_with_bytes:
            logger.error("❌ Не удалось подготовить ни одного фото")
            sys.exit(1)

        # Публикуем группами по 10
        success = True
        for i in range(0, len(photos_with_bytes), MEDIA_GROUP_LIMIT):
            chunk = photos_with_bytes[i:i + MEDIA_GROUP_LIMIT]
            if len(chunk) == 1:
                ok = send_single_photo(tg_token, tg_chat, chunk[0][0], chunk[0][1])
            else:
                ok = send_photo_group(tg_token, tg_chat, chunk)
            if not ok:
                success = False
            if i + MEDIA_GROUP_LIMIT < len(photos_with_bytes):
                time.sleep(2)

        if success:
            logger.info(f"✨ [Mail.ru] Опубликовано {len(photos_with_bytes)} фото")
        else:
            logger.warning("⚠️ [Mail.ru] Публикация с ошибками")
            sys.exit(1)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
