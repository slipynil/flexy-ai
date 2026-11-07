import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from src.config import ARM_FORM_FILL_DELAY_SECONDS
from src.errors.email_errors import RegistrationEmailError
from src.parse_files import get_arm_form_fields, get_arm_wp_nonce
from src.waf_session import waf_request

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


def _extract_form_keys(html: str) -> list[str]:
    """Ключи форм ARMember на странице, в порядке появления, без дублей."""
    keys: list[str] = []
    for key in re.findall(r"(\d+_[a-zA-Z0-9]{10})", html):
        if key not in keys:
            keys.append(key)
    return keys


def reinit_forms(session: requests.Session, page_html: str, headers: dict | None = None) -> dict | None:
    """arm_reinit_session_multiple_form: свежий arm_wp_nonce и 8-символьные
    ключи сабмита для всех форм страницы (так делает JS ARMember на загрузке).

    Ответ сервера: {'nonce': <hex>, '<ключ формы>': '<8 символов>', ...}.
    После восстановления хостинга 8-символьные значения используются как
    имена обязательных полей-маркеров «живого» сабмита (см. register_user /
    FlexyAiAPI._login_user).
    """
    found_keys = _extract_form_keys(page_html)
    if not found_keys:
        logger.error("На странице нет ключей форм ARMember (структура сайта изменилась?)")
        return None

    try:
        arm_wp_nonce = get_arm_wp_nonce(page_html)
    except ValueError:
        return None

    response = waf_request(session, "POST", AJAX_URL, headers=headers or {}, data={
        "action": "arm_reinit_session_multiple_form",
        "form_key_arr": ",".join(found_keys),
        "_wpnonce": arm_wp_nonce
    }, timeout=15)

    try:
        data = response.json()
    except Exception as e:
        logger.error(f"Сервер не вернул JSON! Статус: {response.status_code}. Ответ: {response.text[:200]}")
        return None

    if not isinstance(data, dict):
        # при сбое может прийти голая JSON-строка — .items()/.get() на ней падают
        logger.error(f"Сервер вернул неожиданный ответ: {str(data)[:200]}")
        return None

    return data


def form_random_id(page_html: str, form_prefix: str) -> str | None:
    """data-random-id формы с заданным префиксом (101 — регистрация, 102 — вход).

    Именно это значение текущая версия ARMember (4.0.66) ждет в поле
    form_random_key при сабмите — не 8-символьное значение reinit.
    """
    match = re.search(rf'data-random-id="({form_prefix}_[a-zA-Z0-9]{{10}})"', page_html)
    return match.group(1) if match else None


def reinit_key_for(data: dict, form_prefix: str) -> str | None:
    """8-символьный ключ сабмита конкретной формы из ответа reinit_forms."""
    return next(
        (val for key, val in data.items() if key.startswith(f"{form_prefix}_")), None
    )

def check_field(session: requests.Session, headers: dict, field_name: str, value: str, nonce: str):
    logger.info(f"-> Проверка поля {field_name}: {value}")
    response = waf_request(session, "POST", AJAX_URL, headers=headers, data={
        "action": "arm_check_exist_field",
        "field": field_name,
        "value": value,
        "_wpnonce": nonce
    }, timeout=15)
    logger.info(f"Ответ сервера: {response.text[:200]}")

def register_user(login, email, password, session: requests.Session | None = None):
    session = session or requests.Session()
    # момент загрузки формы: anti-бот ARMember отклоняет сабмиты раньше
    # ARM_FORM_FILL_DELAY_SECONDS с загрузки страницы («Обнаружен спам»)
    started = time.monotonic()

    try:
        # 468 (челлендж SafeLine) обрабатывается внутри waf_request: куки
        # получаются лениво при первом ответе WAF (ADR-004)
        main_page = waf_request(session, "GET", BASE_URL, headers=HEADERS, timeout=15)
        main_page.raise_for_status()
        # поля формы регистрации после восстановления хостинга динамические
        # (arm_action=please-signup, referral_url, undefined, arm_nonce_check) —
        # парсим со страницы вместо хардкода
        fields = get_arm_form_fields(main_page.text, login_form=False)
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Ошибка при получении формы регистрации: {e}")
        return None

    # reinit всех форм страницы (как JS ARMember) + ключи формы регистрации
    reinit = reinit_forms(session, main_page.text, HEADERS)
    if not reinit or not reinit.get("nonce"):
        logger.error("Не удалось получить рабочие токены от сервера.")
        return None

    nonce = reinit["nonce"]
    # полный ключ формы (data-random-id) — его шлет arm_form_ajax_action
    full_form_key = form_random_id(main_page.text, "101")
    # 8-символьный ключ сабмита из reinit
    form_random_key = reinit_key_for(reinit, "101")
    if not full_form_key or not form_random_key:
        logger.error("Не найдены ключи формы регистрации (структура сайта изменилась?)")
        return None

    logger.info(f"Токены получены! Nonce: {nonce} | Form Key: {form_random_key}")
    logger.info(f"Начинаем регистрацию пользователя: {login} ({email})...")

    check_field(session, HEADERS, "user_login", login, nonce)
    check_field(session, HEADERS, "user_email", email, nonce)

    # дозасыпаем до минимального «времени заполнения» формы
    remaining = ARM_FORM_FILL_DELAY_SECONDS - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)

    logger.info("-> Отправка формы регистрации...")
    # Payload повторяет то, что собирает arm_form_ajax_action (arm_common.js):
    # 1) form_random_key = data-random-id формы (полный ключ 101_XXXXXXXXXX);
    # 2) honeypot arm_filter_input JS удаляет перед serialize — не отправляем;
    # 3) безымянный числовой инпут («stime») получает имя form_filter_st,
    #    счетчик нажатий — form_filter_kp;
    # 4) последний инпут формы переименован в 8-символьное значение reinit —
    #    сервер проверяет наличие этого поля как признак «живого» сабмита.
    # Без п.1-4 ARMember отвечал «Обнаружен спам».
    payload: dict = dict(fields)
    st_time = payload.pop("undefined", "")
    payload.pop("arm_filter_input", None)
    payload.update({
        "action": "arm_shortcode_form_ajax_action",
        "form_random_key": full_form_key,
        "arm_wp_nonce": nonce,
        "user_login": login,
        "user_email": email,
        "user_pass": password,
        "form_filter_st": st_time,
        # клик по кнопке submit инкрементирует счетчик нажатий
        "form_filter_kp": "5",
    })
    payload[form_random_key] = ""

    response = waf_request(session, "POST", AJAX_URL, headers=HEADERS, data=payload, timeout=15)
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
    # True/None (а не исключение) при «мягких» провалах: старый temp-mail флоу
    # игнорирует возврат, новый (ADR-003) использует для перехода к другому email
    return True
