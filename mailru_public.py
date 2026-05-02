"""
Клиент для публичных папок Mail.ru Cloud.

Использует API публичных ссылок (cloud.mail.ru/public/...), который НЕ требует
авторизации, cookies, CSRF и не привязан к IP. В отличие от login/password или
cookie-auth подходов, этот путь стабилен и работает с любого IP, включая
GitHub Actions runners.

Принцип:
1. Пользователь расшаривает папку на Mail.ru → получает ссылку вида
   https://cloud.mail.ru/public/7192/RDJK5axoi
2. Из ссылки извлекаем weblink (`7192/RDJK5axoi`)
3. Дальше через /api/v2/folder?weblink=... листаем содержимое
4. Скачиваем через /api/v2/dispatcher + weblink файла

Ссылка по сути работает как «секретный URL»: длина ~12 символов случайной
строки, в поисковиках не индексируется, никто кроме знающих URL не попадёт.

Поддерживается несколько публичных ссылок (через запятую) — бот соберёт
фото со всех.
"""
import re
import time
import logging
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


class MailRuPublicClient:
    BASE_API = 'https://cloud.mail.ru/api/v2'
    PUBLIC_PREFIX = 'https://cloud.mail.ru/public/'
    REQUEST_TIMEOUT = 30
    PAGE_LIMIT = 500
    SCAN_RATE_LIMIT = 0.2  # сек между запросами листинга

    PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif', '.bmp')
    VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v', '.3gp')

    def __init__(self, public_links):
        """
        public_links: одна публичная ссылка (str) или список ссылок (list[str]).
        Также принимает строку с ссылками через запятую.
        """
        if isinstance(public_links, str):
            # Может быть одна ссылка или несколько через запятую
            links = [s.strip() for s in public_links.split(',') if s.strip()]
        else:
            links = list(public_links)

        if not links:
            raise ValueError("Нужна хотя бы одна публичная ссылка Mail.ru")

        self.weblinks: List[str] = []
        for link in links:
            wl = self._extract_weblink(link)
            self.weblinks.append(wl)
            logger.info(f"✅ Публичная ссылка добавлена (weblink: {wl[:20]}...)")

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/121.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
            'Origin': 'https://cloud.mail.ru',
            'Referer': 'https://cloud.mail.ru/',
            'X-Requested-With': 'XMLHttpRequest',
        })

        self.dispatcher_url: Optional[str] = None

    @staticmethod
    def _extract_weblink(link: str) -> str:
        """
        Из 'https://cloud.mail.ru/public/7192/RDJK5axoi' → '7192/RDJK5axoi'.
        Поддерживает варианты с или без https://, с/без trailing slash, с query.
        """
        # Убираем протокол и домен, ищем после /public/
        m = re.search(r'/public/([^?#]+?)/?(?:[?#]|$)', link)
        if not m:
            raise ValueError(
                f"Не похоже на публичную ссылку Mail.ru: {link}\n"
                f"Ожидаемый формат: https://cloud.mail.ru/public/XXXX/YYYY"
            )
        weblink = m.group(1)
        # Sanity-check: weblink Mail.ru обычно вида XXXX/YYYY (две части)
        if '/' not in weblink:
            logger.warning(f"⚠️ Подозрительный weblink (нет /): {weblink}")
        return weblink

    def _ensure_dispatcher(self) -> None:
        """Получает URL CDN-сервера для скачивания. Кешируется в self.

        Для публичных файлов Mail.ru использует специальный ключ `weblink_get`
        (URL вида `/public/...`), а не общий `get` (URL вида `/attach/...`,
        который для авторизованных файлов пользователя).
        """
        if self.dispatcher_url:
            return
        try:
            resp = self.session.get(
                f'{self.BASE_API}/dispatcher',
                timeout=self.REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json().get('body', {})
        except (requests.exceptions.RequestException, ValueError) as e:
            raise RuntimeError(f"Не удалось получить dispatcher: {e}")

        logger.debug(f"   Dispatcher response keys: {list(data.keys())}")

        # Приоритет: weblink_get (для публичных) → get (legacy fallback)
        for key in ('weblink_get', 'get'):
            urls = data.get(key, [])
            if urls and urls[0].get('url'):
                self.dispatcher_url = urls[0]['url']
                logger.info(f"   Dispatcher ({key}): {self.dispatcher_url[:50]}...")
                return

        raise RuntimeError(
            f"Dispatcher не вернул ни weblink_get, ни get URL. "
            f"Доступные ключи: {list(data.keys())}"
        )

    def find_photos_by_date(self, day: int, month: int) -> List[Dict]:
        if not 1 <= day <= 31:
            raise ValueError(f"День должен быть 1-31, получено: {day}")
        if not 1 <= month <= 12:
            raise ValueError(f"Месяц должен быть 1-12, получено: {month}")

        logger.info(f"🔍 [Mail.ru public] Начинаем поиск фото за {day}.{month:02d}")

        all_photos: List[Dict] = []
        for weblink in self.weblinks:
            try:
                photos = self._scan_public_folder(weblink, day, month)
                all_photos.extend(photos)
            except Exception as e:
                logger.error(f"❌ [Mail.ru public] Ошибка обхода {weblink}: {e}")

        # Дедуп по weblink файла (на случай если одна папка попала через две ссылки)
        unique = {p['weblink']: p for p in all_photos if p.get('weblink')}
        result = list(unique.values())
        result.sort(key=lambda x: x['year'])

        logger.info(f"✅ [Mail.ru public] Итого найдено {len(result)} фото за {day}.{month:02d}")
        return result

    def _scan_public_folder(self, weblink: str, day: int, month: int,
                            depth: int = 0, max_depth: int = 10) -> List[Dict]:
        if depth > max_depth:
            logger.warning(f"⚠️ Достигнута макс. глубина рекурсии на {weblink}")
            return []

        photos: List[Dict] = []
        offset = 0
        total = 0

        while True:
            try:
                resp = self.session.get(
                    f'{self.BASE_API}/folder',
                    params={
                        'weblink': weblink,
                        'offset': offset,
                        'limit': self.PAGE_LIMIT,
                        'sort': '{"type":"name","order":"asc"}',
                    },
                    timeout=self.REQUEST_TIMEOUT,
                )

                if resp.status_code == 404:
                    logger.error(f"❌ Публичная папка не найдена: {weblink}")
                    logger.error(f"   Ссылка отозвана или некорректная")
                    return photos
                if resp.status_code in (401, 403):
                    logger.error(f"❌ {resp.status_code} на {weblink}: {resp.text[:200]}")
                    return photos
                resp.raise_for_status()

                body = resp.json().get('body', {})
                items = body.get('list', [])
                if not items:
                    if total == 0:
                        logger.info(f"📂 [Mail.ru public] {weblink}: папка пустая")
                    break

                total += len(items)
                logger.info(f"📊 [Mail.ru public] {weblink}: обработано {total}...")

                for item in items:
                    item_type = item.get('type') or item.get('kind')
                    item_weblink = item.get('weblink', '')
                    item_name = item.get('name', '')
                    item_home = item.get('home', '')

                    if item_type == 'folder':
                        # Рекурсивно в подпапку
                        if item_weblink:
                            try:
                                photos.extend(self._scan_public_folder(
                                    item_weblink, day, month,
                                    depth=depth + 1, max_depth=max_depth,
                                ))
                            except Exception as e:
                                logger.error(f"❌ Не удалось войти в подпапку {item_weblink}: {e}")
                        continue

                    name_lower = item_name.lower()
                    if name_lower.endswith(self.VIDEO_EXTENSIONS):
                        continue
                    if not name_lower.endswith(self.PHOTO_EXTENSIONS):
                        continue

                    photo_date = self._extract_date(item)
                    if not photo_date:
                        continue

                    if photo_date.day == day and photo_date.month == month:
                        photos.append({
                            'name': item_name,
                            'path': item_home or item_name,
                            'weblink': item_weblink,
                            'date': photo_date,
                            'year': photo_date.year,
                            'size': item.get('size', 0),
                            'created': item.get('mtime'),
                            'modified': item.get('mtime'),
                        })
                        logger.info(
                            f"✅ [Mail.ru public] {item_name} → "
                            f"{photo_date.strftime('%Y-%m-%d')}"
                        )

                if len(items) < self.PAGE_LIMIT:
                    break
                offset += self.PAGE_LIMIT
                time.sleep(self.SCAN_RATE_LIMIT)

            except requests.exceptions.Timeout:
                logger.error(f"⏱️ Timeout на {weblink} (offset={offset})")
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ Сетевая ошибка на {weblink}: {e}")
                break
            except (ValueError, KeyError) as e:
                logger.error(f"❌ Ошибка парсинга ответа {weblink}: {e}")
                break

        return photos

    def download_photo(self, photo: Dict) -> Optional[bytes]:
        """Скачивает фото через dispatcher. Cookies/auth не нужны.

        Если dispatcher URL не сработал — пробуем прямой URL
        cloud.mail.ru/public/<weblink>?download=1 (тот, что использует
        кнопка «Скачать» на сайте).
        """
        weblink = photo.get('weblink')
        name = photo.get('name', 'unknown')
        if not weblink:
            logger.warning(f"⚠️ Нет weblink для {name}")
            return None

        # Стратегия 1: dispatcher URL (быстрее, через CDN)
        try:
            self._ensure_dispatcher()
            url1 = f"{self.dispatcher_url}{quote(weblink, safe='/')}"
            data = self._try_download(url1, name, strategy='dispatcher')
            if data:
                return data
        except RuntimeError as e:
            logger.warning(f"⚠️ Dispatcher не сработал: {e}")

        # Стратегия 2: прямая ссылка через cloud.mail.ru/public/...
        # Это URL который Mail.ru использует когда нажимаешь «Скачать»
        # в браузере. Медленнее (через основной домен), но надёжнее.
        url2 = f"https://cloud.mail.ru/public/{quote(weblink, safe='/')}"
        logger.info(f"🔄 Пробуем прямую ссылку для {name}")
        data = self._try_download(url2, name, strategy='direct',
                                  follow_redirects=True)
        if data:
            return data

        logger.error(f"❌ Не удалось скачать {name} ни одной из стратегий")
        return None

    def _try_download(self, url: str, name: str, strategy: str,
                      follow_redirects: bool = True) -> Optional[bytes]:
        """Пытается скачать по URL. Возвращает None если не получилось."""
        try:
            resp = self.session.get(
                url, timeout=60, stream=True,
                allow_redirects=follow_redirects,
            )

            if resp.status_code != 200:
                # Подробная диагностика для 403/404 — что именно сказал Mail.ru
                logger.warning(
                    f"⚠️ {strategy} вернул {resp.status_code} для {name}"
                )
                logger.debug(f"   URL: {url[:120]}")
                logger.debug(f"   Headers: Server={resp.headers.get('Server')}, "
                             f"Content-Type={resp.headers.get('Content-Type')}")
                body_preview = resp.text[:200] if resp.text else '(пусто)'
                logger.debug(f"   Body: {body_preview}")
                return None

            data = resp.content
            if len(data) < 100:
                logger.warning(f"⚠️ Слишком маленький файл ({len(data)} байт): {name}")
                return None
            if data[:5] in (b'<!DOC', b'<html', b'<HTML'):
                logger.warning(
                    f"⚠️ {strategy} вернул HTML вместо изображения для {name}"
                )
                return None
            logger.info(f"📥 {strategy}: скачано {len(data)} байт для {name}")
            return data

        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️ {strategy} упал: {e}")
            return None

    # ─── Парсинг даты — переиспользует логику с auth-based клиентом ───────

    def _extract_date(self, item: Dict) -> Optional[datetime]:
        """Дата из имени → пути → mtime. EXIF Mail.ru не отдаёт через API."""
        name = item.get('name', '')
        date_from_name = self._extract_date_from_filename(name)
        if date_from_name:
            return date_from_name

        path = item.get('home', '') or name
        date_from_path = self._extract_date_from_path(path)
        if date_from_path:
            return date_from_path

        mtime = item.get('mtime')
        if mtime:
            try:
                return datetime.fromtimestamp(int(mtime))
            except (ValueError, TypeError, OSError):
                pass

        return None

    @staticmethod
    def _extract_date_from_path(path: str) -> Optional[datetime]:
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

    @staticmethod
    def _extract_date_from_filename(filename: str) -> Optional[datetime]:
        patterns = [
            r'\b(\d{4})-(\d{2})-(\d{2})\b',     # 2024-01-15
            r'\b(\d{4})(\d{2})(\d{2})\b',       # 20240115
            r'\b(\d{2})\.(\d{2})\.(\d{4})\b',   # 15.01.2024
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
