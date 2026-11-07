"""WAF-сессия flexy-ai.com: обход челленджа SafeLine (ADR-004).

С 07.11.2026 flexy-ai.com отвечает 468 + JS-челленджем SafeLine всем клиентам
без браузера. Челлендж детектит CDP-автоматизацию (devtools-detector: объекты с
getter'ами в console.log сериализуются из-за Runtime.enable у chromedriver;
timing `new Function('debugger')`), поэтому uc-браузер сам по себе не проходит —
но проходит с контр-инъекцией до загрузки страницы. Пройденные куки (sl_jwt_* и
др.) переносим в обычную requests.Session: сервер принимает запросы без
браузера (проверено end-to-end на регистрации и входе).

Все HTTP-обращения к flexy-ai.com идут через waf_request(): при 468 куки
пере-получаются (лениво, с кэшем на процесс) и запрос ретраится один раз.
"""

import logging
import threading
import time

import requests

from src.errors.waf_errors import WafBlockedError

logger = logging.getLogger(__name__)

# Нестандартный статус, которым SafeLine отвечает на «не-браузерные» запросы
WAF_STATUS_CODE = 468

# Сколько ждать прохождения челленджа (обычно проходит сам за ~5 сек)
WAF_CHALLENGE_TIMEOUT_SECONDS = 60

# Минимальный интервал между запусками браузера за куками: защита от шторма
# запусков, если челлендж перестал проходиться (468 повторяется)
WAF_MIN_OBTAIN_INTERVAL_SECONDS = 30

# Контр-инъекция против детекторов challenge.js. Должна выполняться ДО загрузки
# страницы (Page.addScriptToEvaluateOnNewDocument):
# - маскировка console-методов: объектные аргументы не доходят до CDP-сериализации
#   (гасит regToString/functionToString/dateToString/devtoolsFormatter/
#   performance чекеры devtools-detector);
# - патч Function-конструктора: `new Function('debugger')` становится no-op
#   (гасит debuggerChecker с timing-замером).
COUNTER_INJECT_JS = r"""
(() => {
    const patchFn = (orig) => function(...args) {
        for (let i = 0; i < args.length; i++) {
            if (typeof args[i] === 'string' && args[i].includes('debugger')) {
                args[i] = args[i].replace(/\bdebugger\b/g, '');
            }
        }
        return orig.apply(this, args);
    };
    try { Function.prototype.constructor = patchFn(Function.prototype.constructor); } catch (e) {}
    try { window.eval = patchFn(window.eval); } catch (e) {}
    const safe = (orig) => function(...args) {
        try {
            orig.call(console, ...args.map(a => (a && typeof a === 'object') || typeof a === 'function'
                ? '[masked]' : a));
        } catch (e) {}
    };
    for (const m of ['log', 'debug', 'info', 'warn', 'error', 'dir', 'dirxml',
                     'table', 'trace', 'group', 'groupCollapsed', 'groupEnd',
                     'profile', 'profileEnd']) {
        try {
            if (typeof console[m] === 'function') console[m] = safe(console[m].bind(console));
        } catch (e) {}
    }
})();
"""

# Кэш на процесс: куки челленджа переиспользуются всеми потоками
# (бот логинится из потоков asyncio.to_thread)
_cache: dict | None = None  # {"cookies": {name: value}, "ua": str, "obtained_at": float}
_lock = threading.Lock()
_last_obtain_at: float = 0.0


def obtain_waf_cookies() -> tuple[str, dict[str, str]]:
    """Прогоняет челлендж SafeLine в uc-браузере, возвращает (user_agent, cookies).

    Поднимает свой браузер через farm.setup_driver (ленивый импорт — farm
    импортирует registration, который импортирует нас: без ленивости цикл)
    и закрывает его после. Куки возвращаются для переноса в requests.Session.
    """
    # ленивый импорт: разорвать цикл waf_session -> farm -> registration -> waf_session
    from src.farm import setup_driver

    logger.info("Получаю WAF-сессию: прохожу челлендж SafeLine в браузере...")
    driver = setup_driver()
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": COUNTER_INJECT_JS}
        )
        driver.get("https://flexy-ai.com/")

        deadline = time.monotonic() + WAF_CHALLENGE_TIMEOUT_SECONDS
        src = ""
        while time.monotonic() < deadline:
            time.sleep(1)
            try:
                src = driver.page_source or ""
            except Exception:  # браузер может перезагружаться в момент челленджа
                src = ""
            # форма ARMember в DOM = челлендж пройден, открыт реальный сайт
            if "arm_form" in src:
                break
        else:
            # диагностика при «гонке вооружений»: какие тексты показывает челлендж
            try:
                texts = driver.execute_script(
                    "return Array.from(document.querySelectorAll('button, h1, h2, h3, div[id]'))"
                    ".map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))"
                    ".filter(t => t && t.length < 60)"
                    ".slice(0, 6)"
                )
            except Exception:
                texts = []
            logger.error(f"Челлендж SafeLine не пройден за {WAF_CHALLENGE_TIMEOUT_SECONDS} сек; "
                         f"тексты страницы: {texts}")
            raise WafBlockedError(
                "челлендж SafeLine не пройден (тексты в логе) — вероятно, "
                "challenge.js обновился, см. src/waf_session.py (ADR-004)"
            )

        ua = driver.execute_script("return navigator.userAgent")
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        logger.info(f"WAF-сессия получена: {len(cookies)} cookies")
        return ua, cookies
    finally:
        driver.quit()


def _obtain_into_cache() -> None:
    """Заполняет кэш свежими куками (вызывать под _lock).

    В кэш попадают только куки WAF (sl-*): PHPSESSID — сессия приложения,
    ее перезапись в чужой сессии роняет логин; _ym_* — Яндекс.Метрика.
    """
    global _cache, _last_obtain_at
    _last_obtain_at = time.monotonic()
    ua, cookies = obtain_waf_cookies()
    cookies = {name: value for name, value in cookies.items() if name.startswith("sl")}
    _cache = {"cookies": cookies, "ua": ua, "obtained_at": time.monotonic()}


def _apply_cache_to_session(session: requests.Session) -> None:
    """Кладёт куки из кэша в сессию (перезапись по имени; вызывать под _lock)."""
    for name, value in _cache["cookies"].items():
        session.cookies.set(name, value)


def seed_waf_cookies(session: requests.Session) -> None:
    """Кладёт в сессию WAF-куки из кэша; кэш пуст — получает (запуск браузера)."""
    with _lock:
        if _cache is None:
            _obtain_into_cache()
        _apply_cache_to_session(session)


def waf_request(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs,
) -> requests.Response:
    """HTTP-запрос к flexy-ai.com с авто-обновлением WAF-сессии при 468.

    При 468: если другой поток уже обновил кэш после начала нашей попытки —
    берём его куки; иначе сами получаем новые (браузер, не чаще
    WAF_MIN_OBTAIN_INTERVAL_SECONDS) и повторяем запрос один раз.
    Повторный 468 -> WafBlockedError.
    """
    started = time.monotonic()
    # новая сессия сразу получает куки из кэша: иначе её первый запрос
    # гарантированно ловит 468 (и поднимает браузер при живом кэше).
    # sl-* идемпотентны, перезапись безвредна (PHPSESSID в кэш не попадает)
    with _lock:
        if _cache is not None:
            _apply_cache_to_session(session)
    response = session.request(method, url, **kwargs)
    if response.status_code != WAF_STATUS_CODE:
        return response

    global _last_obtain_at
    with _lock:
        cache_fresh = (
            _cache is not None and _cache["obtained_at"] > started
        )
        if not cache_fresh:
            if time.monotonic() - _last_obtain_at < WAF_MIN_OBTAIN_INTERVAL_SECONDS:
                raise WafBlockedError(
                    "flexy-ai.com отвечает 468, а WAF-куки обновлялись только что — "
                    "челлендж не проходится повторно, подождите"
                )
            logger.warning("flexy-ai.com ответил 468: обновляю WAF-сессию...")
            _obtain_into_cache()
        _apply_cache_to_session(session)

    response = session.request(method, url, **kwargs)
    if response.status_code == WAF_STATUS_CODE:
        raise WafBlockedError(
            "flexy-ai.com отвечает 468 даже со свежими WAF-куками "
            "(детали — в логе, ADR-004)"
        )
    return response
