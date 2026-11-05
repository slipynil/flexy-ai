class LoginFlapError(RuntimeError):
    """Вход выполнен (status=success), но flexy-ai не выдал user_token.

    Типичный ответ при флапе анти-бота хостинга: {'success': True,
    'data': {'logged_in': False}}. Лечится повторным входом «с нуля» —
    страница /login выдаст свежий nonce.

    Наследует RuntimeError: вызывающий код, не различающий типы ошибок,
    продолжает обрабатывать это как обычную ошибку API (cooldown аккаунта).
    """
