from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By

class TempMail:

    def __init__(self, temp_mail_url, driver: webdriver.Firefox) -> None:
        if len(temp_mail_url) == 0:
            raise ValueError("temp_mail_url is empty")
        self.temp_mail_url = temp_mail_url
        self.driver = driver

    def create_mail(self) -> str | None:
        self.driver.get(self.temp_mail_url)
        wait = WebDriverWait(self.driver, 15)

        # Функция проверяет, что поле появилось и в нем реальная почта (нет слова loading)
        def _get_email(driver):
            element = driver.find_element(By.ID, "mail")
            value = element.get_attribute("value")
            if value and "loading" not in value.lower():
                return value
            return None

        return wait.until(_get_email)

    def get_verification_link(self) -> str|None:
        self.driver.get(self.temp_mail_url)
        self.driver.find_element(By.CSS_SELECTOR, "a.viewLink.nu-reward").click()

        link_element = self.driver.find_element(
            By.CSS_SELECTOR, "div.inbox-data-content-intro > div > a"
        )
        return link_element.get_attribute("href")
