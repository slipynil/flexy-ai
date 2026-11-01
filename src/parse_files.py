import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def get_arm_wp_nonce(response_text: str) -> str:
    """Парсит форму главной страницы и возвращает найденное скрытое поле: arm_nonce_check."""
    soup = BeautifulSoup(response_text, 'html.parser')

    # Шаг 1: Находим инпут почты, чтобы привязаться к форме регистрации
    email_input = soup.find('input', {'name': 'user_email'})
    if not email_input:
        raise ValueError("Форма регистрации не найдена!")

    # Шаг 2: Поднимаемся до тега <form>
    form = email_input.find_parent('form')
    if not form:
        raise ValueError("Тег <form> для регистрации не найден.")

    logger.debug("Форма регистрации успешно найдена!")

    # Шаг 3: Ищем инпут с именем arm_wp_nonce именно внутри этой формы
    nonce_input = form.find('input', {'name': 'arm_wp_nonce'})

    if nonce_input and nonce_input.get('value'):
        nonce_value = nonce_input.get('value')
        logger.debug(f"Найден arm_wp_nonce: {nonce_value}")
        return str(nonce_value)

    raise ValueError("Поле arm_wp_nonce не найдено внутри формы.")

def get_undefined_and_arm_nonce_check_inputs(response_text: str) -> tuple[str, str]:
    """Парсит форму регистрации и возвращает найденные скрытые поля: undefined и arm_nonce_check."""
    soup = BeautifulSoup(response_text, 'html.parser')

    email_input = soup.find('input', {'name': 'user_email'})

    if not email_input:
        raise ValueError("Форма регистрации не найдена!")

    # Поднимаемся вверх по дереву тегов до тега <form>
    form = email_input.find_parent('form')

    if not form:
        raise ValueError("Тег <form> для регистрации не найден.")

    logger.debug("Форма регистрации успешно найдена!\n")

    undefined, arm_nonce_check = None, None
    inputs = form.find_all('input')
    for inp in inputs:
        input_type = inp.get('type')
        name = inp.get('name')
        value = inp.get('value', '') # Значение скрытого поля, если оно задано в HTML
        input_id = inp.get('id')
        if name == 'arm_nonce_check':
            arm_nonce_check = value
        if not name and input_type == 'hidden' and input_id is None and str(value).isdigit():
            undefined = value
        if undefined and arm_nonce_check:
            return str(undefined), str(arm_nonce_check)
    raise ValueError("Не удалось найти undefined и arm_nonce_check в форме регистрации.")
