"""Gemini Live Ukrainian transcription, using the user's Gemini API key.

The capture thread only enqueues short PCM blocks. An isolated asyncio thread
owns the Google SDK session and forwards interim and final text callbacks.
"""
from __future__ import annotations

import asyncio
from collections import deque
import logging
from math import gcd
import re
import threading
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.signal import resample_poly

from google import genai
from google.genai import types
from websockets.exceptions import ConnectionClosed


MAX_PENDING_TURNS = 4
LIVE_CONNECT_SECONDS = 5
trace = logging.getLogger('phantom.timing')


MODEL = "gemini-3.5-transcribe-live"


def friendly_gemini_error(error: Exception) -> str:
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    # Live websocket failures can expose a transport close code (1008) while
    # the HTTP status is only in the reason. Never display the raw reason.
    detail = str(error)
    if str(code) == "429" or re.search(r"(?<!\d)429(?!\d)|RESOURCE_EXHAUSTED", detail, re.I):
        return "Gemini STT: досягнуто ліміту API (HTTP 429). Переходжу на локальний Whisper."
    if str(code) in {"401", "403"}:
        return f"Gemini STT: доступ відхилено (HTTP {code}). Переходжу на локальний Whisper."
    if str(code) == "404":
        return "Gemini STT: модель Live Transcribe недоступна (HTTP 404). Переходжу на локальний Whisper."
    if isinstance(error, TimeoutError):
        return "Gemini STT: час підключення вичерпано. Переходжу на локальний Whisper."
    return "Gemini STT: з’єднання недоступне. Переходжу на локальний Whisper."


class GeminiRealtimeSTT:
    def __init__(
        self, api_key: str,
        on_preview: Callable[[str], None],
        on_final: Callable[[int, str], None],
        on_error: Callable[[str], None],
        *, keywords: tuple[str, ...] = (), client_factory=genai.Client, trace_id=str,
    ) -> None:
        self.api_key = api_key
        self.on_preview = on_preview
        self.on_final = on_final
        self.on_error = on_error
        self.keywords = [term for term in keywords if isinstance(term, str) and term.strip()][:100]
        self.client_factory = client_factory
        self.trace_id = trace_id
        self.error = ""
        self.reconnectable = False
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serve_task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None
        self._pending_ids: deque[int] = deque()
        self._end_send_started = {}
        self._lock = threading.Lock()
        self._active = False
        self._pcm_pending = np.empty(0, dtype=np.float32)
        self._rate = 0
        self._audio_started_at = None
        self._audio_request_id = None
        self._first_event_at = None
        self._last_audio_sent_at = None
        self._last_send_at = perf_counter()

    @property
    def failed(self) -> bool:
        return bool(self.error)

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and not self.failed and not self._closed.is_set()

    def start(self, *, wait=True) -> bool:
        self._thread = threading.Thread(target=self._thread_main, name="ExamAI-Gemini-Live", daemon=True)
        self._thread.start()
        if not wait:
            return True
        if not self._ready.wait(LIVE_CONNECT_SECONDS):
            self._fail("Gemini STT: сервер не підтвердив сесію за 5 с. Переходжу на локальний Whisper.")
        if self.failed or self._closed.is_set():
            self.close()
            return False
        return True

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if not self._closed.is_set():
                code = getattr(exc, "code", None)
                if isinstance(exc, ConnectionClosed) and exc.rcvd is not None:
                    code = exc.rcvd.code
                if isinstance(exc, ConnectionClosed) and code is None:
                    code = 1006
                idle_abort = (code == 1008 and "operation was aborted" in str(exc).casefold()
                              and perf_counter() - self._last_send_at >= 30)
                self.reconnectable = (isinstance(exc, (OSError, EOFError))
                                      or code in {1000, 1001, 1006, 1011, 1012, 1013}
                                      or idle_abort)
                trace.info("live_disconnected type=%s close_code=%s reconnectable=%s",
                           type(exc).__name__, code if isinstance(code, int) else "-", self.reconnectable)
                self._fail(friendly_gemini_error(exc))

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._serve_task = asyncio.current_task()
        if self._closed.is_set():
            return
        def expire_connect():
            self._fail("Gemini STT: сервер не підтвердив підключення. Переходжу на Whisper.")
            self._serve_task.cancel()

        deadline = self._loop.call_later(LIVE_CONNECT_SECONDS, expire_connect)
        client = self.client_factory(api_key=self.api_key)
        try:
            config = types.LiveConnectConfig(
                response_modalities=["TEXT"],
                realtime_input_config=types.RealtimeInputConfig(
                    automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
                ),
                input_audio_transcription=types.AudioTranscriptionConfig(
                    language_codes=["uk-UA"],
                    custom_vocabulary=self.keywords or None,
                    mode="SMART",
                ),
            )
            async with client.aio.live.connect(model=MODEL, config=config) as session:
                deadline.cancel()
                self._queue = asyncio.Queue(maxsize=16)
                self._ready.set()
                sender = asyncio.create_task(self._send_loop(session))
                receiver = asyncio.create_task(self._receive_loop(session))
                tasks = (sender, receiver)
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            deadline.cancel()
            client.close()

    async def _send_loop(self, session) -> None:
        assert self._queue is not None
        while True:
            kind, data = await self._queue.get()
            if kind == "start":
                self._audio_started_at = perf_counter()
                self._audio_request_id = data
                self._first_event_at = None
                self._last_audio_sent_at = None
                await session.send_realtime_input(activity_start=types.ActivityStart())
            elif kind == "audio":
                await session.send_realtime_input(
                    audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                )
                self._last_audio_sent_at = perf_counter()
            elif kind == "end":
                self._end_send_started[data[0]] = perf_counter()
                await session.send_realtime_input(activity_end=types.ActivityEnd())
                now = perf_counter()
                start = self._audio_started_at
                first = self._first_event_at
                trace.info("request_id=%s live_audio_send_ms=%.1f live_first_event_ms=%.1f live_end_queue_ms=%.1f",
                           self.trace_id(data[0]),
                           (self._last_audio_sent_at - start) * 1000
                           if start is not None and self._last_audio_sent_at is not None else -1,
                           (first - start) * 1000 if start is not None and first is not None else -1,
                           (now - data[1]) * 1000)
            self._last_send_at = perf_counter()

    async def _receive_loop(self, session) -> None:
        # The SDK's receive() iterator ends after each model turn. Open a new
        # iterator for the next utterance while keeping the same Live session.
        delivered = False
        while not self._closed.is_set():
            received = False
            async for message in session.receive():
                if self._closed.is_set():
                    return
                received = True
                content = getattr(message, "server_content", None)
                if content is None:
                    continue
                if self._first_event_at is None:
                    self._first_event_at = perf_counter()
                    if self._audio_started_at is not None:
                        trace.info("request_id=%s live_first_event_from_audio_ms=%.1f",
                                   self.trace_id(self._audio_request_id)
                                   if self._audio_request_id is not None else "-",
                                   (self._first_event_at - self._audio_started_at) * 1000)
                interim = getattr(content, "interim_input_transcription", None)
                if interim and getattr(interim, "text", None):
                    self.on_preview(interim.text.strip())
                final = getattr(content, "input_transcription", None)
                boundary = (getattr(content, "turn_complete", False)
                            or getattr(content, "interaction_status", None) == "IDLE")
                authoritative = final is not None and getattr(final, "finished", None) is not False
                # Transcribe Live can send multiple finals in one receive()
                # iterator. Its final transcript is itself the turn boundary.
                if authoritative or (boundary and not delivered):
                    with self._lock:
                        utterance_id = self._pending_ids.popleft() if self._pending_ids else None
                    if utterance_id is not None:
                        delivered = authoritative
                        sent = self._end_send_started.pop(utterance_id, None)
                        trace.info("request_id=%s live_final_from_end_send_ms=%.1f",
                                   self.trace_id(utterance_id),
                                   (perf_counter() - sent) * 1000 if sent is not None else -1)
                        self.on_final(utterance_id, str(getattr(final, "text", "") or "").strip())
                if boundary:
                    delivered = False
            if not received and not self._closed.is_set():
                raise EOFError("Gemini Live receive iterator ended without data")

    def push(self, audio: np.ndarray, sample_rate: int, *, utterance_id: int | None = None) -> None:
        if self.failed or self._closed.is_set() or sample_rate <= 0:
            return
        if not self._active:
            self._enqueue("start", utterance_id)
            self._active = True
        if self._rate and self._rate != sample_rate:
            self.clear()
            return
        self._rate = sample_rate
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        self._pcm_pending = np.concatenate((self._pcm_pending, samples))
        size = max(1, round(sample_rate * .1))
        while len(self._pcm_pending) >= size:
            self._queue_pcm(self._pcm_pending[:size], sample_rate)
            self._pcm_pending = self._pcm_pending[size:]

    def commit(self, utterance_id: int) -> None:
        if self.failed or self._closed.is_set() or not self._active:
            return
        if len(self._pcm_pending):
            self._queue_pcm(self._pcm_pending, self._rate)
        self._pcm_pending = np.empty(0, dtype=np.float32)
        with self._lock:
            overflow = len(self._pending_ids) >= MAX_PENDING_TURNS
            if not overflow:
                self._pending_ids.append(utterance_id)
        if overflow:
            self._fail("Gemini STT: забагато незавершених фраз. Переходжу на локальний Whisper.")
            return
        self._enqueue("end", (utterance_id, perf_counter()))
        self._active = False

    def reset_after_missing_final(self) -> None:
        self.reconnectable = True
        self._fail("Gemini STT: попередня фраза не завершилась. Переходжу на Whisper.")

    def clear(self) -> None:
        # A broken turn cannot be matched safely to a later final without an
        # item ID. Keep the local audio path instead of combining two phrases.
        self._fail("Gemini STT: пропущено частину звуку. Переходжу на локальний Whisper.")

    def _queue_pcm(self, samples: np.ndarray, rate: int) -> None:
        if rate != 16000:
            divisor = gcd(rate, 16000)
            samples = resample_poly(samples, 16000 // divisor, rate // divisor)
        pcm = np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2", copy=False).tobytes()
        self._enqueue("audio", pcm)

    def _enqueue(self, kind: str, data) -> None:
        loop, queue = self._loop, self._queue
        if loop is None or queue is None or self.failed:
            return

        def put() -> None:
            try:
                queue.put_nowait((kind, data))
            except asyncio.QueueFull:
                self._fail("Gemini STT: мережа не встигає за звуком. Переходжу на локальний Whisper.")

        try:
            loop.call_soon_threadsafe(put)
        except RuntimeError:
            self._fail("Gemini STT: сесію закрито. Переходжу на локальний Whisper.")

    def _fail(self, message: str) -> None:
        if not self.error and not self._closed.is_set():
            self.error = message
            self._ready.set()
            self.on_error(message)

    def close(self, *, wait: bool = True) -> None:
        was_closed = self._closed.is_set()
        self._closed.set()
        self._ready.set()
        loop, task = self._loop, self._serve_task
        if not was_closed and loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
