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
        yandex_token_2 = os.getenv('YANDEX_DISK_TOKEN_2')  # Второй Яндекс.Диск (опционально)
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        
        if not all([yandex_token, telegram_token, telegram_chat_id]):
            logger.error("❌ Не все обязательные переменные окружения установлены")
            logger.error("Требуются: YANDEX_DISK_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
            sys.exit(1)
        
        logger.info("🚀 Инициализация клиентов...")
        yandex = YandexDiskClient(yandex_token)
        telegram = TelegramPublisher(telegram_token, telegram_chat_id)
        
        # Второй Яндекс.Диск (если токен указан)
        yandex_2 = None
        if yandex_token_2:
            logger.info("📂 Инициализирован второй Яндекс.Диск")
            yandex_2 = YandexDiskClient(yandex_token_2)
        
        # Московское время (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        today = datetime.now(moscow_tz)
        target_day = today.day
        target_month = today.month
        
        logger.info(f"🕐 Московское время: {today.strftime('%Y-%m-%d %H:%M:%S')} МСК")
        logger.info(f"🔍 Ищем фото за {target_day}.{target_month:02d} из прошлых лет...")
        
        # Поиск в первом Яндекс.Диске
        photos = yandex.find_photos_by_date(target_day, target_month)
        
        # Помечаем источник
        for photo in photos:
            photo['source'] = 'disk_1'
        
        # Поиск во втором Яндекс.Диске (если есть)
        if yandex_2:
            logger.info("🔍 Ищем фото во втором Яндекс.Диске...")
            photos_2 = yandex_2.find_photos_by_date(target_day, target_month)
            
            # Помечаем источник
            for photo in photos_2:
                photo['source'] = 'disk_2'
            
            # Объединяем результаты
            if photos_2:
                logger.info(f"✅ Второй Яндекс.Диск: найдено {len(photos_2)} фото")
                photos.extend(photos_2)
                
                # Удаляем дубликаты по имени файла
                seen_names = set()
                unique_photos = []
                for photo in photos:
                    name = photo.get('name', '')
                    if name not in seen_names:
                        seen_names.add(name)
                        unique_photos.append(photo)
                
                duplicate_count = len(photos) - len(unique_photos)
                if duplicate_count > 0:
                    logger.info(f"🔄 Удалено {duplicate_count} дубликатов")
                
                photos = unique_photos
                
                # Пересортировка по годам после объединения
                photos.sort(key=lambda x: x['year'])
        
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
        
        # Адаптивная логика под лимит 12 фото
        if years_count <= 4:
            photos_per_year = 3  # 4 года * 3 = 12 фото
        elif years_count <= 6:
            photos_per_year = 2  # 6 лет * 2 = 12 фото
        else:
            photos_per_year = 1  # 7+ лет * 1 = 12+ фото
        
        selected_photos = []
        for year in sorted(photos_by_year.keys()):
            year_photos = photos_by_year[year]
            
            # Разделяем по источникам для равномерного выбора
            disk1_photos = [p for p in year_photos if p.get('source') == 'disk_1']
            disk2_photos = [p for p in year_photos if p.get('source') == 'disk_2']
            
            # Чередуем источники
            selected_from_year = []
            d1_idx, d2_idx = 0, 0
            
            for i in range(photos_per_year):
                # Чередуем: disk_1, disk_2, disk_1, disk_2...
                if i % 2 == 0:
                    if d1_idx < len(disk1_photos):
                        selected_from_year.append(disk1_photos[d1_idx])
                        d1_idx += 1
                    elif d2_idx < len(disk2_photos):
                        selected_from_year.append(disk2_photos[d2_idx])
                        d2_idx += 1
                else:
                    if d2_idx < len(disk2_photos):
                        selected_from_year.append(disk2_photos[d2_idx])
                        d2_idx += 1
                    elif d1_idx < len(disk1_photos):
                        selected_from_year.append(disk1_photos[d1_idx])
                        d1_idx += 1
            
            selected_photos.extend(selected_from_year)
            if len(selected_photos) >= 12:
                selected_photos = selected_photos[:12]
                break
        
        logger.info(f"📤 Публикуем {len(selected_photos)} фото (по {photos_per_year} из каждого года)")
        
        # Статистика по источникам
        disk1_count = sum(1 for p in selected_photos if p.get('source') == 'disk_1')
        disk2_count = sum(1 for p in selected_photos if p.get('source') == 'disk_2')
        if disk2_count > 0:
            logger.info(f"📊 Источники: Диск 1 = {disk1_count} фото, Диск 2 = {disk2_count} фото")
        
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
