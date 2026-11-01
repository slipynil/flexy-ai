import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DEFAULT_PROMPT
from src.generation import context_to_image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KrakenBot")


def main():
    parser = argparse.ArgumentParser(description="Кракер бот для FlexyAI")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="запрос для генерации изображения")
    parser.add_argument("--imagepath", required=True, help="путь к изображению")
    args = parser.parse_args()

    logger.info(f"генерация изображения для запроса: {args.prompt}")

    try:
        output_filepath = context_to_image(args.imagepath, args.prompt)
        logger.info(f"изображение сохранено: {output_filepath}")
    except RuntimeError as e:
        logger.error(f"ошибка генерации: {e}")


if __name__ == "__main__":
    main()
