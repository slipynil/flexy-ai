from selenium import webdriver

from src.flexy_ai import register_user
from src.temp_mail import TempMail
from src.generate_username import generate_random_username

def main():
    temp_mail = TempMail("https://temp-mail.org/en/", webdriver.Firefox())

    # 1. создаем почту в temp-mail.org
    mail = temp_mail.create_mail()

    # 2. регистрируем пользователя в flexy.ai
    register_user(
        login=generate_random_username(),
        email=mail,
        password="kraken1488",
    )

    # 3. получаем ссылку на верификацию flexy.ai
    verification_link = temp_mail.get_verification_link()
    if verification_link is None:
        print("Verification link not found")
    else:
        print(verification_link)
