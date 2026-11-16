"""Desktop HTTP client and FAST transport for the EXAM AI beta server."""
import base64
import os
import threading
import logging
from time import perf_counter
from urllib.parse import urlsplit
from pathlib import Path
import json

import httpx

from examai.ai.llm_engine import GenerationError


def distribution_url():
    path = Path(__file__).with_name("cloud_config.json")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))["server_url"]
    except (OSError, ValueError, KeyError):
        return ""


class BetaClient:
    def __init__(self, url="", token=""):
        url = (url or os.getenv("EXAMAI_SERVER_URL", "")).rstrip("/")
        parsed = urlsplit(url)
        if url and (parsed.username or parsed.password or parsed.query or parsed.fragment or
                    not (parsed.scheme == "https" and parsed.hostname or
                         parsed.scheme == "http" and parsed.hostname == "127.0.0.1")):
            raise ValueError("Use an HTTPS address for EXAM AI Cloud")
        self.url = url
        self.token = token
        self.lock = threading.Lock()
        self.users = 0

    def request(self, method, route, body=None):
        if not self.url:
            raise GenerationError("EXAM AI Cloud is not configured in this build.")
        try:
            response = httpx.request(method, self.url + route, json=body,
                                     headers=self.headers(), timeout=8)
            if not response.is_success:
                detail = response.json().get("detail")
                if route == "/invite" and response.status_code == 422:
                    detail = "Вставте повний код запрошення (від 16 до 128 символів)."
                elif route == "/invite" and response.status_code == 409 and detail == "Invite unavailable":
                    detail = "Запрошення недійсне, уже використане або призначене іншому обліковому запису."
                raise GenerationError(detail if isinstance(detail, str) else "Не вдалося виконати запит до EXAM AI.")
            return response.json()
        except (ValueError, httpx.HTTPError):
            raise GenerationError("EXAM AI service unavailable") from None

    def headers(self):
        return {"Authorization": "Bearer " + self.token} if self.token else {}

    def acquire(self):
        with self.lock:
            state = self.request("POST", "/activity/start")
            if not state["granted"]:
                raise GenerationError("Beta access expired or active on another device.")
            self.users += 1

    def release(self):
        with self.lock:
            self.users = max(0, self.users - 1)
            if not self.users:
                try:
                    self.request("POST", "/activity/stop")
                except GenerationError:
                    pass  # Server lease expires independently.


class CloudEngine:
    used_model = "EXAM AI Cloud"

    def __init__(self, client):
        self.client = client

    def close(self):
        pass

    def generate_answer(self, question, context, *, on_text=None, request_id="-", **_kwargs):
        return self._stream({"question": question[:4000], "context": context[:3500]}, on_text, request_id)

    def generate_image_answer(self, image, mime, *, on_text=None, user_instruction="", context="",
                              request_id="-", cancelled=None, **_kwargs):
        if not image or len(image) > 2_625_000 or mime not in {"image/png", "image/jpeg"}:
            raise GenerationError("Зображення завелике або пошкоджене. Виділіть меншу область.")
        return self._stream({"question": user_instruction[:4000] or "Проаналізуй зображення",
                             "context": context[:3500], "mime": mime,
                             "image": base64.b64encode(image).decode()}, on_text, request_id, cancelled)

    def generate_summary(self, context, *, cancelled=None):
        return self._stream({"question": "Склади короткий конспект за наданим текстом.",
                             "context": context[:3500]}, None, "-", cancelled)

    def _stream(self, body, on_text, request_id="-", cancelled=None):
        started = perf_counter()
        self.client.acquire()
        timing = logging.getLogger("phantom.timing")
        timing.info("request_id=%s cloud_lease_ms=%.2f", request_id, (perf_counter() - started) * 1000)
        sent = perf_counter()
        first = True
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(5):
                try:
                    if not self.client.request("POST", "/activity/start")["granted"]:
                        break
                except GenerationError:
                    break
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        try:
            if cancelled and cancelled():
                raise GenerationError("Запит скасовано.")
            with httpx.stream("POST", self.client.url + "/answer", json=body,
                              headers=self.client.headers(),
                              timeout=httpx.Timeout(48, connect=5) if "image" in body else 25) as response:
                response.raise_for_status()
                import json
                for line in response.iter_lines():
                    if cancelled and cancelled():
                        raise GenerationError("Запит скасовано.")
                    event = json.loads(line)
                    if "text" in event and on_text:
                        if first:
                            timing.info("request_id=%s cloud_ttft_ms=%.2f", request_id, (perf_counter() - sent) * 1000)
                            first = False
                        on_text(event["text"])
                    if "final" in event:
                        return event["final"]
                    if "error" in event:
                        raise GenerationError(event["error"])
        except httpx.ReadTimeout:
            raise GenerationError("EXAM AI Cloud затримав відповідь. Спробуйте ще раз.") from None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403, 429):
                message = {401: "Увійдіть в EXAM AI Cloud ще раз.",
                           403: "Доступ до EXAM AI Cloud або ліміт використання вичерпано.",
                           429: "Досягнуто квоти EXAM AI Cloud."}[status]
                raise GenerationError(message) from None
            raise GenerationError("EXAM AI Cloud тимчасово недоступний.") from None
        except (httpx.HTTPError, ValueError):
            raise GenerationError("EXAM AI Cloud request failed or the usage limit was reached.") from None
        finally:
            stop.set()
            def cleanup():
                worker.join()
                self.client.release()
            threading.Thread(target=cleanup, daemon=True, name="examai-cloud-release").start()
        raise GenerationError("EXAM AI Cloud ended without a complete answer.")
