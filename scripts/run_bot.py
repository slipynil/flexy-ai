import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from aiogram.types.input_file import FSInputFile

from src.config import DEFAULT_PROMPT
from src.generation import context_to_image

# Токен берется из окружения: mise загружает его из файла .env (см. .env.example)
TOKEN = os.environ.get("BOT_TOKEN")

logger = logging.getLogger("KrakenBot")

# Диспетчер управляет всеми входящими от пользователя событиями (сообщениями, кнопками)
dp = Dispatcher()

SOURCE_DIR = Path("source")


# Состояния режима /context: пользователь присылает фото и описывает,
# что именно нужно изменить на изображении
class ContextFlow(StatesGroup):
    waiting_photo = State()  # ожидаем фото (описание можно дать сразу подписью)
    waiting_description = State()  # фото получено, ожидаем описание сообщением


# Справка по боту — общий текст для /start и /help
HELP_TEXT = (
    "Я умею изменять фотографии. Как мной пользоваться:\n"
    "\n"
    "📸 Просто пришли фото — изменю его стандартным образом.\n"
    "\n"
    "✏️ /context — изменить фото по твоему описанию:\n"
    "1. Отправь команду /context\n"
    "2. Пришли фото и подписью к нему опиши, что изменить\n"
    "   (или сначала фото, а описание — следующим сообщением)\n"
    "3. Дождись результата — генерация занимает около минуты\n"
    "\n"
    "❌ /cancel — отменить режим /context\n"
    "ℹ️ /help — показать эту справку"
)


# Хендлер на команду /start: приветствие + справка
@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    fullName = "пользователь"
    if message.from_user:
        fullName = message.from_user.full_name
    await message.answer(f"Привет, {fullName}!\n\n{HELP_TEXT}")


# Хендлер на команду /help: описание всех команд
@dp.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    await message.answer(HELP_TEXT)


# Хендлер на команду /context: вход в режим изменения фото по описанию
@dp.message(Command("context"))
async def command_context_handler(message: Message, state: FSMContext) -> None:
    # если пользователь уже был в режиме — выбрасываем прежнее фото
    await _drop_stored_photo(state)
    await state.set_state(ContextFlow.waiting_photo)
    await message.answer(
        "Режим изменения фото по описанию.\n"
        "Пришли фото и подпиши, что в нем изменить "
        "(или фото сейчас, а описание — следующим сообщением).\n"
        "Отменить — /cancel"
    )


# Хендлер на команду /cancel: выход из режима /context
@dp.message(Command("cancel"))
async def command_cancel_handler(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять — вы не в режиме /context.")
        return
    await _drop_stored_photo(state)
    await state.clear()
    await message.answer("Режим /context отменен.")


# Фото в режиме /context. Важно: хендлер зарегистрирован ВЫШЕ обычного
# photo_handler, иначе обычный (без state-фильтра) перехватит сообщение.
# Фото с подписью — сразу генерация, без подписи — просим описание.
# В состоянии waiting_description новое фото заменяет прежнее.
@dp.message(StateFilter(ContextFlow.waiting_photo, ContextFlow.waiting_description), F.photo)
async def context_photo_handler(message: Message, state: FSMContext) -> None:
    await _drop_stored_photo(state)

    photo_path = await _download_photo(message)
    if photo_path is None:
        await message.answer("Не получилось скачать фото, попробуйте еще раз")
        return

    prompt = (message.caption or "").strip()
    if prompt:
        # описание дано подписью к фото — можно генерировать
        await state.clear()
        await _generate_and_reply(message, photo_path, prompt)
        return

    # описания нет — ждем его следующим сообщением
    await state.update_data(photo_path=str(photo_path))
    await state.set_state(ContextFlow.waiting_description)
    await message.answer("Фото принял. Теперь опиши, что изменить на изображении.")


# Текст в состоянии waiting_photo: описание прислали раньше фото
@dp.message(ContextFlow.waiting_photo, F.text)
async def context_text_handler(message: Message) -> None:
    await message.answer(
        "Сначала пришли фото. Описание можно дать подписью к нему "
        "или следующим сообщением."
    )


# Описание в состоянии waiting_description: запускаем генерацию
@dp.message(ContextFlow.waiting_description, F.text)
async def context_description_handler(message: Message, state: FSMContext) -> None:
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Описание пустое — напиши, что изменить на фото.")
        return

    data = await state.get_data()
    photo_path = data.get("photo_path")
    if not photo_path:
        # фото потерялось (например, перезапуск бота — FSM-хранилище в памяти) — начинаем заново
        await state.set_state(ContextFlow.waiting_photo)
        await message.answer("Фото не найдено. Пришли фото заново, можно сразу с подписью.")
        return

    await state.clear()
    await _generate_and_reply(message, Path(photo_path), prompt)


# Фото вне режима /context — генерация со стандартным промптом
@dp.message(F.photo)
async def photo_handler(message: Message) -> None:
    photo_path = await _download_photo(message)
    if photo_path is None:
        await message.answer("Вы не прислали фото, пожалуйста, попробуйте еще раз")
        return

    await _generate_and_reply(message, photo_path, DEFAULT_PROMPT)


async def _download_photo(message: Message) -> Path | None:
    """Скачивает самое крупное фото сообщения под уникальным именем.

    Имя уникально (uuid): параллельные запросы не должны
    перезаписывать файлы друг друга.
    """
    if message.photo is None or message.bot is None:
        return None

    best_photo = message.photo[-1]
    photo_path = SOURCE_DIR / f"{uuid.uuid4().hex}.jpg"
    await message.bot.download(best_photo, destination=photo_path)
    return photo_path


async def _drop_stored_photo(state: FSMContext) -> None:
    """Удаляет временный файл фото, сохраненный в состоянии.

    Вызывается при отмене/перезапуске режима /context и при замене фото,
    чтобы файлы не копились в source/.
    """
    data = await state.get_data()
    photo_path = data.get("photo_path")
    if photo_path:
        Path(photo_path).unlink(missing_ok=True)


async def _generate_and_reply(message: Message, photo_path: Path, prompt: str) -> None:
    """Запускает генерацию по фото с промптом, отвечает пользователю результатом.

    Входное фото удаляется в любом случае.
    """
    await message.answer("Принял! Генерирую изображение, это займет около минуты... ⏳")

    try:
        # генерация долгая (десятки секунд) — уводим в отдельный поток,
        # чтобы бот продолжал отвечать другим пользователям
        image_filepath = await asyncio.to_thread(context_to_image, str(photo_path), prompt)
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
        caption="Вот ваша сгенерированная картинка!"
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
