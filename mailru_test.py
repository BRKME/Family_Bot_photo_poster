"""
Тестовый скрипт для Mail.ru интеграции.

Запускается через workflow .github/workflows/test_mailru.yml вручную.
Не пересекается с production-флоу (mailru_main.py).

Режимы (env: TEST_MODE):
  • diagnose      — авторизация + листинг папок + поиск фото за дату.
                    Telegram НЕ дёргается. По умолчанию.
  • download_test — diagnose + скачивание первого найденного фото
                    (проверяет, что cookies сессии работают для download).
  • send_one      — download_test + отправка ОДНОГО фото в Telegram с
                    префиксом [ТЕСТ]. Чат можно переопределить через
                    TELEGRAM_TEST_CHAT_ID, иначе используется основной.

Параметры (env):
  • TEST_DATE              — DD.MM, пусто = сегодня
  • MAILRU_FOLDERS_OVERRIDE — переопределить папки для одного теста
"""
import io
import os
import sys
import logging
from datetime import datetime, timezone, timedelta

import requests

from mailru_disk import MailRuClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('mailru_test.log')
    ]
)
logger = logging.getLogger(__name__)


def parse_test_date(test_date: str):
    """Парсит DD.MM или DD.MM.YYYY. Возвращает (day, month). Пусто = сегодня (МСК)."""
    if not test_date or not test_date.strip():
        moscow_tz = timezone(timedelta(hours=3))
        today = datetime.now(moscow_tz)
        return today.day, today.month, "сегодня (МСК)"

    parts = test_date.strip().split('.')
    if len(parts) < 2:
        raise ValueError(f"TEST_DATE должен быть в формате DD.MM, получено: {test_date}")
    day = int(parts[0])
    month = int(parts[1])
    if not (1 <= day <= 31 and 1 <= month <= 12):
        raise ValueError(f"Некорректная дата: {day}.{month:02d}")
    return day, month, f"{day}.{month:02d}"


def list_folder_preview(client: MailRuClient, folder: str, limit: int = 10):
    """Печатает первые N элементов папки — чтобы убедиться, что путь правильный."""
    if not client._authenticated:
        client._authenticate()
    try:
        resp = client.session.get(
            f'{client.API_URL}/folder',
            params={
                'token': client.csrf_token,
                'home': folder,
                'limit': limit,
                'offset': 0,
            },
            timeout=client.REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            logger.error(f"❌ Папка не существует: {folder}")
            return False
        if resp.status_code == 403:
            logger.error(f"❌ Нет доступа к: {folder}")
            return False
        resp.raise_for_status()
        body = resp.json().get('body', {})
        items = body.get('list', [])
        total = body.get('count', {})

        logger.info(f"📂 {folder}: всего элементов = {total}")
        if not items:
            logger.warning(f"⚠️ {folder}: папка пустая")
            return True

        logger.info(f"   Первые {len(items)}:")
        for item in items:
            kind = item.get('type', '?')
            icon = '📁' if kind == 'folder' else '📄'
            size = item.get('size', 0)
            name = item.get('name', '')
            mtime = item.get('mtime', 0)
            try:
                date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
            except Exception:
                date_str = '?'
            logger.info(f"   {icon} {name} ({size} байт, mtime={date_str})")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка листинга {folder}: {e}")
        return False


def send_test_photo(token: str, chat_id: str, photo: dict, data: bytes) -> bool:
    """Отправляет одно тестовое фото с префиксом [ТЕСТ]."""
    url = f'https://api.telegram.org/bot{token}/sendPhoto'
    year = photo.get('year', '')
    caption = (
        f"[ТЕСТ Mail.ru]\n"
        f"📅 {photo.get('date').strftime('%d.%m.%Y') if photo.get('date') else 'неизвестно'}\n"
        f"📁 {photo.get('path', '')}\n"
        f"📷 {photo.get('name', '')}"
        + (f"\n📆 {year} год" if year else '')
    )
    try:
        resp = requests.post(
            url,
            data={'chat_id': chat_id, 'caption': caption[:1024]},
            files={'photo': (photo.get('name', 'test.jpg'),
                             io.BytesIO(data), 'image/jpeg')},
            timeout=60,
        )
        resp.raise_for_status()
        logger.info(f"✅ Тестовое фото отправлено в чат {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка отправки в Telegram: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"   Body: {e.response.text[:300]}")
        return False


def main():
    print("=" * 70)
    print("🧪 ТЕСТ Mail.ru Cloud интеграции")
    print("=" * 70)

    # --- Чтение конфига ---
    mode = os.getenv('TEST_MODE', 'diagnose').strip().lower()
    if mode not in ('diagnose', 'download_test', 'send_one'):
        logger.error(f"❌ Неизвестный TEST_MODE: {mode}")
        sys.exit(1)

    login = os.getenv('MAILRU_LOGIN')
    password = os.getenv('MAILRU_PASSWORD')
    cookies = os.getenv('MAILRU_COOKIES')
    folders_override = os.getenv('MAILRU_FOLDERS_OVERRIDE', '').strip()
    folders_default = os.getenv('MAILRU_FOLDERS', '/').strip()
    folders_raw = folders_override if folders_override else folders_default

    test_date_raw = os.getenv('TEST_DATE', '').strip()
    tg_token = os.getenv('TELEGRAM_BOT_TOKEN')
    tg_chat = os.getenv('TELEGRAM_TEST_CHAT_ID') or os.getenv('TELEGRAM_CHAT_ID')

    if not cookies and not (login and password):
        logger.error("❌ Нужны либо MAILRU_COOKIES (рекомендуется),")
        logger.error("   либо MAILRU_LOGIN + MAILRU_PASSWORD")
        sys.exit(1)

    folders = [f.strip() for f in folders_raw.split(',') if f.strip()]
    day, month, date_label = parse_test_date(test_date_raw)

    logger.info(f"🔧 Режим: {mode}")
    logger.info(f"🔧 Дата: {date_label}")
    logger.info(f"🔧 Папки: {folders}")
    logger.info(f"🔧 Auth: {'cookies' if cookies else 'login/password'}")
    if mode == 'send_one':
        logger.info(f"🔧 Telegram чат: {tg_chat}")

    # --- Шаг 1: авторизация ---
    print()
    print("─" * 70)
    print("Шаг 1/4: Авторизация в Mail.ru")
    print("─" * 70)
    try:
        client = MailRuClient(
            login=login, password=password,
            cookies=cookies, scan_folders=folders,
        )
        client._authenticate()
        logger.info("✅ Авторизация прошла успешно")
    except Exception as e:
        logger.error(f"❌ Авторизация не удалась: {e}")
        if cookies:
            logger.error("   Проверь MAILRU_COOKIES — возможно протухли,")
            logger.error("   зайди в cloud.mail.ru в браузере и обнови секрет.")
        else:
            logger.error("   Проверь:")
            logger.error("   • MAILRU_LOGIN — полный email (например, family@mail.ru)")
            logger.error("   • MAILRU_PASSWORD — пароль для приложений, не основной")
            logger.error("   • Включена ли 2FA — без app password не залогинишься")
        sys.exit(1)

    # --- Шаг 2: листинг папок ---
    print()
    print("─" * 70)
    print("Шаг 2/4: Листинг настроенных папок")
    print("─" * 70)
    all_ok = True
    for folder in folders:
        if not list_folder_preview(client, folder):
            all_ok = False
        print()
    if not all_ok:
        logger.error("❌ Не все папки доступны — проверь MAILRU_FOLDERS")
        sys.exit(1)

    # --- Шаг 3: поиск фото ---
    print()
    print("─" * 70)
    print(f"Шаг 3/4: Поиск фото за {date_label}")
    print("─" * 70)
    try:
        photos = client.find_photos_by_date(day, month)
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}", exc_info=True)
        sys.exit(1)

    if not photos:
        logger.warning(f"⚠️ За {date_label} ничего не найдено")
        logger.warning("   Это может быть нормально (просто нет фото на эту дату),")
        logger.warning("   попробуй другую дату через TEST_DATE — например, день рождения.")
        # Авторизация и скан работают, выходим успешно
        print()
        print("=" * 70)
        print("✅ Mail.ru подключение работает (фото за эту дату нет)")
        print("=" * 70)
        return

    logger.info(f"✅ Найдено {len(photos)} фото:")
    for p in photos[:20]:  # первые 20
        logger.info(f"   📷 {p['date'].strftime('%Y-%m-%d')} | "
                    f"{p['size']:>8} байт | {p['path']}")
    if len(photos) > 20:
        logger.info(f"   ... и ещё {len(photos) - 20}")

    if mode == 'diagnose':
        print()
        print("=" * 70)
        print(f"✅ Диагностика завершена. Найдено {len(photos)} фото.")
        print("   Чтобы протестировать скачивание — TEST_MODE=download_test")
        print("   Чтобы отправить тестовое фото в Telegram — TEST_MODE=send_one")
        print("=" * 70)
        return

    # --- Шаг 4: скачивание первого фото ---
    print()
    print("─" * 70)
    print("Шаг 4/4: Скачивание первого фото")
    print("─" * 70)
    test_photo = photos[0]
    logger.info(f"📥 Качаем: {test_photo['name']}")
    data = client.download_photo(test_photo)
    if not data:
        logger.error("❌ Скачивание не удалось — см. логи выше")
        sys.exit(1)
    logger.info(f"✅ Скачано {len(data)} байт")

    if mode == 'download_test':
        print()
        print("=" * 70)
        print(f"✅ Скачивание работает. Файл размером {len(data)} байт.")
        print("   Чтобы отправить тестовое фото в Telegram — TEST_MODE=send_one")
        print("=" * 70)
        return

    # --- send_one: отправка в Telegram ---
    if not tg_token or not tg_chat:
        logger.error("❌ Для TEST_MODE=send_one нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
        sys.exit(1)

    print()
    print("─" * 70)
    print("Шаг 5: Отправка тестового фото в Telegram")
    print("─" * 70)
    if not send_test_photo(tg_token, tg_chat, test_photo, data):
        sys.exit(1)

    print()
    print("=" * 70)
    print("🎉 Полный цикл работает: Mail.ru → скачивание → Telegram")
    print("=" * 70)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("⚠️ Прервано пользователем")
        sys.exit(130)
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка: {e}", exc_info=True)
        sys.exit(1)
