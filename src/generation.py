"""Сервис генерации изображений через пул аккаунтов (ADR-001).

Инкапсулирует единый для бота и CLI-скриптов сценарий:
захват свободного аккаунта -> login -> генерация -> списание tries.
При ошибке API аккаунт уходит в cooldown и берется следующий,
пока не закончатся все свободные.

Все функции блокирующие: вызывать из потоков (asyncio.to_thread).
"""

import logging

from src.config import ACCOUNT_COOLDOWN_SECONDS, DEFAULT_PROMPT
from src.flexy_ai.api import FlexyAiAPI
from src.repository.sqlite import DB

logger = logging.getLogger(__name__)


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


def _generate_with_pool(generate) -> str:
    """Выполняет generate(api), перебирая свободные аккаунты.

    Если API заблокировал аккаунт (rate-limit), тот уходит в cooldown,
    и автоматически берется следующий — пока не закончатся все.
    """
    db = DB()
    try:
        while True:
            account = db.get_account()
            if account is None:
                raise RuntimeError(
                    "нет свободных аккаунтов: все заблокированы или исчерпаны, попробуйте позже"
                )

            api_key, email, password = account
            logger.info(f"генерация через аккаунт {email}")

            api = FlexyAiAPI()
            try:
                api.login(email, password)
                api.set_api_key(api_key)
                output_filepath = generate(api)
                db.update_account_tries(api_key)
                logger.info(f"генерация через {email} успешна: {output_filepath}")
                return output_filepath
            except (RuntimeError, ValueError) as e:
                logger.warning(f"аккаунт {email} недоступен, уходит в cooldown: {e}")
                db.set_account_busy(api_key, ACCOUNT_COOLDOWN_SECONDS)
                # пробуем следующий свободный аккаунт
    finally:
        db.close()
