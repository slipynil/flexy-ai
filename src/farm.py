"""Ферма регистрации аккаунтов flexy-ai.com (ADR-001 фаза 2, ADR-002).

Раньше логика жила в scripts/run_register_accounts.py; переехала в src/,
чтобы её можно было вызывать и из CLI, и из UI-меню.

Все функции блокирующие (selenium + requests): вызывать из потоков.
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

import requests
import undetected_chromedriver as uc
from undetected_chromedriver import dprocess

from src.errors.email_errors import RegistrationEmailError
from src.flexy_ai.api import FlexyAiAPI
from src.flexy_ai.registration import register_user
from src.generate_username import generate_random_username
from src.repository.sqlite import DB
from src.temp_mail import TempMail

logger = logging.getLogger(__name__)

# Пароль для всех регистрируемых аккаунтов
ACCOUNTS_PASSWORD = "kraken1488"

# Сколько раз пробуем запустить браузер, прежде чем сдаться
BROWSER_START_ATTEMPTS = 3

# Каталог, где uc хранит пропатченный драйвер
UC_CACHE_DIR = Path.home() / ".local" / "share" / "undetected_chromedriver"
# Имя файла по умолчанию, куда uc кладет свежескачанный драйвер
UC_DEFAULT_PATCHED_NAME = "undetected_chromedriver"

# Логи Chrome: при сбое запуска показываем хвост последнего лога
CHROME_LOG_DIR = Path("data/chrome-logs")
CHROME_LOG_LINES = 15
CHROME_LOG_KEEP = 10

# Сколько раз пробуем получить почту с temp-mail.org на один аккаунт
# (раньше while True крутился бесконечно, если сайт не отдавал почту)
MAIL_CREATE_ATTEMPTS = 5

# Chrome for Testing: версии и ссылки на драйверы по мажорным версиям
CFT_MILESTONES_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "latest-versions-per-milestone-with-downloads.json"
)


def _cached_driver_path(version_main: int) -> Path:
    """Путь к нашему версионному кэшу пропатченного драйвера."""
    return UC_CACHE_DIR / f"{UC_DEFAULT_PATCHED_NAME}_v{version_main}"


def _cft_platform() -> str:
    """Имя платформы в нотации Chrome for Testing."""
    if sys.platform.startswith("linux"):
        return "linux64"
    if sys.platform == "darwin":
        return "mac-arm64" if platform.machine() == "arm64" else "mac-x64"
    return "win64" if platform.machine().endswith("64") else "win32"


def _download_driver(version_main: int, dst: Path) -> bool:
    """Скачивает chromedriver нужной мажорной версии и патчит его для uc.

    В отличие от встроенного загрузчика uc — с таймаутами через requests,
    чтобы при плохой сети падать быстро, а не висеть минутами.
    Возвращает True, если драйвер готов (скачан и пропатчен).
    """
    try:
        UC_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        resp = requests.get(CFT_MILESTONES_URL, timeout=15)
        resp.raise_for_status()
        milestone = resp.json()["milestones"].get(str(version_main))
        if milestone is None:
            logger.warning(f"В Chrome for Testing нет версии {version_main}")
            return False

        downloads = milestone["downloads"]["chromedriver"]
        url = next(d["url"] for d in downloads if d["platform"] == _cft_platform())

        logger.info(f"Скачиваю chromedriver {milestone['version']}...")
        zip_path = dst.with_suffix(".zip")
        with requests.get(url, timeout=(10, 120), stream=True) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)

        import tempfile

        with zipfile.ZipFile(zip_path) as zf, tempfile.TemporaryDirectory() as tmp:
            member = next(
                n for n in zf.namelist()
                if n.endswith("/chromedriver") or n == "chromedriver.exe"
            )
            zf.extract(member, path=tmp)
            shutil.move(Path(tmp) / member, dst)
        zip_path.unlink(missing_ok=True)
        dst.chmod(0o755)

        # патчим бинарник средствами uc (без скачивания — уже свой путь)
        patcher = uc.Patcher(executable_path=str(dst), version_main=version_main)
        patcher.auto()

        logger.info(f"Драйвер готов: {dst}")
        return True
    except Exception as e:
        logger.warning(f"Не удалось скачать драйвер самостоятельно: {e}")
        return False


def _seed_driver_cache(version_main: int) -> None:
    """Сохраняет свежескачанный uc-драйвер в версионный кэш (best effort).

    Нужен только для fallback-пути, когда драйвер качал сам uc.
    """
    try:
        src = UC_CACHE_DIR / UC_DEFAULT_PATCHED_NAME
        dst = _cached_driver_path(version_main)
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            logger.info(f"Драйвер закэширован: {dst}")
    except OSError as e:
        logger.debug(f"Не удалось закэшировать драйвер: {e}")


def _new_chrome_log_path() -> Path:
    """Создает файл для лога Chrome и подчищает старые (держим последние N)."""
    CHROME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = CHROME_LOG_DIR / f"chrome_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.log"
    try:
        logs = sorted(p for p in CHROME_LOG_DIR.glob("chrome_*.log"))
        for old in logs[:-CHROME_LOG_KEEP]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return log_path


def _chrome_log_tail() -> str:
    """Хвост самого свежего лога Chrome — для диагностики сбоя запуска."""
    try:
        logs = sorted(CHROME_LOG_DIR.glob("chrome_*.log"))
        if not logs:
            return ""
        lines = logs[-1].read_text(errors="replace").splitlines()
        return "\n".join(lines[-CHROME_LOG_LINES:])
    except OSError:
        return ""


def _start_chrome_detached(executable: str, *args: str) -> int:
    """Замена uc.start_detached (используется при use_subprocess=False).

    Оригинал uc открывает Chrome с stderr/stdout=PIPE и никогда не читает их.
    Chrome при этом получает --log-level=0 (болтливый), буфер пайпа (~64 КБ)
    переполняется, Chrome замирает на записи — chromedriver ~30 сек ждет
    DevTools-порт, и сессия падает с 'str' object has no attribute 'get'.

    Эта версия направляет stderr Chrome в файл лога (data/chrome-logs/):
    пайп не переполняется, а при сбое запуска лог можно показать пользователю.
    Возвращает pid напрямую, как и ожидает uc.
    """
    # файл закрываем сразу после Popen: ребенок получил свою копию дескриптора
    with open(_new_chrome_log_path(), "ab") as chrome_log:
        proc = subprocess.Popen(
            [executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=chrome_log,
            start_new_session=True,
        )
    # паритет с оригиналом: uc держит реестр pid'ов для очистки при выходе
    dprocess.REGISTERED.append(proc.pid)
    return proc.pid


@contextmanager
def uc_launch():
    """Запуск uc.Chrome с локальным обходом дефекта stderr-пайпа (правило 9).

    На время вызова uc.Chrome подменяет uc.start_detached на версию с DEVNULL
    и восстанавливает оригинал после. Использовать строго как контекстный
    менеджер вокруг uc.Chrome — патч не должен жить дольше запуска.
    """
    original = uc.start_detached
    uc.start_detached = _start_chrome_detached
    try:
        yield
    finally:
        uc.start_detached = original


def _launch_uc(
    options: uc.ChromeOptions,
    browser_path: str | None = None,
    version_main: int | None = None,
    driver_executable_path: str | None = None,
) -> uc.Chrome:
    """Единая точка создания uc.Chrome (с локальным обходом дефекта)."""
    kwargs: dict = {"options": options, "use_subprocess": False}
    if browser_path:
        kwargs["browser_executable_path"] = browser_path
        kwargs["version_main"] = version_main
    if driver_executable_path:
        kwargs["driver_executable_path"] = driver_executable_path
    with uc_launch():
        return uc.Chrome(**kwargs)


def find_browser() -> tuple[str, int] | None:
    """Находит установленный Chrome/Chromium.

    Возвращает (путь к бинарнику, мажорная версия) или None.
    """
    for binary in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(binary)
        if not path:
            continue
        try:
            output = subprocess.check_output(
                [path, "--version"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            match = re.search(r"(\d+)\.", output)
            if match:
                return path, int(match.group(1))
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def setup_driver() -> uc.Chrome:
    """Запускает uc-браузер, переиспользуя закэшированный драйвер.

    uc 3.5.5 сам не сверяет версию браузера и драйвера: без version_main
    качает последний Stable, а браузер выбирает перебором (может взять
    Chromium не той версии) -> SessionNotCreated. Поэтому фиксируем оба.

    Пропатченный драйвер кэшируется в версионный файл и переиспользуется
    через driver_executable_path: uc по умолчанию перекачивает драйвер
    при каждом запуске через urllib без таймаутов и виснет при плохой сети.
    """
    last_error: Exception | None = None

    for attempt in range(1, BROWSER_START_ATTEMPTS + 1):
        try:
            logger.info(f"Попытка запуска браузера ({attempt}/{BROWSER_START_ATTEMPTS})...")
            options = uc.ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            browser = find_browser()
            if browser is None:
                # браузер не найден — пусть uc попробует сам
                return _launch_uc(options)

            browser_path, version_main = browser
            logger.info(f"Браузер: {browser_path} (v{version_main})")

            cached_driver = _cached_driver_path(version_main)
            if not cached_driver.exists():
                # нет кэша — качаем сами через requests (с таймаутами),
                # встроенный загрузчик uc без таймаутов может висеть минутами
                _download_driver(version_main, cached_driver)

            if cached_driver.exists():
                logger.info(f"Использую драйвер: {cached_driver}")
                return _launch_uc(
                    options,
                    browser_path=browser_path,
                    version_main=version_main,
                    driver_executable_path=str(cached_driver),
                )

            # скачать не удалось — последний шанс: встроенный загрузчик uc
            logger.warning("Пробую встроенный загрузчик uc (может занять время)...")
            driver = _launch_uc(options, browser_path=browser_path, version_main=version_main)
            _seed_driver_cache(version_main)
            return driver

        except Exception as e:
            last_error = e
            # exc_info: без traceback ('str' object has no attribute 'get' и т.п.)
            # невозможно понять, где именно упало
            logger.error(f"Ошибка подключения: {e}", exc_info=True)
            # хвост лога Chrome: что происходило в браузере перед сбоем
            tail = _chrome_log_tail()
            if tail:
                logger.error(f"Последние строки лога Chrome ({CHROME_LOG_DIR}):\n{tail}")
            if attempt < BROWSER_START_ATTEMPTS:
                logger.debug("Ждем 5 секунд перед повторной попыткой...")
                time.sleep(5)

    raise RuntimeError(
        f"не удалось запустить браузер за {BROWSER_START_ATTEMPTS} попытки: {last_error}\n"
        "Логи Chrome — в data/chrome-logs/. Проверь, что Chrome запускается вручную,\n"
        "и доступ к googlechromelabs.github.io / storage.googleapis.com (сеть/VPN)"
    )


def register_new_account(temp_mail: TempMail) -> tuple[str, str]:
    """Генерирует почту и регистрирует аккаунт на сайте, возвращает (mail, password)."""
    password = ACCOUNTS_PASSWORD

    # temp-mail.org флапает (Cloudflare, медленная доставка) — ретраи ограничены,
    # раньше while True мог крутиться бесконечно
    for mail_attempt in range(1, MAIL_CREATE_ATTEMPTS + 1):
        try:
            # 1. создаем почту в temp-mail.org
            mail = temp_mail.create_mail()
            if mail is None:
                logger.error(
                    f"Почта не получена (попытка {mail_attempt}/{MAIL_CREATE_ATTEMPTS})"
                )
                continue

            username = generate_random_username()
            # 2. регистрируем пользователя в flexy.ai
            register_user(
                login=username,
                email=mail,
                password=password,
            )
            return mail, password
        except RegistrationEmailError as e:
            logger.error(f"Ошибка регистрации: {e}")
            continue
        except Exception as e:
            logger.error(f"Непредвиденная ошибка: {e}", exc_info=True)
            raise

    raise RuntimeError(
        f"не удалось получить почту с temp-mail.org за {MAIL_CREATE_ATTEMPTS} попыток"
    )


def verify_and_get_api_key(temp_mail: TempMail, mail: str, password: str) -> str | None:
    """Получает ссылку из письма, подтверждает аккаунт и возвращает готовый api_key."""
    verification_link = temp_mail.get_verification_link()
    if verification_link is None:
        logger.error("Verification link не найден")
        return None

    logger.debug(f"verification_link: {verification_link}")
    logger.debug(f"e-mail: {mail}")
    logger.debug(f"password: {password}")

    flexy_ai = FlexyAiAPI()
    # вход и получение токена флапают (анти-бот хостинга, одноразовые nonce):
    # делаем одну повторную попытку обычным логином, без ссылки верификации
    for attempt in (1, 2):
        try:
            if attempt == 1:
                token = flexy_ai.verification_and_login(
                    verification_link=verification_link,
                    login=mail,
                    password=password,
                )
            else:
                logger.warning("Повторная попытка входа обычным логином...")
                token = flexy_ai.login(mail, password)
            return mail + ":" + token
        except (ValueError, RuntimeError) as e:
            logger.error(f"Ошибка верификации (попытка {attempt}/2): {e}")
    return None


def register_accounts(count: int) -> int:
    """Регистрирует count аккаунтов и сохраняет их в пул.

    Возвращает количество успешно зарегистрированных.
    """
    db = DB("./data/data.db")
    driver = setup_driver()
    registered = 0

    try:
        for i in range(count):
            temp_mail = TempMail("https://temp-mail.org/en/", driver)
            mail, password = register_new_account(temp_mail)

            api_key = verify_and_get_api_key(temp_mail, mail, password)
            if api_key is None:
                logger.error("Аккаунт не верифицирован, прерываем")
                break

            db.add_account(api_key, mail, password)
            registered += 1
            logger.info(f"Аккаунт №{i} успешно зарегистрирован и сохранен!")

    finally:
        driver.quit()
        db.close()

    return registered
