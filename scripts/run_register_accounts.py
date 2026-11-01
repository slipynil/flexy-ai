import argparse
import logging
import re
import shutil
import subprocess
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import undetected_chromedriver as uc
from selenium.common.exceptions import WebDriverException

from src.errors.email_errors import RegistrationEmailError
from src.flexy_ai.api import FlexyAiAPI
from src.flexy_ai.registration import register_user
from src.generate_username import generate_random_username
from src.repository.sqlite import DB
from src.temp_mail import TempMail

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("KrakenBot")


def get_chrome_major_version() -> str | None:
    """Определяет мажорную версию установленного Chrome/Chromium (например, '155')."""
    for binary in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        try:
            output = subprocess.check_output(
                [binary, "--version"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            match = re.search(r"(\d+)\.", output)
            if match:
                return match.group(1)
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return None


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
    while True:
        try:
            logger.info("Попытка запуска браузера...")
            options = uc.ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            # uc 3.5.5 сам не сверяет версию браузера и драйвера:
            # без version_main качает последний Stable, а браузер выбирает
            # перебором (может взять Chromium не той версии) -> SessionNotCreated.
            # Поэтому фиксируем оба: конкретный бинарник браузера и его версию.
            browser = find_browser()
            if browser:
                browser_path, version_main = browser
                logger.info(f"Браузер: {browser_path} (v{version_main})")
                return uc.Chrome(
                    options=options,
                    browser_executable_path=browser_path,
                    version_main=version_main,
                    use_subprocess=True,
                )

            # браузер не найден — пусть uc попробует сам
            return uc.Chrome(options=options, use_subprocess=True)

        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            logger.debug("Ждем 5 секунд перед повторной попыткой...")
            time.sleep(5)


def register_new_account(temp_mail: TempMail) -> tuple[str, str]:
    """Генерирует почту и регистрирует аккаунт на сайте, возвращает (mail, password)."""
    password = "kraken1488"


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


def main():
    parser = argparse.ArgumentParser(description="Кракер бот для FlexyAI")
    parser.add_argument("--counts", required=True, help="количество аккаунтов для регистрации", type=int)
    args = parser.parse_args()

    db = DB("./data/data.db")
    driver = setup_driver()

    try:
        for i in range(args.counts):
            temp_mail = TempMail("https://temp-mail.org/en/", driver)
            mail, password = register_new_account(temp_mail)

            api_key = verify_and_get_api_key(temp_mail, mail, password)
            if api_key is None:
                return

            db.add_account(api_key, mail, password)
            logger.info(f"Аккаунт №{i} успешно зарегистрирован и сохранен!")

    finally:
        driver.quit()
        db.close()


if __name__ == "__main__":
    main()
