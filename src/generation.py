"""Сервис генерации изображений через пул аккаунтов (ADR-001).

Инкапсулирует единый для бота и CLI-скриптов сценарий:
захват свободного аккаунта -> login -> генерация -> списание tries.
При ошибке API аккаунт уходит в cooldown и берется следующий,
пока не закончатся все свободные.

Все функции блокирующие: вызывать из потоков (asyncio.to_thread).
"""

import logging
import time

from requests.exceptions import RequestException

from src.config import (
    ACCOUNT_COOLDOWN_SECONDS,
    DEFAULT_PROMPT,
    GENERATION_DEADLINE_SECONDS,
    LOGIN_ATTEMPTS,
    SERVICE_BREAKER_SECONDS,
    SERVICE_UNAVAILABLE_MESSAGE,
    SYSTEMIC_FAILURE_THRESHOLD,
)
from src.errors.api_errors import LoginFlapError
from src.flexy_ai.api import FlexyAiAPI
from src.repository.sqlite import DB

logger = logging.getLogger(__name__)

# «Предохранитель»: monotonic-время, до которого сервис считается системно
# сломанным и запросы завершаются мгновенно. None — не сработал.
# Записывается из потоков (asyncio.to_thread): присваивание float под GIL
# атомарно, гонка стоит максимум одного лишнего перебора — приемлемо
_service_broken_until: float | None = None


def _check_service_breaker() -> None:
    """Мгновенный отказ, если сервис недавно признан системно сломанным."""
    if _service_broken_until is not None and time.monotonic() < _service_broken_until:
        raise RuntimeError(SERVICE_UNAVAILABLE_MESSAGE)


def _trip_service_breaker() -> None:
    """Открывает «предохранитель»: новые запросы падают сразу, не трогая пул."""
    global _service_broken_until
    _service_broken_until = time.monotonic() + SERVICE_BREAKER_SECONDS


def context_to_image(image_path: str, prompt: str = DEFAULT_PROMPT) -> str:
    """Генерация «изображение -> изображение» через пул аккаунтов.

    Возвращает путь к сохраненному файлу.
    """
    return _generate_with_pool(lambda api: api.context_to_image(image_path, prompt))


def text_to_image(prompt: str) -> str:
    """Генерация «текст -> изображение» через пул аккаунтов.

    Возвращает путь к сохраненному файлу.
    """
    return _generate_with_pool(lambda api: api.text_to_image(prompt))


def _login_with_retry(api: FlexyAiAPI, email: str, password: str) -> None:
    """Вход с повторной попыткой при флапе токена.

    admin-ajax иногда отвечает success, но не выдает user_token
    ({'data': {'logged_in': False}}) — анти-бот хостинга. Повторяем вход
    «с нуля»: страница /login выдаст свежий nonce. Тот же прием, что и в
    farm.py::verify_and_get_api_key. Прочие ошибки (сеть, неверный пароль)
    не ретраятся — их обрабатывает _generate_with_pool как обычно.
    """
    for attempt in range(1, LOGIN_ATTEMPTS + 1):
        try:
            api.login(email, password)
            return
        except LoginFlapError as e:
            if attempt >= LOGIN_ATTEMPTS:
                raise
            logger.warning(
                f"токен не выдан (попытка {attempt}/{LOGIN_ATTEMPTS}), "
                f"повторный вход {email}: {e}"
            )


def _generate_with_pool(generate) -> str:
    """Выполняет generate(api), перебирая свободные аккаунты.

    Если API заблокировал аккаунт (rate-limit), тот уходит в cooldown,
    и автоматически берется следующий — пока не закончатся все.

    Защита от «вне штатного» режима flexy-ai.com:
    - перебор ограничен по времени (GENERATION_DEADLINE_SECONDS): каждая
      неудачная попытка тянется десятки секунд, без потолка пользователь
      ждал бы минуты и все равно получил ошибку;
    - флап токена на SYSTEMIC_FAILURE_THRESHOLD аккаунтах подряд — проблема
      серверная (сессии не держатся), остальные аккаунты не жжем: сразу
      отказ и «предохранитель» на SERVICE_BREAKER_SECONDS — новые запросы
      падают мгновенно, не расходуя пул.

    Уже запущенная генерация не прерывается: опрос статуса ограничен своим
    таймаутом в FlexyAiAPI.
    """
    db = DB()
    deadline = time.monotonic() + GENERATION_DEADLINE_SECONDS
    flap_accounts = 0
    try:
        while True:
            _check_service_breaker()

            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"flexy-ai.com отвечает слишком медленно дольше "
                    f"{GENERATION_DEADLINE_SECONDS} сек — попробуйте позже"
                )

            account = db.get_account()
            if account is None:
                raise RuntimeError(
                    "нет свободных аккаунтов: все заблокированы или исчерпаны, попробуйте позже"
                )

            api_key, email, password = account
            logger.info(f"генерация через аккаунт {email}")

            api = FlexyAiAPI()
            try:
                _login_with_retry(api, email, password)
                api.set_api_key(api_key)
                output_filepath = generate(api)
                db.update_account_tries(api_key)
                logger.info(f"генерация через {email} успешна: {output_filepath}")
                return output_filepath
            except (RuntimeError, ValueError, RequestException) as e:
                # RequestException (сброс соединения, таймаут и т.п.) — сетевые сбои
                # хостинга лечим так же, как ошибки API: cooldown + следующий аккаунт,
                # иначе сброс на TLS-рукопожатии ронял хендлер без ответа пользователю
                logger.warning(f"аккаунт {email} недоступен, уходит в cooldown: {e}")
                db.set_account_busy(api_key, ACCOUNT_COOLDOWN_SECONDS)

                if isinstance(e, LoginFlapError):
                    # аккаунт упал именно флапом токена: оба входа прошли,
                    # но сессию сервер не держит — вероятна серверная проблема
                    flap_accounts += 1
                    if flap_accounts >= SYSTEMIC_FAILURE_THRESHOLD:
                        _trip_service_breaker()
                        logger.warning(
                            f"флап токена на {flap_accounts} аккаунтах подряд — "
                            f"сервис flexy-ai.com системно недоступен"
                        )
                        raise RuntimeError(SERVICE_UNAVAILABLE_MESSAGE) from e
                else:
                    # другая ошибка (сеть, rate-limit) — серия флапов прервана
                    flap_accounts = 0
                # пробуем следующий свободный аккаунт
    finally:
        db.close()
