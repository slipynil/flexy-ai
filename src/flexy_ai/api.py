
import logging
import time
import uuid

import requests
from requests.exceptions import RequestException

from src.config import (
    ARM_FORM_FILL_DELAY_SECONDS,
    HTTP_CONNECT_TIMEOUT,
    TASK_POLL_ERROR_ATTEMPTS,
    TASK_POLL_INTERVAL_SECONDS,
    TASK_POLL_TIMEOUT_SECONDS,
)
from src.convert_image import convert_image_to_base64 as convert_image
from src.errors.api_errors import LoginFlapError
from src.flexy_ai.registration import form_random_id, reinit_forms, reinit_key_for
from src.parse_files import get_arm_form_fields
from src.save_base64_image import save_base64_image

API_BASE_URL = "https://flexy-ai.com"

# API генерации переехал после инцидента на хостинге Beget (ноябрь 2026) на
# отдельный домен: сайт задает его в inline-скрипте страниц
# (window.flexyApiUrl = "https://api.flexyai.ru"). Старый путь
# flexy-ai.com/api/* теперь отвечает 302 на главную
GENERATION_API_BASE_URL = "https://api.flexyai.ru"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ru-RU,ru;q=0.8,en-US;q=0.5,en;q=0.3",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",  # Сразу делаем стандартным для сессии
    "Origin": f"{API_BASE_URL}",
}



class FlexyAiAPI:
    def __init__(self):
        self.session = requests.session()
        self.session.headers.update(HEADERS)
        self.logger = logging.getLogger(__name__)

    def set_api_key(self, api_key: str) -> None:
        self.session.headers.update({"X-API-Key": api_key})

    def _post_ajax(self, referer: str, payload: dict) -> dict:
        self.session.headers.update({"Referer": referer})
        response = self.session.post(
            f"{API_BASE_URL}/wp-admin/admin-ajax.php", data=payload,
            timeout=(HTTP_CONNECT_TIMEOUT, 30),
        )

        if not response.ok:
            raise RuntimeError(f"сервер вернул ошибку {response.status_code}: {response.text[:200]}")

        try:
            data = response.json()
        except ValueError:
            # без входа / при срабатывании анти-бота хостинга приходит HTML
            raise RuntimeError(f"сервер вернул не JSON: {response.text[:200]}")

        if not isinstance(data, dict):
            # admin-ajax иногда отвечает голой JSON-строкой — .get() на ней
            # и ронял все с "'str' object has no attribute 'get'"
            raise RuntimeError(f"сервер вернул неожиданный JSON: {str(data)[:200]}")
        return data

    @staticmethod
    def _data_dict(data: dict) -> dict:
        """Достает вложенное поле "data", гарантируя dict.

        При ошибках flexy-ai кладет в "data" строку — .get() на ней падал
        с "'str' object has no attribute 'get'". Теперь вместо краша —
        RuntimeError с телом ответа.
        """
        value = data.get("data", {})
        if not isinstance(value, dict):
            raise RuntimeError(f"неожиданный формат ответа (data — не словарь): {str(data)[:300]}")
        return value

    def text_to_image(self, prompt: str) -> dict | None:
        if not self.session.headers.get("X-API-Key"):
            raise ValueError("Не задан API-ключ (X-API-KEY) header.")

        self.session.headers.update({
            "Content-Type": "application/json",
            "Referer": f"{API_BASE_URL}/txt2img",
        })

        payload = {
            "prompt": prompt, "width": 1024, "height": 1024, "style": "",
            "model": "Turbo", "tiling": False, "sfw": False
        }

        url = f"{GENERATION_API_BASE_URL}/api/txt2img"
        response = self.session.post(
            url, json=payload, timeout=(HTTP_CONNECT_TIMEOUT, 60)
        )
        if not response.ok:
            raise RuntimeError(f"Ошибка генерации {response.status_code}: {response.text}")

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"неожиданный ответ генерации: {str(data)[:200]}")
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"Не удалось запустить генерацию. Ответ: {str(data)[:300]}")

        self.logger.info(f"Задача создана. ID: {task_id}. Ожидание...")

        return self._poll_image_result(task_id)


    def context_to_image(self, image_path: str, prompt: str) -> str:
        if not self.session.headers.get("X-API-Key"):
            raise ValueError("Не задан API-ключ (X-API-KEY) header.")

        self.session.headers.update({
            "Content-Type": "application/json",
            "Referer": f"{API_BASE_URL}/context2img",
        })

        image_base64, width, height = convert_image(image_path)
        payload = {
            "prompt": prompt, "width": width, "height": height,
            "style": "", "swf": "", "context_images": [image_base64]
        }
        url = f"{GENERATION_API_BASE_URL}/api/context2img"
        response = self.session.post(
            url, json=payload, timeout=(HTTP_CONNECT_TIMEOUT, 120)
        )
        if not response.ok:
            raise RuntimeError(f"Ошибка генерации {response.status_code}: {response.text}")

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"неожиданный ответ генерации: {str(data)[:200]}")
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"Не удалось запустить генерацию. Ответ: {str(data)[:300]}")

        return self._poll_image_result(task_id)

    def _poll_image_result(self, task_id: str) -> str:
        url = f"{GENERATION_API_BASE_URL}/api/status/{task_id}"
        # дедлайн обязателен: при деградации хостинга статус может «висеть»
        # в processing бесконечно — раньше while True крутился вечно
        deadline = time.monotonic() + TASK_POLL_TIMEOUT_SECONDS
        poll_errors = 0

        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"генерация не завершилась за {TASK_POLL_TIMEOUT_SECONDS} сек "
                    f"(task_id={task_id})"
                )
            time.sleep(TASK_POLL_INTERVAL_SECONDS)

            try:
                response = self.session.get(url, timeout=(HTTP_CONNECT_TIMEOUT, 30))
                if not response.ok:
                    raise RuntimeError(
                        f"опрос статуса: сервер вернул {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                status_data = response.json()
            except (RequestException, ValueError, RuntimeError) as e:
                # транзиентные сбои (сброс соединения, 5xx, HTML вместо JSON
                # при перегрузке) не губим попытку — терпим несколько подряд
                poll_errors += 1
                if poll_errors >= TASK_POLL_ERROR_ATTEMPTS:
                    raise RuntimeError(
                        f"опрос статуса не удается {poll_errors} раз подряд: {e}"
                    ) from e
                self.logger.warning(
                    f"опрос статуса не удался ({poll_errors}/{TASK_POLL_ERROR_ATTEMPTS}): {e}"
                )
                continue
            poll_errors = 0

            if not isinstance(status_data, dict):
                raise RuntimeError(f"неожиданный ответ статуса: {str(status_data)[:200]}")

            status = status_data.get("status")
            progress = status_data.get("progress", 0)

            self.logger.debug(f"Статус: {status} | Прогресс: {progress}%")

            if status == "completed":
                images = status_data.get("images", [])
                # uuid в имени — чтобы параллельные генерации не перезаписали друг друга
                filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
                return save_base64_image(images[0], output_filename=filename)

            if status == "failed" or status_data.get("error"):
                raise RuntimeError(f"Ошибка при генерации изображения: {status_data}")


    def login(self, login: str, password: str) -> str | None:
        # timeout обязателен: хостинг иногда не сбрасывает, а подвешивает
        # соединение — без таймаута поток генерации висит бесконечно
        started = time.monotonic()
        response = self.session.get(f"{API_BASE_URL}/login", timeout=(HTTP_CONNECT_TIMEOUT, 30))
        fields = _parse_login_form(response)
        keys = self._login_submit_keys(response.text)
        _sleep_to_fill_delay(started)
        self._login_user(login, password, fields, *keys)
        return self._get_user_token()


    def verification_and_login(self, *, verification_link, login, password) -> str:
        started = time.monotonic()
        response = self.session.get(
            verification_link, timeout=(HTTP_CONNECT_TIMEOUT, 30)
        )
        if not response.ok:
            raise RuntimeError(f"Верификация не удалась: {response.status_code}")

        fields = _parse_login_form(response)
        keys = self._login_submit_keys(response.text)
        _sleep_to_fill_delay(started)
        self._login_user(login, password, fields, *keys)
        return self._get_user_token()

    def _login_submit_keys(self, page_html: str) -> tuple[str, str]:
        """Ключи анти-бота ARMember для формы входа (префикс 102).

        Возвращает (data-random-id, 8-символьный ключ reinit) — оба обязательны
        в payload, иначе ARMember отвечает «Обнаружен спам».
        """
        reinit = reinit_forms(self.session, page_html)
        if not reinit:
            raise RuntimeError("reinit форм не удался (структура сайта изменилась?)")
        full_key = form_random_id(page_html, "102")
        key8 = reinit_key_for(reinit, "102")
        if not full_key or not key8:
            raise RuntimeError("не найдены ключи формы входа (структура сайта изменилась?)")
        return full_key, key8

    def _get_user_token(self) -> str:
        data = self._post_ajax("https://flexy-ai.com/profile", { "action": "flexy_get_user_profile_data" })
        token = self._data_dict(data).get("user_token")
        if not token:
            # флап анти-бота хостинга: {'success': True, 'data': {'logged_in': False}}
            # лечится повторным входом, поэтому отдельный тип ошибки
            raise LoginFlapError(f"в ответе нет user_token (вход не выполнен?): {str(data)[:300]}")
        return token

    def _create_api_key(self) -> str:
        data = self._post_ajax("https://flexy-ai.com/profile", { "action": "flexy_generate_api_key" })
        api_key = self._data_dict(data).get("api_key")
        if not api_key:
            raise RuntimeError(f"не удалось получить API-ключ: {str(data)[:300]}")
        return api_key

    def _login_user(self, login, password, fields: dict, form_random_key_full: str, reinit_key8: str):
        # payload повторяет arm_form_ajax_action (arm_common.js) для формы входа:
        # form_random_key = data-random-id формы, honeypot arm_filter_input не
        # отправляется, stime/kpress и переименованный reinit-инпут обязательны —
        # без них ARMember после восстановления хостинга отвечает «Обнаружен спам»
        payload: dict = dict(fields)
        st_time = payload.pop("undefined", "")
        payload.pop("arm_filter_input", None)
        payload.update({
            "action": "arm_shortcode_form_ajax_action",
            "user_login": login,
            "user_pass": password,
            "form_random_key": form_random_key_full,
            "form_filter_st": st_time,
            "form_filter_kp": "5",
        })
        payload[reinit_key8] = ""
        data = self._post_ajax("https://flexy-ai.com/wp-admin/admin-ajax.php", payload)
        # раньше ответ не проверялся: "вход выполнен успешно" печатался всегда,
        # а фактический провал всплывал позже на получении токена малопонятной ошибкой
        if data.get("status") != "success":
            message = str(data)
            if "спам" in message or "spam" in message.lower():
                # анти-бот хостинга флапает (то пускает, то нет) — повторный
                # вход с новой страницей часто проходит, поэтому LoginFlapError
                # (ретраится вызывающим кодом, см. generation._login_with_retry)
                raise LoginFlapError(f"анти-бот отклонил вход: {message[:200]}")
            raise RuntimeError(f"вход не выполнен: {message[:300]}")
        self.logger.info("вход выполнен успешно.")

def _sleep_to_fill_delay(started: float) -> None:
    """Дозасыпает до минимального «времени заполнения» формы ARMember.

    После восстановления хостинга anti-бот отклоняет сабмиты, отправленные
    раньше ARM_FORM_FILL_DELAY_SECONDS с момента загрузки страницы
    («Обнаружен спам») — имитируем человека, печатающего логин и пароль.
    """
    remaining = ARM_FORM_FILL_DELAY_SECONDS - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)


def _parse_login_form(r: requests.Response) -> dict:
    """Поля формы входа со страницы (см. get_arm_form_fields)."""
    r.encoding = 'utf-8'
    return get_arm_form_fields(r.text, login_form=True)
