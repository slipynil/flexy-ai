import logging
import re

import requests
from bs4 import BeautifulSoup

from src.errors.email_errors import RegistrationEmailError
from src.parse_files import get_arm_wp_nonce

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

AJAX_URL = "https://flexy-ai.com/wp-admin/admin-ajax.php"
BASE_URL = "https://flexy-ai.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL,
}


def get_initial_tokens(session: requests.Session, headers: dict):
    """Шаг 2: Получает стартовый nonce со страницы и запрашивает актуальные ключи формы."""
    # Получаем главную страницу через requests для поиска стартового nonce и ключей
    main_page = session.get(BASE_URL, headers=headers, timeout=15)
    # Ищем все ключи формата префикс_хеш на странице
    found_keys = re.findall(r'(\d+_[a-zA-Z0-9]{10})', main_page.text)
    # Распределяем по конкретным ID форм, чтобы гарантированно передать 101, 102 и 103
    keys_dict = {}
    for k in found_keys:
        prefix = k.split('_')[0]
        if prefix in ['101', '102', '103'] and prefix not in keys_dict:
            keys_dict[prefix] = k

    # Если на странице ключи не подгрузились регуляркой, используем актуальные дефолтные
    form_103 = keys_dict.get('103', '103_GxF6iUdIs6')
    form_101 = keys_dict.get('101', '101_Me1WKoglp8')
    form_102 = keys_dict.get('102', '102_5ouXsg8SUC')

    # Порядок следования ключей в строке важен (соответствует тому, как шлет браузер)
    form_key_arr_str = f"{form_103},{form_101},{form_102}"

    try:
        arm_wp_nonce = get_arm_wp_nonce(main_page.text)
    except (ValueError):
        return None, None

    # Запрашиваем свежие токены безопасности через AJAX
    init_payload = {
        "action": "arm_reinit_session_multiple_form",
        "form_key_arr": form_key_arr_str,
        "_wpnonce": arm_wp_nonce
    }

    response = session.post(AJAX_URL, headers=headers, data=init_payload, timeout=15)

    try:
        data = response.json()
    except Exception as e:
        logger.error(f"Сервер не вернул JSON! Статус: {response.status_code}. Ответ: {response.text[:200]}")
        return None, None

    if not isinstance(data, dict):
        # при сбое может прийти голая JSON-строка — .items()/.get() на ней падают
        logger.error(f"Сервер вернул неожиданный ответ: {str(data)[:200]}")
        return None, None

    # Безопасно достаем динамический ключ для 101 формы (пробуем из ответа, иначе берем из словаря)
    form_random_key = None
    for key, val in data.items():
        if key.startswith("101_"):
            form_random_key = val
            break

    if not form_random_key:
        form_random_key = data.get(form_101)

    return data.get("nonce"), form_random_key

def check_field(session: requests.Session, headers: dict, field_name: str, value: str, nonce: str):
    logger.info(f"-> Проверка поля {field_name}: {value}")
    response = session.post(AJAX_URL, headers=headers, data={
        "action": "arm_check_exist_field",
        "field": field_name,
        "value": value,
        "_wpnonce": nonce
    }, timeout=15)
    logger.info(f"Ответ сервера: {response.text[:200]}")

def register_user(login, email, password):
    session = requests.Session()

    try:
        nonce, form_random_key = get_initial_tokens(session, HEADERS)
        if not nonce or not form_random_key:
            logger.error("Не удалось получить рабочие токены от сервера.")
            return
    except Exception as e:
        logger.error(f"Ошибка при получении токенов: {e}")
        return

    logger.info(f"Токены получены! Nonce: {nonce} | Form Key: {form_random_key}")
    logger.info(f"Начинаем регистрацию пользователя: {login} ({email})...")

    check_field(session, HEADERS, "user_login", login, nonce)
    check_field(session, HEADERS, "user_email", email, nonce)

    logger.info("-> Отправка формы регистрации...")
    payload = {
        "action": "arm_shortcode_form_ajax_action",
        "form_random_key": form_random_key,
        "arm_wp_nonce": nonce,
        "arm_wp_nonce_check": "1",
        "user_login": login,
        "user_email": email,
        "user_pass": password,
        "checkbox_abgvf": "Я принимаю пользовательское соглашение",
        "checkbox_abgvf_arm_hidden": "Я принимаю пользовательское соглашение",
        "arm_action": "pozhalujsta-zaregistrirujtes",
        "redirect_to": BASE_URL,
        "isAdmin": "0",
        "referral_url": BASE_URL,
        "arm_form_id": "101",
        "form_filter_kp": "1",
        "undefined": "1785453192",
        "arm_nonce_check": "2e828d06f3",
        "jhvf9366": ""
    }

    response = session.post(AJAX_URL, headers=HEADERS, data=payload, timeout=15)
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(f"Сервер вернул не JSON: {response.text[:200]}")

    # admin-ajax/ARMember может ответить голой JSON-строкой: .get() на ней
    # ронял регистрацию с "'str' object has no attribute 'get'"
    if not isinstance(data, dict):
        raise RuntimeError(f"Сервер вернул неожиданный ответ: {str(data)[:200]}")

    if data.get("status") == "error":
        raw_message = data.get("message", "Неизвестная ошибка")
        clean_message = BeautifulSoup(raw_message, "html.parser").get_text(strip=True)
        raise RegistrationEmailError(clean_message)

    logger.info("Регистрация прошла успешно")
