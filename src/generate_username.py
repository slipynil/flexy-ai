import random
import string

from src.config import RANDOM_EMAIL_DOMAINS


def generate_random_username(length=12):
    # Берем буквы в нижнем регистре и цифры
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))


def generate_random_email() -> str:
    """Случайная почта для регистрации без верификации (ADR-003).

    Локальная часть — случайный username с цифровой «приставкой»
    (как у реальных адресов вида alex9931), домен — из RANDOM_EMAIL_DOMAINS.
    """
    suffix = "".join(random.choices(string.digits, k=random.randint(2, 4)))
    domain = random.choice(RANDOM_EMAIL_DOMAINS)
    return f"{generate_random_username(8)}{suffix}@{domain}"
