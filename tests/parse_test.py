from bs4 import BeautifulSoup


def parse_registration_form(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')

    email_input = soup.find('input', {'name': 'user_email'})

    if not email_input:
        raise ValueError("Форма регистрации не найдена!")

    # Поднимаемся вверх по дереву тегов до тега <form>
    form = email_input.find_parent('form')

    if not form:
        raise ValueError("Тег <form> для регистрации не найден.")

    print("Форма регистрации успешно найдена!\n")
    print("-" * 40)

    # Шаг 2: Ищем ВСЕ инпуты ТОЛЬКО внутри этой формы
    inputs = form.find_all('input')

    for inp in inputs:
        input_type = inp.get('type')
        name = inp.get('name')
        value = inp.get('value', '') # Значение скрытого поля, если оно задано в HTML
        input_id = inp.get('id')

        # Выводим подробную информацию
        print(f"Тип: {input_type:<10} | Name: {str(name):<20} | Value: {str(value):<15} | ID: {input_id}")

def get_undefined_and_arm_nonce_check_inputs(response_text: str):
    soup = BeautifulSoup(response_text, 'html.parser')

    email_input = soup.find('input', {'name': 'user_email'})

    if not email_input:
        raise ValueError("Форма регистрации не найдена!")

    # Поднимаемся вверх по дереву тегов до тега <form>
    form = email_input.find_parent('form')

    if not form:
        raise ValueError("Тег <form> для регистрации не найден.")

    print("Форма регистрации успешно найдена!\n")

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
            return undefined, arm_nonce_check
    print("Не удалось найти undefined и arm_nonce_check в форме регистрации.")
    return "", ""

def parse_login(response_text: str):
    soup = BeautifulSoup(response_text, 'html.parser')

    inputs = soup.find_all('input')

    print(f"Найдено полей input: {len(inputs)}\n")

    for inp in inputs:
        name = inp.get('name')
        input_type = inp.get('type')

        if input_type == 'hidden' or not name:
            continue

        input_id = inp.get('id')
        print(f"Тип: {input_type} | Name: {name} | ID: {input_id}")

def main():
    file_path = "fixtures/first-visit.html"

    html_content = open(file_path, 'r', encoding='utf-8').read()

    parse_registration_form(html_content)

if __name__ == "__main__":
    main()
