import logging

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from undetected_chromedriver import time


class TempMail:

    def __init__(self, temp_mail_url, driver: webdriver.Chrome) -> None:
        if len(temp_mail_url) == 0:
            raise ValueError("temp_mail_url is empty")
        self.temp_mail_url = temp_mail_url
        self.logger = logging.getLogger(__name__)
        self.driver = driver

    def create_mail(self) -> str | None:
        self.driver.delete_all_cookies()
        self.driver.get(self.temp_mail_url)

        self.logger.debug("Ждем генерацию почты...")
        for _ in range(15):
            time.sleep(1)
            try:
                element = self.driver.find_element(By.ID, "mail")
                val = element.get_attribute("value")

                if val and "loading" not in val.lower() and "@" in val:
                    self.logger.debug(f"Почта успешно получена: {val}")
                    return val

            except NoSuchElementException:
                continue

        self.logger.error("Не удалось дождаться почты за 15 секунд.")
        return None

    def get_verification_link(self) -> str | None:
        self.driver.get(self.temp_mail_url)

        try:
            self.logger.info("Ждем появления письма во входящих...")

            # Ждем появления любой ссылки с классом viewLink (письмо пришло)
            link_element = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[class*='viewLink'][href*='/view/']"))
            )
            mail_view_url = link_element.get_attribute("href")
            self.logger.debug(f"Найдено письмо, переходим: {mail_view_url}")

            if mail_view_url:
                self.driver.get(mail_view_url)

            verify_link = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.inbox-data-content-intro a"))
            )
            return verify_link.get_attribute("href")
        except (TimeoutException, NoSuchElementException) as e:
            self.logger.error(f"Не удалось найти ссылку для верификации: {e}")
            return None
