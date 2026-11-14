"""Desktop HTTP client and FAST transport for the EXAM AI beta server."""
import base64
import os
import threading
from urllib.parse import urlsplit

import httpx

from examai.ai.llm_engine import GenerationError


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
            raise GenerationError("Set the EXAM AI server URL in Subscriptions.")
        try:
            response = httpx.request(method, self.url + route, json=body,
                                     headers=self.headers(), timeout=8)
            if not response.is_success:
                raise GenerationError(response.json().get("detail", "EXAM AI service unavailable"))
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

    def generate_answer(self, question, context, *, on_text=None, **_kwargs):
        return self._stream({"question": question[:4000], "context": context[:3500]}, on_text)

    def generate_image_answer(self, image, mime, *, on_text=None, user_instruction="", context="", **_kwargs):
        return self._stream({"question": user_instruction[:4000] or "Проаналізуй зображення",
                             "context": context[:3500], "mime": mime,
                             "image": base64.b64encode(image).decode()}, on_text)

    def generate_summary(self, context):
        return self.generate_answer("Склади короткий конспект за наданим текстом.", context)

    def _stream(self, body, on_text):
        self.client.acquire()
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
            with httpx.stream("POST", self.client.url + "/answer", json=body,
                              headers=self.client.headers(), timeout=25) as response:
                response.raise_for_status()
                import json
                for line in response.iter_lines():
                    event = json.loads(line)
                    if "text" in event and on_text:
                        on_text(event["text"])
                    if "final" in event:
                        return event["final"]
                    if "error" in event:
                        raise GenerationError(event["error"])
        except (httpx.HTTPError, ValueError):
            raise GenerationError("EXAM AI Cloud request failed or the usage limit was reached.") from None
        finally:
            stop.set()
            worker.join()
            self.client.release()
        raise GenerationError("EXAM AI Cloud ended without a complete answer.")
