
import logging
import time
import uuid

import requests

from src.convert_image import convert_image_to_base64 as convert_image
from src.parse_files import get_undefined_and_arm_nonce_check_inputs
from src.save_base64_image import save_base64_image

API_BASE_URL = "https://flexy-ai.com"

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
        response = self.session.post(f"{API_BASE_URL}/wp-admin/admin-ajax.php", data=payload)

        if not response.ok:
            raise RuntimeError(f"сервер вернул ошибку {response.status_code}: {response.text}")

        try:
            return response.json()
        except ValueError:
            raise RuntimeError(f"сервер вернул не JSON: {response.text}")

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

        url = f"{API_BASE_URL}/api/txt2img"
        response = self.session.post(url, json=payload)
        if not response.ok:
            raise RuntimeError(f"Ошибка генерации {response.status_code}: {response.text}")

        data = response.json()
        task_id = data.get("task_id")
        if not task_id:
            raise RuntimeError(f"Не удалось запустить генерацию. Ответ: {data}")

        self.logger.info(f"Задача создана. ID: {task_id}. Ожидание...")

        self._poll_image_result(task_id)


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
        url = f"{API_BASE_URL}/api/context2img"
        response = self.session.post(url, json=payload)
        if not response.ok:
            raise RuntimeError(f"Ошибка генерации {response.status_code}: {response.text}")

        data = response.json()
        task_id = data.get("task_id")

        return self._poll_image_result(task_id)

    def _poll_image_result(self, task_id: str) -> str:
        url = f"{API_BASE_URL}/api/status/{task_id}"

        while True:
            time.sleep(2)
            status_data = self.session.get(url).json()

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
        response = self.session.get(f"{API_BASE_URL}/login")
        undefined, arm_nonce_check = _parse_login_html(response)
        self._login_user(login, password, undefined, arm_nonce_check)
        return self._get_user_token()


    def verification_and_login(self, *, verification_link, login, password) -> str:
        response = self.session.get(verification_link)
        if not response.ok:
            raise RuntimeError(f"Верификация не удалась: {response.status_code}")

        undefined, arm_nonce_check = _parse_login_html(response)
        self._login_user(login, password, undefined, arm_nonce_check)
        return self._get_user_token()

    def _get_user_token(self) -> str:
        data = self._post_ajax("https://flexy-ai.com/profile", { "action": "flexy_get_user_profile_data" })
        token = data.get("data", {}).get("user_token")
        if not token:
            raise RuntimeError("не удалось получить токен")
        return token

    def _create_api_key(self) -> str:
        data = self._post_ajax("https://flexy-ai.com/profile", { "action": "flexy_generate_api_key" })
        api_key = data.get("data", {}).get("api_key")
        if not api_key:
            raise RuntimeError("не удалось получить API-ключ")
        return api_key

    def _login_user(self, login, password, undefined, arm_nonce_check):
        payload = {
            "action": "arm_shortcode_form_ajax_action",
            "user_login": login,
            "user_pass": password,
            "arm_action": "pozhalujsta-vojdite-v-sistemu",
            "redirect_to": "https://flexy-ai.com",
            "isadmin": 0,
            "referral_url": "https://flexy-ai.com",
            "form_filter_kp": 2,
            "": undefined,
            "arm_nonce_check": arm_nonce_check,
        }
        self._post_ajax("https://flexy-ai.com/wp-admin/admin-ajax.php", payload)
        self.logger.info("вход выполнен успешно.")

def _parse_login_html(r: requests.Response):
    r.encoding = 'utf-8'
    return get_undefined_and_arm_nonce_check_inputs(r.text)
