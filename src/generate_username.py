import random
import string

def generate_random_username(length=12):
    # Берем буквы в нижнем регистре и цифры
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))
