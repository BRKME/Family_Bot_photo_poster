"""
Бот для публикации фотографий "этот день в истории" из Яндекс.Диска в Telegram
"""
import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from yandex_disk import YandexDiskClient
from telegram_publisher import TelegramPublisher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('memories_bot.log')
    ]
)
logger = logging.getLogger(__name__)


def main():
    try:
        yandex_token = os.getenv('YANDEX_DISK_TOKEN')
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if not all([yandex_token, telegram_token, telegram_chat_id]):
            logger.error("❌ Не все переменные окружения установлены")
            logger.error("Требуются: YANDEX_DISK_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
            sys.exit(1)
        
        logger.info("🚀 Инициализация клиентов...")
        yandex = YandexDiskClient(yandex_token)
        telegram = TelegramPublisher(telegram_token, telegram_chat_id)
        
        # Московское время (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        today = datetime.now(moscow_tz)
        target_day = today.day
        target_month = today.month
        
        logger.info(f"🕐 Московское время: {today.strftime('%Y-%m-%d %H:%M:%S')} МСК")
        logger.info(f"🔍 Ищем фото за {target_day}.{target_month:02d} из прошлых лет...")
        
        photos = yandex.find_photos_by_date(target_day, target_month)
        
        if not photos:
            logger.info(f"📭 Фотографий за {target_day}.{target_month:02d} не найдено")
            message = f"📅 {target_day}.{target_month:02d}\n\nК сожалению, на эту дату фотографий в архиве не найдено 😔"
            success = telegram.send_message(message)
            
            if not success:
                logger.error("❌ Не удалось отправить сообщение о пустом результате")
                sys.exit(1)
            
            logger.info("✅ Уведомление об отсутствии фото отправлено")
            return
        
        logger.info(f"✅ Найдено {len(photos)} фотографий")
        
        photos_by_year = {}
        for photo in photos:
            year = photo['year']
            if year not in photos_by_year:
                photos_by_year[year] = []
            photos_by_year[year].append(photo)
        
        years_count = len(photos_by_year)
        logger.info(f"📊 Фото распределены по {years_count} годам")
        
        if years_count <= 3:
            photos_per_year = 3
        elif years_count <= 5:
            photos_per_year = 2
        else:
            photos_per_year = 1
        
        selected_photos = []
        for year in sorted(photos_by_year.keys()):
            year_photos = photos_by_year[year][:photos_per_year]
            selected_photos.extend(year_photos)
            if len(selected_photos) >= 10:
                selected_photos = selected_photos[:10]
                break
        
        logger.info(f"📤 Публикуем {len(selected_photos)} фото (по {photos_per_year} из каждого года)")
        
        success = telegram.publish_photos(selected_photos, f"{target_day}.{target_month:02d}")
        
        if success:
            logger.info("✨ Публикация успешно завершена!")
        else:
            logger.warning("⚠️ Публикация завершена с ошибками")
            sys.exit(1)
            
    except ValueError as e:
        logger.error(f"❌ Ошибка валидации данных: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        try:
            if 'telegram' in locals():
                telegram.send_message(f"⚠️ Ошибка в боте воспоминаний:\n\n{str(e)}")
        except:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
