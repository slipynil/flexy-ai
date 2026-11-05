import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.types.input_file import FSInputFile

from src.generation import context_to_image

# Токен берется из окружения: mise загружает его из файла .env (см. .env.example)
TOKEN = os.environ.get("BOT_TOKEN")

logger = logging.getLogger("KrakenBot")

# Диспетчер управляет всеми входящими от пользователя событиями (сообщениями, кнопками)
dp = Dispatcher()

SOURCE_DIR = Path("source")


# Хендлер на команду /start
@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    fullName = "пользователь"
    if message.from_user:
        fullName = message.from_user.full_name
    await message.answer(f"Привет, {fullName}! Я бот для генерации изображений.")


@dp.message(F.photo)
async def photo_handler(message: Message) -> None:
    if message.photo is None:
        await message.answer("Вы не прислали фото, пожалуйста, попробуйте еще раз")
        return

    best_photo = message.photo[-1]
    if message.bot is None:
        return

    # уникальное имя файла, чтобы параллельные запросы не перезаписывали друг друга
    photo_path = SOURCE_DIR / f"{uuid.uuid4().hex}.jpg"
    await message.bot.download(best_photo, destination=photo_path)
    await message.answer("Принял! Генерирую изображение, это займет около минуты... ⏳")

    try:
        # генерация долгая (десятки секунд) — уводим в отдельный поток,
        # чтобы бот продолжал отвечать другим пользователям
        image_filepath = await asyncio.to_thread(context_to_image, str(photo_path))
    except RuntimeError as e:
        await message.answer(f"Не получилось сгенерировать изображение: {e}")
        return
    except Exception:
        # страховка: при любой непредвиденной ошибке отвечаем пользователю,
        # а не оставляем его в ожидании после «Принял! Генерирую…»
        logger.exception("непредвиденная ошибка генерации")
        await message.answer(
            "Не получилось сгенерировать изображение, попробуйте позже"
        )
        return
    finally:
        photo_path.unlink(missing_ok=True)

    photo_to_send = FSInputFile(image_filepath)
    await message.answer_photo(
        photo=photo_to_send,
        caption="Вот ваша сгенерированная картика!"
    )


async def main() -> None:
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан. Создайте файл .env (скопируйте .env.example) "
            "и укажите в нем токен от @BotFather"
        )

    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    bot = Bot(token=TOKEN)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
