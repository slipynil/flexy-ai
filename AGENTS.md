# AGENTS.md

Инструкции для AI-агентов (и людей), работающих с этим репозиторием.

## Что это за проект

**flexy_service** — Telegram-бот для генерации изображений через flexy-ai.com + ферма аккаунтов.
Бот принимает фото от пользователей, генерирует изображение через API flexy-ai.com, используя пул
автоматически зарегистрированных аккаунтов (round-robin + cooldown при rate-limit).
Аккаунты регистрируются скриптом через одноразовую почту temp-mail.org (selenium/undetected-chromedriver).

Подробная архитектура: [docs/c4-model.md](docs/c4-model.md)

## Команды

```bash
mise run bot                              # запуск телеграм-бота
mise run register_accounts -- --counts 5  # регистрация 5 аккаунтов (аргументы после --)
mise run generate_txt2img -- --prompt "..."   # CLI-генерация
mise run generate_ctx2img -- --imagepath f.jpg
mise run install                          # poetry install
./venv/bin/python tests/parse_test.py     # ручные тесты-скрипты (не pytest-сьют)
```

- Окружение: `./venv` (Python 3.12), зависимости — poetry (`pyproject.toml`, `poetry.lock`)
- Задачи описаны в `mise.toml` — это источник правды по запуску
- Скрипты запускаются как модули из корня: `poetry run python -m scripts.run_...`

## Структура

```
scripts/            # точки входа (бот, ферма аккаунтов, CLI-генераторы)
src/flexy_ai/       # HTTP-клиент flexy-ai.com (api.py) и регистрация (registration.py)
src/repository/     # DB — пул аккаунтов в SQLite
src/temp_mail.py    # клиент temp-mail.org (selenium)
src/parse_files.py  # парсинг скрытых полей форм WordPress/ARMember
src/convert_image.py, save_base64_image.py  # работа с изображениями
src/errors/         # доменные исключения
data/data.db        # SQLite (в git не входит, схема мигрирует автоматически)
.env                # BOT_TOKEN (в git не входит), шаблон — .env.example
```

## Критичные правила (не нарушать)

1. **Блокирующие вызовы — только через `asyncio.to_thread`.** Генерация занимает ~30 сек.
   Прямой вызов из async-хендлера замораживает весь бот.

2. **DB потокобезопасна через соединение на вызов.** `get_account()` атомарно захватывает
   аккаунт (`BEGIN IMMEDIATE` + обновление `last_used`) — не выпиливать транзакцию, иначе
   параллельные запросы получат один и тот же аккаунт.

3. **Семантика пула аккаунтов:**
   - `tries` — оставшиеся генерации (decrement после успешной), `tries > 0` — фильтр пула
   - `busy_until` — cooldown при rate-limit API (120 сек, `DB.set_account_busy`)
   - ротация — по `last_used ASC` (round-robin); никогда не выбирать «первый попавшийся»

4. **Секреты — только в `.env`** (читается mise автоматически). Не хардкодить токены,
   не коммитить `.env`. Шаблон — `.env.example`.

5. **Не привязываться к версии chromedriver.** `undetected-chromedriver` сам скачивает
   и патчит драйвер; в `setup_driver()` явно передаются `browser_executable_path` и
   `version_main` (uc сам версии не сверяет — без этого возможен SessionNotCreated).
   Бинарник `chromedriver` в репозитории не нужен (в `.gitignore`).

6. **Уникальные имена файлов** для входящих фото и результатов генерации (`uuid`) —
   запросы обрабатываются параллельно, фиксированные имена приводят к перезаписи.

7. **Скрипты импортируют `src.*`** — сохранять bootstrap `sys.path.insert(0, ...)`
   в начале каждого скрипта из `scripts/`.

8. **Схема БД мигрирует автоматически** (`DB._init_schema`, ALTER TABLE) — не требовать
   пересоздания `data/data.db` при добавлении колонок.

## Известные особенности внешних систем

- **flexy-ai.com** — WordPress с плагином ARMember. Для login/регистрации нужно парсить
  скрытые поля формы (`arm_wp_nonce`, `arm_nonce_check`, безымянное числовое `undefined`)
  из HTML — см. `src/parse_files.py`. Поля/структура формы могут меняться.
- **Rate-limit**: API блокирует аккаунт на 1–2 минуты при частых запросах → cooldown.
- **temp-mail.org** за Cloudflare — работает только через undetected-chromedriver,
  headless-режим палится.
- **Версии chromedriver и браузера**: драйвер новее браузера → `SessionNotCreated`;
  драйвер равный или старше — работает.

## Стиль кода

- Комментарии, логи и docstring — на русском (как в существующем коде)
- Логгеры: `logging.getLogger(__name__)` в модулях, `"KrakenBot"` в скриптах
- Аннотации типов в сигнатурах публичных функций
- Формат данных БД: кортежи `(api_key, email, password)`
