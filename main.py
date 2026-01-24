"""
Бот для публикации фотографий "этот день в истории" из Яндекс.Диска в Telegram
"""
import os
import sys
import logging
from datetime import datetime, timezone
from yandex_disk import YandexDiskClient
from telegram_publisher import TelegramPublisher

# Настройка логирования
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
    """Основная функция для поиска и публикации фото"""
    
    try:
        # Получаем токены из переменных окружения
        yandex_token = os.getenv('YANDEX_DISK_TOKEN')
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if not all([yandex_token, telegram_token, telegram_chat_id]):
            logger.error("❌ Не все переменные окружения установлены")
            logger.error("Требуются: YANDEX_DISK_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
            sys.exit(1)
        
        # Инициализируем клиенты
        logger.info("🚀 Инициализация клиентов...")
        yandex = YandexDiskClient(yandex_token)
        telegram = TelegramPublisher(telegram_token, telegram_chat_id)
        
        # Получаем текущую дату (день и месяц) с UTC timezone
        today = datetime.now(timezone.utc)
        target_day = today.day
        target_month = today.month
        
        logger.info(f"🔍 Ищем фото за {target_day}.{target_month:02d} из прошлых лет...")
        
        # Ищем фотографии
        photos = yandex.find_photos_by_date(target_day, target_month)
        
        if not photos:
            logger.info(f"📭 Фотографий за {target_day}.{target_month:02d} не найдено")
            
            # Отправляем сообщение в группу
            message = f"📅 {target_day}.{target_month:02d}\n\nК сожалению, на эту дату фотографий в архиве не найдено 😔"
            success = telegram.send_message(message)
            
            if not success:
                logger.error("❌ Не удалось отправить сообщение о пустом результате")
                sys.exit(1)
            
            logger.info("✅ Уведомление об отсутствии фото отправлено")
            return
        
        logger.info(f"✅ Найдено {len(photos)} фотографий")
        
        # Группируем по годам
        photos_by_year = {}
        for photo in photos:
            year = photo['year']
            if year not in photos_by_year:
                photos_by_year[year] = []
            photos_by_year[year].append(photo)
        
        logger.info(f"📊 Фото распределены по {len(photos_by_year)} годам")
        
        # Публикуем фотографии
        all_success = True
        for year in sorted(photos_by_year.keys()):
            year_photos = photos_by_year[year]
            logger.info(f"📤 Публикуем {len(year_photos)} фото за {year} год...")
            
            success = telegram.publish_photos(
                photos=year_photos,
                date_str=f"{target_day}.{target_month:02d}.{year}"
            )
            
            if not success:
                logger.error(f"❌ Ошибка при публикации фото за {year} год")
                all_success = False
            else:
                logger.info(f"✅ Фото за {year} год опубликованы")
        
        if all_success:
            logger.info("✨ Публикация успешно завершена!")
        else:
            logger.warning("⚠️ Публикация завершена с ошибками")
            sys.exit(1)
            
    except ValueError as e:
        logger.error(f"❌ Ошибка валидации данных: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        # Пытаемся отправить уведомление об ошибке в Telegram
        try:
            if 'telegram' in locals():
                telegram.send_message(f"⚠️ Ошибка в боте воспоминаний:\n\n{str(e)}")
        except:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
