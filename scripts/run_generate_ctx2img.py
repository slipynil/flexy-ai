import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.flexy_ai.api import FlexyAiAPI
from src.repository.sqlite import DB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KrakenBot")


def main():
    parser = argparse.ArgumentParser(description="Кракер бот для FlexyAI")
    parser.add_argument("--prompt", default="раздеть персонажа до гола, лицо и позу не менять", help="запрос для генерации изображения")
    parser.add_argument("--imagepath", required=True, help="путь к изображению")
    args = parser.parse_args()

    logger.info(f"генерация изображения для запроса: {args.prompt}")
    db = DB()
    account = db.get_account()

    if account is None:
        logger.error("нет доступных аккаунтов")
        return

    api_key, email, password = account
    logger.info(f"используем аккаунт: {email}")

    flexy_ai = FlexyAiAPI()

    try:
        flexy_ai.login(email, password)
        flexy_ai.set_api_key(api_key)
        flexy_ai.context_to_image(args.imagepath, args.prompt)
    except (RuntimeError, ValueError) as e:
        logger.error(f"ошибка при работе с API: {e}")
        return
    db.update_account_tries(api_key)
    db.close()





if __name__ == "__main__":
    main()
