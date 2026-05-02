"""
Модуль для работы с Облаком Mail.ru через неофициальный API.

ВАЖНО: У Mail.ru нет публичного OAuth API (в отличие от Яндекса).
Мы эмулируем веб-логин через login/password и работаем с тем же API,
что использует cloud.mail.ru в браузере.

Авторизация:
1. POST на auth.mail.ru/cgi-bin/auth → получаем cookies сессии (Mpop, sdcs)
2. GET CSRF токена с api/v2/tokens/csrf
3. GET dispatcher URL для скачивания файлов

Если на аккаунте включена 2FA — нужен пароль для внешних приложений:
https://account.mail.ru/user/2-step-auth/passwords/

Ограничения по сравнению с Яндекс.Диском:
- Нет EXIF в API → дату определяем по имени/пути/mtime
- Скачивание требует cookies сессии (URL не публичный)
- Нет endpoint'а "все картинки" → рекурсивный обход папок
"""
import re
import time
import logging
from datetime import datetime
from typing import List, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class MailRuClient:
    AUTH_URL = 'https://auth.mail.ru/cgi-bin/auth'
    CLOUD_URL = 'https://cloud.mail.ru'
    API_URL = 'https://cloud.mail.ru/api/v2'
    REQUEST_TIMEOUT = 30
    PAGE_LIMIT = 500
    SCAN_RATE_LIMIT = 0.2  # сек между запросами листинга

    # Маппинг доменов login для поля Domain в форме авторизации
    DOMAIN_MAP = {
        'mail.ru': 'mail.ru',
        'inbox.ru': 'inbox.ru',
        'bk.ru': 'bk.ru',
        'list.ru': 'list.ru',
        'internet.ru': 'internet.ru',
    }

    PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif', '.bmp')
    VIDEO_EXTENSIONS = ('.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.m4v', '.3gp')

    def __init__(self, login: str, password: str, scan_folders: Optional[List[str]] = None):
        if not login or '@' not in login:
            raise ValueError("Логин Mail.ru должен быть в формате email")
        if not password or len(password) < 4:
            raise ValueError("Некорректный пароль Mail.ru")

        self.login = login
        self.password = password
        # По умолчанию сканируем корень. Можно передать список папок,
        # чтобы ограничить сканирование (например, ['/Семейные фото', '/2024'])
        self.scan_folders = scan_folders or ['/']

        domain = login.split('@')[1].lower()
        self.domain = self.DOMAIN_MAP.get(domain, 'mail.ru')

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/121.0.0.0 Safari/537.36'
            )
        })

        self.csrf_token: Optional[str] = None
        self.dispatcher_url: Optional[str] = None
        self._authenticated = False

        masked = f"{login[:3]}***{login[login.index('@'):]}"
        logger.info(f"✅ MailRuClient инициализирован (логин: {masked}, домен: {self.domain})")

    def _authenticate(self) -> None:
        """Логинимся в Mail.ru, получаем CSRF и dispatcher URL."""
        logger.info("🔐 Авторизация в Mail.ru...")

        # ВАЖНО: форма Mail.ru ожидает Login БЕЗ @-части, а Domain отдельно.
        # Если передать full_email как Login — Mail.ru склеит "user@mail.ru@mail.ru"
        # и логин не пройдёт.
        local_part = self.login.split('@')[0]

        # 1. Логин — получаем cookies сессии
        try:
            resp = self.session.post(
                self.AUTH_URL,
                data={
                    'Login': local_part,
                    'Password': self.password,
                    'Domain': self.domain,
                    'page': f'{self.CLOUD_URL}/?from=promo',
                    'new_auth_form': '1',
                    'saveauth': '1',
                },
                timeout=self.REQUEST_TIMEOUT,
                allow_redirects=True,
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Сетевая ошибка при логине Mail.ru: {e}")

        if 'Mpop' not in self.session.cookies:
            # Подробная диагностика, чтобы понять что не так
            logger.error("─" * 60)
            logger.error("🔍 Диагностика провала логина:")
            logger.error(f"   HTTP статус: {resp.status_code}")
            logger.error(f"   Финальный URL: {resp.url}")
            logger.error(f"   История редиректов: {[r.url for r in resp.history]}")
            cookies_got = list(self.session.cookies.keys())
            logger.error(f"   Полученные cookies: {cookies_got or '(нет)'}")

            # Ищем подсказки в URL и теле ответа
            url_lower = resp.url.lower()
            body_snippet = resp.text[:500] if resp.text else ''

            hints = []
            if 'fail' in url_lower or 'error' in url_lower:
                hints.append("URL содержит 'fail/error' — Mail.ru явно вернул ошибку логина")
            if 'captcha' in url_lower or 'captcha' in body_snippet.lower():
                hints.append("Mail.ru требует капчу — нужно зайти в браузере один раз")
            if '2-step' in url_lower or 'twofa' in url_lower or 'sms' in url_lower:
                hints.append("Mail.ru запросил 2FA — обычный пароль не работает, нужен app password")
            if 'invalid' in body_snippet.lower():
                hints.append("В теле ответа есть 'invalid' — вероятно неверный пароль")

            if hints:
                logger.error("   Возможные причины:")
                for h in hints:
                    logger.error(f"      • {h}")
            logger.error("─" * 60)

            raise RuntimeError(
                "❌ Не получен Mpop cookie. Самые частые причины:\n"
                "  • App password Mail.ru работает для IMAP/SMTP, но НЕ для cloud.mail.ru.\n"
                "    Если 2FA включена и app password не пускает — нужен fallback через\n"
                "    cookies из браузера (см. README раздел 'Cookie auth')\n"
                "  • Mail.ru может требовать капчу при первом входе с GitHub Actions IP —\n"
                "    зайди один раз в браузере с того же аккаунта, потом попробуй снова\n"
                "  • Неверный логин/пароль\n"
                "  Подробная диагностика выше в логах ↑"
            )

        # 2. Получаем SDCS cookie (нужно для cloud.mail.ru)
        try:
            self.session.get(
                f'{self.CLOUD_URL}/?from=promo',
                timeout=self.REQUEST_TIMEOUT,
                allow_redirects=True,
            )
        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️ Не удалось получить SDCS cookie: {e}")

        # 3. CSRF токен
        try:
            csrf_resp = self.session.get(
                f'{self.API_URL}/tokens/csrf',
                timeout=self.REQUEST_TIMEOUT,
            )
            csrf_resp.raise_for_status()
            self.csrf_token = csrf_resp.json().get('body', {}).get('token')
        except (requests.exceptions.RequestException, ValueError) as e:
            raise RuntimeError(f"Не удалось получить CSRF токен: {e}")

        if not self.csrf_token:
            raise RuntimeError("CSRF токен пустой — проверь авторизацию")

        # 4. Dispatcher URL для скачивания
        try:
            disp_resp = self.session.get(
                f'{self.API_URL}/dispatcher',
                params={'token': self.csrf_token},
                timeout=self.REQUEST_TIMEOUT,
            )
            disp_resp.raise_for_status()
            disp_data = disp_resp.json().get('body', {})
            get_urls = disp_data.get('get', [])
            if not get_urls:
                raise RuntimeError("Dispatcher не вернул URL для скачивания")
            self.dispatcher_url = get_urls[0]['url']
        except (requests.exceptions.RequestException, ValueError, KeyError, IndexError) as e:
            raise RuntimeError(f"Не удалось получить dispatcher URL: {e}")

        self._authenticated = True
        logger.info(f"✅ Авторизация в Mail.ru успешна (dispatcher: {self.dispatcher_url[:30]}...)")

    def find_photos_by_date(self, day: int, month: int) -> List[Dict]:
        if not 1 <= day <= 31:
            raise ValueError(f"День должен быть 1-31, получено: {day}")
        if not 1 <= month <= 12:
            raise ValueError(f"Месяц должен быть 1-12, получено: {month}")

        if not self._authenticated:
            self._authenticate()

        logger.info(f"🔍 [Mail.ru] Начинаем поиск фото за {day}.{month:02d}")

        all_photos: List[Dict] = []
        for folder in self.scan_folders:
            try:
                photos = self._scan_folder(folder, day, month, recursive=True)
                all_photos.extend(photos)
            except Exception as e:
                logger.error(f"❌ [Mail.ru] Ошибка сканирования {folder}: {e}")

        # Дедуп по path
        unique = {p['path']: p for p in all_photos}
        result = list(unique.values())
        result.sort(key=lambda x: x['year'])

        logger.info(f"✅ [Mail.ru] Итого найдено {len(result)} уникальных фото за {day}.{month:02d}")
        return result

    def _scan_folder(self, path: str, day: int, month: int, recursive: bool = True,
                     depth: int = 0, max_depth: int = 10) -> List[Dict]:
        if depth > max_depth:
            logger.warning(f"⚠️ [Mail.ru] Достигнута макс. глубина рекурсии на {path}")
            return []

        photos: List[Dict] = []
        offset = 0
        total = 0

        while True:
            try:
                resp = self.session.get(
                    f'{self.API_URL}/folder',
                    params={
                        'token': self.csrf_token,
                        'home': path,
                        'offset': offset,
                        'limit': self.PAGE_LIMIT,
                        'sort': '{"type":"name","order":"asc"}',
                    },
                    timeout=self.REQUEST_TIMEOUT,
                )

                if resp.status_code == 404:
                    logger.warning(f"⚠️ [Mail.ru] Папка не найдена: {path}")
                    return photos
                if resp.status_code == 403:
                    logger.warning(f"⚠️ [Mail.ru] Нет доступа к: {path}")
                    return photos
                resp.raise_for_status()

                body = resp.json().get('body', {})
                items = body.get('list', [])
                if not items:
                    break

                total += len(items)
                logger.info(f"📊 [Mail.ru] {path}: обработано {total} элементов...")

                for item in items:
                    item_type = item.get('type') or item.get('kind')
                    item_home = item.get('home', '')
                    item_name = item.get('name', '')

                    if item_type == 'folder':
                        if recursive:
                            try:
                                photos.extend(
                                    self._scan_folder(
                                        item_home, day, month,
                                        recursive=True,
                                        depth=depth + 1,
                                        max_depth=max_depth,
                                    )
                                )
                            except Exception as e:
                                logger.error(f"❌ [Mail.ru] Не удалось войти в {item_home}: {e}")
                        continue

                    # Только файлы дальше
                    name_lower = item_name.lower()

                    if name_lower.endswith(self.VIDEO_EXTENSIONS):
                        continue
                    if not name_lower.endswith(self.PHOTO_EXTENSIONS):
                        continue

                    photo_date = self._extract_date(item)
                    if not photo_date:
                        continue

                    if photo_date.day == day and photo_date.month == month:
                        download_url = f"{self.dispatcher_url}{item_home}"
                        photos.append({
                            'name': item_name,
                            'path': item_home,
                            'download_url': download_url,
                            'created': item.get('mtime'),
                            'modified': item.get('mtime'),
                            'date': photo_date,
                            'year': photo_date.year,
                            'size': item.get('size', 0),
                        })
                        logger.info(f"✅ [Mail.ru] {item_name} → {photo_date.strftime('%Y-%m-%d')}")

                if len(items) < self.PAGE_LIMIT:
                    break
                offset += self.PAGE_LIMIT
                time.sleep(self.SCAN_RATE_LIMIT)

            except requests.exceptions.Timeout:
                logger.error(f"⏱️ [Mail.ru] Timeout на {path} (offset={offset})")
                break
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ [Mail.ru] Сетевая ошибка на {path}: {e}")
                break
            except (ValueError, KeyError) as e:
                logger.error(f"❌ [Mail.ru] Ошибка парсинга ответа {path}: {e}")
                break

        return photos

    def download_photo(self, photo: Dict) -> Optional[bytes]:
        """Скачивает фото по download_url с cookies сессии.

        URL Mail.ru не публичный — Telegram сервер сам по нему не сходит,
        нужно скачивать локально с cookies авторизованной сессии.
        """
        url = photo.get('download_url')
        name = photo.get('name', 'unknown')
        if not url:
            logger.warning(f"⚠️ [Mail.ru] Нет URL для {name}")
            return None
        if not self._authenticated:
            self._authenticate()

        try:
            resp = self.session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            data = resp.content
            if len(data) < 100:
                logger.warning(f"⚠️ [Mail.ru] Слишком маленький файл ({len(data)} байт): {name}")
                return None
            # Если cookies протухли, Mail.ru вернёт HTML страницы логина
            if data[:5] in (b'<!DOC', b'<html', b'<HTML'):
                logger.error(f"❌ [Mail.ru] Получен HTML вместо изображения для {name} — "
                             f"вероятно протухли cookies сессии")
                return None
            logger.debug(f"📥 [Mail.ru] Скачано {len(data)} байт: {name}")
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ [Mail.ru] Ошибка скачивания {name}: {e}")
            return None

    def _extract_date(self, item: Dict) -> Optional[datetime]:
        """Дата из имени → пути → mtime. EXIF Mail.ru не отдаёт через API."""
        name = item.get('name', '')
        date_from_name = self._extract_date_from_filename(name)
        if date_from_name:
            return date_from_name

        path = item.get('home', '')
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

    def _extract_date_from_path(self, path: str) -> Optional[datetime]:
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

    def _extract_date_from_filename(self, filename: str) -> Optional[datetime]:
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
