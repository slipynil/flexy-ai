"""Ферма регистрации аккаунтов flexy-ai.com (ADR-001 фаза 2, ADR-002).

Раньше логика жила в scripts/run_register_accounts.py; переехала в src/,
чтобы её можно было вызывать и из CLI, и из UI-меню.

Все функции блокирующие (selenium + requests): вызывать из потоков.
"""

import logging
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


@contextmanager
def chrome_stderr_to_devnull(browser_binary: str):
    """uc запускает Chrome с stderr=subprocess.PIPE и никогда не читает его.

    Chrome при этом получает --log-level=0 (максимальная болтливость), и когда
    буфер пайпа (~64 КБ) переполняется, Chrome замирает на записи в stderr —
    chromedriver ~30 сек ждет DevTools-порт и сессия отваливается с
    'str' object has no attribute 'get'. Известный дефект uc 3.5.5.

    Обход: на время создания uc.Chrome подменяем Popen — stderr/stdout Chrome
    уходят в DEVNULL, писать больше некуда, зависнуть невозможно.
    """
    original_popen = subprocess.Popen

    def patched_popen(args, *popen_args, **popen_kwargs):
        if args and str(args[0]) == browser_binary:
            popen_kwargs["stderr"] = subprocess.DEVNULL
            popen_kwargs["stdout"] = subprocess.DEVNULL
        return original_popen(args, *popen_args, **popen_kwargs)

    subprocess.Popen = patched_popen
    try:
        yield
    finally:
        subprocess.Popen = original_popen


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
                return uc.Chrome(options=options, use_subprocess=True)

            browser_path, version_main = browser
            logger.info(f"Браузер: {browser_path} (v{version_main})")

            cached_driver = _cached_driver_path(version_main)
            if not cached_driver.exists():
                # нет кэша — качаем сами через requests (с таймаутами),
                # встроенный загрузчик uc без таймаутов может висеть минутами
                _download_driver(version_main, cached_driver)

            if cached_driver.exists():
                logger.info(f"Использую драйвер: {cached_driver}")
                with chrome_stderr_to_devnull(browser_path):
                    return uc.Chrome(
                        options=options,
                        browser_executable_path=browser_path,
                        version_main=version_main,
                        driver_executable_path=str(cached_driver),
                        use_subprocess=True,
                    )

            # скачать не удалось — последний шанс: встроенный загрузчик uc
            logger.warning("Пробую встроенный загрузчик uc (может занять время)...")
            with chrome_stderr_to_devnull(browser_path):
                driver = uc.Chrome(
                    options=options,
                    browser_executable_path=browser_path,
                    version_main=version_main,
                    use_subprocess=True,
                )
            _seed_driver_cache(version_main)
            return driver

        except Exception as e:
            last_error = e
            logger.error(f"Ошибка подключения: {e}")
            if attempt < BROWSER_START_ATTEMPTS:
                logger.debug("Ждем 5 секунд перед повторной попыткой...")
                time.sleep(5)

    raise RuntimeError(
        f"не удалось запустить браузер за {BROWSER_START_ATTEMPTS} попытки: {last_error}\n"
        "Проверь доступ к googlechromelabs.github.io и storage.googleapis.com "
        "(сеть/VPN) и что Chrome запускается вручную"
    )


def register_new_account(temp_mail: TempMail) -> tuple[str, str]:
    """Генерирует почту и регистрирует аккаунт на сайте, возвращает (mail, password)."""
    password = ACCOUNTS_PASSWORD

    while True:
        try:
            # 1. создаем почту в temp-mail.org
            mail = temp_mail.create_mail()
            if mail is None:
                logger.error("Ошибка регистрации: mail is None")
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
            logger.error(f"Непредвиденная ошибка: {e}")
            raise


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
    try:
        token = flexy_ai.verification_and_login(
            verification_link=verification_link,
            login=mail,
            password=password,
        )
    except (ValueError, RuntimeError) as e:
        logger.error(f"Ошибка верификации: {e}")
        return None
    return mail + ":" + token


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
