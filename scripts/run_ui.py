"""Единое интерактивное CLI-приложение (см. docs/adr/002-unified-cli.md).

Меню для запуска всех функций проекта: телеграм-бот, генерации,
регистрация аккаунтов. Запуск: mise run ui.

UI — только представление: доменная логика живет в src/.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import questionary
from questionary import Style
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from src.config import DEFAULT_PROMPT
from src.farm import register_accounts, register_accounts_no_email
from src.generation import context_to_image, text_to_image
from src.repository.sqlite import DB

console = Console()

CUSTOM_STYLE = Style([
    ("qmark", "fg:cyan bold"),
    ("question", "bold"),
    ("answer", "fg:cyan bold"),
    ("pointer", "fg:cyan bold"),
    ("highlighted", "fg:cyan bold"),
    ("selected", "fg:green"),
])

MENU_BOT = "🤖 Запустить Telegram-бота"
MENU_TXT2IMG = "📝 Генерация: текст → изображение"
MENU_CTX2IMG = "🖼️  Генерация: изображение → изображение"
MENU_REGISTER = "👥 Регистрация аккаунтов"
MENU_REGISTER_NO_EMAIL = "👤 Регистрация аккаунтов (без почты)"
MENU_POOL_STATS = "📊 Пул аккаунтов: доступные и попытки"
MENU_CLEAR_POOL = "🗑️ Очистить пул аккаунтов"
MENU_EXIT = "🚪 Выход"


def show_banner() -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[bold cyan]flexy_service[/bold cyan] — генерация изображений через flexy-ai.com")
    table.add_row("[dim]телеграм-бот · ферма аккаунтов · CLI-генерации[/dim]")
    console.print(Panel(table, border_style="cyan"))


def show_success(title: str, details: dict[str, str] | None = None) -> None:
    body = ""
    if details:
        body = "\n".join(f"[bold]{k}:[/bold] {v}" for k, v in details.items())
    console.print(Panel(body, title=f"✅ {title}", border_style="green"))


def show_error(message: str) -> None:
    console.print(Panel(message, title="❌ Ошибка", border_style="red"))


def run_bot_menu() -> None:
    """Запускает телеграм-бота в этом процессе. Ctrl+C — возврат в меню."""
    from scripts.run_bot import main as run_bot_main

    console.print(Panel(
        "Бот запускается... Остановить: [bold]Ctrl+C[/bold] (вернешься в меню)",
        border_style="cyan",
    ))
    try:
        asyncio.run(run_bot_main())
    except RuntimeError as e:
        show_error(str(e))
    except KeyboardInterrupt:
        pass
    show_success("Бот остановлен")


def txt2img_menu() -> None:
    prompt = questionary.text(
        "Промпт для генерации:",
        style=CUSTOM_STYLE,
    ).ask()
    if not prompt:
        return

    try:
        with console.status("[cyan]Генерация изображения (это займет ~минуту)...", spinner="dots"):
            output_filepath = text_to_image(prompt)
    except RuntimeError as e:
        show_error(str(e))
        return

    show_success("Изображение готово", {"Промпт": prompt, "Файл": output_filepath})


def ctx2img_menu() -> None:
    image_path = questionary.path(
        "Путь к изображению:",
        style=CUSTOM_STYLE,
        validate=lambda p: True if p and os.path.exists(p) else "Файл не найден",
    ).ask()
    if not image_path:
        return

    prompt = questionary.text(
        "Промпт (Enter — промпт по умолчанию):",
        default=DEFAULT_PROMPT,
        style=CUSTOM_STYLE,
    ).ask()
    if prompt is None:
        return

    try:
        with console.status("[cyan]Генерация изображения (это займет ~минуту)...", spinner="dots"):
            output_filepath = context_to_image(image_path, prompt)
    except RuntimeError as e:
        show_error(str(e))
        return

    show_success("Изображение готово", {"Исходник": image_path, "Файл": output_filepath})


def register_menu() -> None:
    raw = questionary.text(
        "Сколько аккаунтов зарегистрировать:",
        default="1",
        style=CUSTOM_STYLE,
        validate=lambda v: True if v.isdigit() and int(v) > 0 else "Введите положительное число",
    ).ask()
    if not raw:
        return

    count = int(raw)
    console.print(Panel(
        "Регистрация идет в браузере... Каждая попытка открывает temp-mail.org.\n"
        "Прервать: [bold]Ctrl+C[/bold]",
        border_style="cyan",
    ))

    try:
        registered = register_accounts(count)
    except KeyboardInterrupt:
        show_error("Регистрация прервана пользователем")
        return
    except Exception as e:
        show_error(f"Регистрация упала: {e}")
        return

    if registered == count:
        show_success("Регистрация завершена", {"Зарегистрировано": f"{registered} из {count}"})
    else:
        show_error(f"Зарегистрировано только {registered} из {count} — смотри логи выше")


def register_no_email_menu() -> None:
    """Регистрация без подтверждения почты (ADR-003): без temp-mail и браузера."""
    raw = questionary.text(
        "Сколько аккаунтов зарегистрировать:",
        default="1",
        style=CUSTOM_STYLE,
        validate=lambda v: True if v.isdigit() and int(v) > 0 else "Введите положительное число",
    ).ask()
    if not raw:
        return

    count = int(raw)
    console.print(Panel(
        "Регистрация без браузера: случайная почта, подтверждение не требуется (ADR-003).\n"
        "Прервать: [bold]Ctrl+C[/bold]",
        border_style="cyan",
    ))

    try:
        registered = register_accounts_no_email(count)
    except KeyboardInterrupt:
        show_error("Регистрация прервана пользователем")
        return
    except Exception as e:
        show_error(f"Регистрация упала: {e}")
        return

    if registered == count:
        show_success("Регистрация завершена", {"Зарегистрировано": f"{registered} из {count}"})
    else:
        show_error(f"Зарегистрировано только {registered} из {count} — смотри логи выше")


def clear_pool_menu() -> None:
    """Удаляет все аккаунты из пула (утилизация мёртвых, ADR-003)."""
    db = DB()
    try:
        total = db.get_pool_stats()["total"]
    finally:
        db.close()

    if total == 0:
        console.print(Panel("Пул и так пуст", border_style="yellow"))
        return

    confirmed = questionary.confirm(
        f"Удалить все {total} аккаунтов из пула? Это необратимо.",
        style=CUSTOM_STYLE,
    ).ask()
    if not confirmed:
        return

    db = DB()
    try:
        deleted = db.clear_accounts()
    finally:
        db.close()
    show_success("Пул очищен", {"Удалено аккаунтов": str(deleted)})


def pool_stats_menu() -> None:
    """Показывает статистику пула: доступные аккаунты и попытки суммарно."""
    db = DB()
    try:
        stats = db.get_pool_stats()
    finally:
        db.close()

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("Доступные аккаунты", f"[green]{stats['available']}[/green]")
    table.add_row("— в cooldown", f"[yellow]{stats['busy']}[/yellow]")
    table.add_row("— исчерпаны (попыток нет)", f"[dim]{stats['exhausted']}[/dim]")
    table.add_row("Всего аккаунтов", str(stats["total"]))
    table.add_row("Попыток суммарно (доступные)", f"[green]{stats['tries_available']}[/green]")
    table.add_row("Попыток суммарно (все)", str(stats["tries_total"]))
    console.print(Panel(table, title="👥 Пул аккаунтов", border_style="cyan"))


ACTIONS = {
    MENU_BOT: run_bot_menu,
    MENU_TXT2IMG: txt2img_menu,
    MENU_CTX2IMG: ctx2img_menu,
    MENU_REGISTER: register_menu,
    MENU_REGISTER_NO_EMAIL: register_no_email_menu,
    MENU_POOL_STATS: pool_stats_menu,
    MENU_CLEAR_POOL: clear_pool_menu,
}


def main() -> None:
    # логи farm/generation/selenium — через rich, чтобы не ломали меню
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )

    # меню требует интерактивный терминал (стрелки, ввод) — в неинтерактивном
    # окружении (CI, пайпы) подсказываем воспользоваться mise-задачами
    if not sys.stdin.isatty():
        console.print(Panel(
            "Интерактивное меню требует терминала.\n"
            "Запустите [bold cyan]mise run ui[/bold cyan] в консоли\n"
            "или используйте задачи: [bold]bot[/bold], [bold]generate_txt2img[/bold], "
            "[bold]generate_ctx2img[/bold], [bold]register_accounts[/bold], "
            "[bold]register_accounts_no_email[/bold]",
            title="ℹ️  Нет интерактивного терминала",
            border_style="yellow",
        ))
        return

    console.clear()
    show_banner()

    while True:
        choice = questionary.select(
            "Что делаем?",
            choices=[*ACTIONS.keys(), questionary.Separator(), MENU_EXIT],
            style=CUSTOM_STYLE,
        ).ask()

        # Ctrl+C / Esc в меню
        if choice is None or choice == MENU_EXIT:
            console.print("[cyan]Пока! 👋[/cyan]")
            return

        console.print()
        ACTIONS[choice]()


if __name__ == "__main__":
    main()
