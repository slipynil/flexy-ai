class WafBlockedError(RuntimeError):
    """flexy-ai.com отвечает 468 (челлендж SafeLine WAF) даже со свежими куками.

    Подробнее: docs/adr/004-safeline-waf-bypass.md. raised из
    src/waf_session.py::waf_request после неудачного обновления WAF-сессии.

    Наследует RuntimeError: вызывающий код, не различающий типы ошибок,
    продолжает обрабатывать это как обычную ошибку API (cooldown аккаунта
    в generation.py и т.п.).
    """
