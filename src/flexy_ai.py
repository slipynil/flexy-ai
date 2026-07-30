import re
import requests

def register_user(login, email, password):
    session = requests.Session()
    url = "https://flexy-ai.com/wp-admin/admin-ajax.php"

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:153.0) Gecko/20100101 Firefox/153.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://flexy-ai.com",
        "Referer": "https://flexy-ai.com/"
    }

    # Шаг 0.1: Сначала заходим на главную страницу, чтобы получить сессию и стартовый nonce
    print("[*] Получаем первичную сессию с сайта...")
    main_page = session.get("https://flexy-ai.com/", headers=headers)

    nonce_match = re.search(r'["\']_wpnonce["\']\s*:\s*["\']([a-f0-9]+)["\']', main_page.text)
    if not nonce_match:
        nonce_match = re.search(r'name="arm_wp_nonce"\s+value="([a-f0-9]+)"', main_page.text)

    initial_nonce = nonce_match.group(1) if nonce_match else "5c973a4494"

    # Шаг 0.2: Запрашиваем свежие ключи форм через найденный стартовый nonce
    print("[*] Получаем свежие токены безопасности от сервера...")
    init_payload = {
        "action": "arm_reinit_session_multiple_form",
        "form_key_arr": "103_n5LUJjS4os,101_To12GxZgat,102_dIFNt1ce5J",
        "_wpnonce": initial_nonce
    }

    init_response = session.post(url, headers=headers, data=init_payload)
    try:
        init_data = init_response.json()
        nonce = init_data.get("nonce")
        form_random_key = init_data.get("101_To12GxZgat")

        if not nonce or not form_random_key:
            raise ValueError("Сервер не вернул нужные ключи")

        print(f"[✔] Успешно! Nonce: {nonce} | Form Key: {form_random_key}")
    except Exception as e:
        print(f"[✖] Ошибка получения токенов: {e}")
        print(init_response.text)
        return

    print(f"\n[*] Регистрация пользователя: {login} ({email})...")

    # Шаг 1: Проверка логина
    print("-> Шаг 1: Проверка логина...")
    session.post(url, headers=headers, data={
        "action": "arm_check_exist_field",
        "field": "user_login",
        "value": login,
        "_wpnonce": nonce
    })

    # Шаг 2: Проверка почты
    print("-> Шаг 2: Проверка почты...")
    session.post(url, headers=headers, data={
        "action": "arm_check_exist_field",
        "field": "user_email",
        "value": email,
        "_wpnonce": nonce
    })

    # Шаг 3: Отправка формы регистрации со свежими токенами
    print("-> Шаг 3: Отправка формы регистрации...")
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
        "redirect_to": "https://flexy-ai.com",
        "isAdmin": "0",
        "referral_url": "https://flexy-ai.com",
        "arm_form_id": "101",
        "form_filter_kp": "1",
        "undefined": "1785453192",
        "arm_nonce_check": "2e828d06f3",
        "jhvf9366": ""
    }

    response = session.post(url, headers=headers, data=payload)

    print("\n[✔] Результат регистрации:")
    print(response.json())
