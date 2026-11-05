# AGENTS.md

Инструкции для AI-агентов (и людей), работающих с этим репозиторием.

## Что это за проект

**flexy_service** — Telegram-бот для генерации изображений через flexy-ai.com + ферма аккаунтов.
Бот принимает фото от пользователей, генерирует изображение через API flexy-ai.com, используя пул
автоматически зарегистрированных аккаунтов (round-robin + cooldown при rate-limit).
Аккаунты регистрируются скриптом через одноразовую почту temp-mail.org (selenium/undetected-chromedriver).

Подробная архитектура: [docs/c4-model.md](docs/c4-model.md)
Архитектурные решения (ADR): [docs/adr/](docs/adr/README.md)

## Команды

```bash
mise run ui                               # интерактивное меню (бот, генерации, регистрация)
mise run bot                              # запуск телеграм-бота
mise run register_accounts -- --counts 5  # регистрация 5 аккаунтов (аргументы после --)
mise run register_accounts_no_email -- --counts 5  # то же без почты и браузера (ADR-003)
mise run generate_txt2img -- --prompt "..."   # CLI-генерация
mise run generate_ctx2img -- --imagepath f.jpg
mise run install                          # poetry install
./venv/bin/python tests/parse_test.py     # ручные тесты-скрипты (не pytest-сьют)
```

- Окружение: `./venv` (Python 3.12) — **единственное**; задачи mise запускают его напрямую
  (`./venv/bin/python`), `poetry run` не используется: poetry может молча уйти в окружение
  из `~/.cache/pypoetry/virtualenvs` с другими версиями библиотек. Привязка poetry к `./venv`
  — через `VIRTUAL_ENV` в задаче `install` (см. `mise.toml`). Зависимости — poetry
  (`pyproject.toml`, `poetry.lock`)
- Задачи описаны в `mise.toml` — это источник правды по запуску
- Скрипты запускаются как модули из корня: `./venv/bin/python -m scripts.run_...`

## Структура

```
scripts/            # тонкие точки входа (UI-меню, бот, ферма аккаунтов, CLI-генераторы)
src/generation.py   # сервис генерации через пул аккаунтов (ADR-001)
src/farm.py         # ферма регистрации аккаунтов (ADR-001 фаза 2, ADR-002)
src/config.py       # общие константы (DEFAULT_PROMPT, ACCOUNT_COOLDOWN_SECONDS)
src/flexy_ai/       # HTTP-клиент flexy-ai.com (api.py) и регистрация (registration.py)
src/repository/     # DB — пул аккаунтов в SQLite
src/temp_mail.py    # клиент temp-mail.org (selenium)
src/parse_files.py  # парсинг скрытых полей форм WordPress/ARMember
src/convert_image.py, save_base64_image.py  # работа с изображениями
src/errors/         # доменные исключения
data/data.db        # SQLite (в git не входит, схема мигрирует автоматически)
.env                # BOT_TOKEN (в git не входит), шаблон — .env.example
```

## ADR — архитектурные решения

Каталог: `docs/adr/`, процесс и индекс — `docs/adr/README.md`.

- **Перед изменениями сверяйся с ADR** — архитектурное решение могло быть уже принято.
  Следуй принятому решению; не согласен — предлагай изменение через новый ADR, а не молча в коде.
- **Глобальные изменения оформляй новым ADR ДО реализации**: новые внешние системы,
  изменение границ модулей/слоёв, смена ключевых библиотек, изменения схемы БД
  или логики пула аккаунтов, новые точки входа.
- Локальные правки в рамках принятых решений ADR не требуют.
- После реализации: обнови индекс ADR; при изменении структуры — `README.md`
  и `docs/c4-model.md`.

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
   Пропатченный драйвер кэшируется в `~/.local/share/undetected_chromedriver/*_v<major>`:
   uc 3.5.5 перекачивает драйвер при каждом запуске через urllib без таймаутов
   (виснет при плохой сети), поэтому при кэш-промахе драйвер качаем сами через
   `requests` (`src/farm.py::_download_driver`), а uc передаем готовый бинарник.
   Бинарник `chromedriver` в репозитории не нужен (в `.gitignore`).

6. **Уникальные имена файлов** для входящих фото и результатов генерации (`uuid`) —
   запросы обрабатываются параллельно, фиксированные имена приводят к перезаписи.

7. **Скрипты импортируют `src.*`** — сохранять bootstrap `sys.path.insert(0, ...)`
   в начале каждого скрипта из `scripts/`.

8. **Схема БД мигрирует автоматически** (`DB._init_schema`, ALTER TABLE) — не требовать
   пересоздания `data/data.db` при добавлении колонок.

9. **Нельзя изменять код библиотек вне `src/` и локальной области видимости.**
   Запрещено: править файлы в `site-packages`, вендорить библиотеки, делать постоянный
   monkeypatching (перезапись атрибутов чужих модулей, глобальных объектов типа
   `subprocess.Popen` и т.п.).
   Разрешено: конфигурация через публичные параметры библиотеки и **временные** замены
   строго внутри функции/контекстного менеджера — с комментарием-обоснованием
   (какой дефект обходится, почему без этого нельзя). Если обход не получается
   сделать локальным — оформлять отдельный ADR.

## Известные особенности внешних систем

- **flexy-ai.com восстановлен после кибератаки на Beget (11.2026) из бэкапа на
  техническом домене** (`*.tw1.ru`, видно в referral_url форм). Следствия:
  - **API генерации переехал на `https://api.flexyai.ru`** (в api.py —
    `GENERATION_API_BASE_URL`; сайт задает его в inline-скрипте
    `window.flexyApiUrl`). Старый путь `flexy-ai.com/api/*` отвечает 302 на
    главную. Формат X-API-Key `mail:user_token` прежний.
  - **Формы ARMember стали динамическими**: arm_action теперь `please-signup` /
    `please-login` (старые транслит-экшены не существуют — сервер отвечал
    «success» без создания сессии, отсюда был `logged_in: False`); ключи форм
    per-render, дефолты протухли. Поля парсим со страницы:
    `parse_files.get_arm_form_fields` (форма по составу полей, не по классам),
    `registration.reinit_forms` + `form_random_id` + `reinit_key_for`.
  - **Анти-бот сабмита форм**: payload должен повторять arm_form_ajax_action
    (arm_common.js): `form_random_key` = data-random-id формы (101_/102_),
    обязательны `form_filter_st` (таймстемп рендера со страницы) и
    `form_filter_kp`, honeypot `arm_filter_input` НЕ отправляется,
    8-символьный ключ reinit — имя обязательного пустого поля. Сабмит раньше
    ~10 сек с загрузки страницы отклоняется («Обнаружен спам») —
    `ARM_FORM_FILL_DELAY_SECONDS` в config.py дозасыпает остаток.
  - **«Обнаружен спам» на входе флапает** (то пускает, то нет) —
    `_login_user` бросает `LoginFlapError`, вызывающий код ретраит вход
    с новой страницей (`generation._login_with_retry`).
  - Часы сервера ~4 часа впереди локальных: не сравнивать `stime` со
    `time.time()` — только передавать как есть.
- **flexy-ai.com** — WordPress с плагином ARMember. Для login/регистрации нужно парсить
  скрытые поля формы (`arm_wp_nonce`, `arm_nonce_check`, безымянное числовое `undefined`)
  из HTML — см. `src/parse_files.py`. Поля/структура формы могут меняться.
- **flexy-ai.com: ответы admin-ajax непостоянны.** При сбойном входе или срабатывании
  анти-бота хостинга (beget) вместо JSON приходит HTML, а поле `data` в JSON может быть
  строкой. Любой `.get()` по такому ответу без isinstance-проверки падает с
  `'str' object has no attribute 'get'`. Парсинг ответов — только через
  `FlexyAiAPI._post_ajax` + `_data_dict` (проверяют форму и дают RuntimeError с телом
  ответа). Вход/токен флапают — `verify_and_get_api_key` повторяет вход обычным логином.
- **Rate-limit**: API блокирует аккаунт на 1–2 минуты при частых запросах → cooldown.
- **temp-mail.org** за Cloudflare — работает только через undetected-chromedriver,
  headless-режим палится.
- **uc + stderr Chrome**: uc запускает Chrome с `stderr=PIPE` и никогда не читает его,
  при этом добавляет Chrome `--log-level=0` (болтливый). Переполнение буфера пайпа
  (~64 КБ) замораживает Chrome → сессия падает с `'str' object has no attribute 'get'`
  через ~30 сек (интермитент, зависит от окружения). Обход — запуск uc только через
  `src/farm.py::_launch_uc`: контекстный менеджер `uc_launch` временно подменяет
  `uc.start_detached` на `_start_chrome_detached` (stderr — в файл `data/chrome-logs/`,
  а не PIPE) и восстанавливает оригинал. Патч строго локальный (правило 9), не выносить
  на уровень модуля.
- **Сбой запуска Chrome = `'str' object has no attribute 'get'`**: если Chrome не поднял
  DevTools-порт за ~30 сек (завис на старте — транзиент: compositor, GPU, загрузка),
  chromedriver возвращает ошибку, где `value` — строка, и selenium падает в `start_session`
  с этой малопонятной ошибкой. Диагностика: traceback в логах (exc_info), хвост лога Chrome
  в ошибке (`data/chrome-logs/`), журнал `journalctl --user`.
- **Версии chromedriver и браузера**: драйвер новее браузера → `SessionNotCreated`;
  драйвер равный или старше — работает.

## Стиль кода

- Комментарии, логи и docstring — на русском (как в существующем коде)
- Логгеры: `logging.getLogger(__name__)` в модулях, `"KrakenBot"` в скриптах
- Аннотации типов в сигнатурах публичных функций
- Формат данных БД: кортежи `(api_key, email, password)`
