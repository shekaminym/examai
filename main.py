import logging
import os
import sqlite3
import sys
import threading
from pathlib import Path
from math import ceil
from time import perf_counter
from uuid import uuid4

import keyboard
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect, QMenu
from PySide6.QtGui import QPainter, QColor, QPen

from audio_capturer import AudioCapturer, audio_log
from audio_vad import NeuralVAD, SpeechSegmenter, Utterance
from config import ConfigManager
from developer_timing import DeveloperTimingWindow
from database import DatabaseManager
from llm_engine import GenerationError, LLMEngine
from privacy_qt import CapturePrivacy
from rounded_window import configure_rounded_window
from rag_engine import FileIndexerThread, RAGEngine
from rag_engine import ReferenceLookupError
from question_aggregator import QuestionAggregator, is_continuation_fragment, is_unreliable_transcript
from image_intent import EXPLAIN_CODE, image_intent, select_screen_instruction
from stt_engine import STTEngine
from speech_correction import SpeechCorrector, has_non_ukrainian_cyrillic
from ui_main import MainWindow
from screen_selection import ScreenSelection, encode_image


timing = logging.getLogger("phantom.timing")
_VAD_NOT_PREPARED = object()
CLOUD_FINAL_TIMEOUT_SECONDS = 0.65
ANSWER_TARGET_SECONDS = 2.0
MAX_RETIRED_ANSWERS = 2
MAX_SESSION_ANSWERS = 30


class ModernSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 50)
        self._angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate)
        self.timer.start(15)

    def rotate(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        pen = QPen(QColor(139, 177, 255, 225))
        pen.setWidth(4)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        
        painter.drawArc(5, 5, 40, 40, -self._angle * 16, 140 * 16)


class SplashScreen(QWidget):
    finished = Signal()

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window 
        
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen)
        self.setStyleSheet("background-color: #101f47;")
        self.setFixedSize(420, 230)
        configure_rounded_window(self, radius=22, opacity=0.90, color="#101f47")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.bg_frame = QWidget(self)
        self.bg_frame.setObjectName("SplashCard")
        self.bg_frame.setStyleSheet("""
            QWidget#SplashCard {
                background-color: rgba(27, 53, 111, 230);
                border-radius: 22px;
                border: 1px solid rgba(164, 194, 255, 70);
            }
        """)
        
        bg_layout = QVBoxLayout(self.bg_frame)
        bg_layout.setAlignment(Qt.AlignCenter)

        self.title_label = QLabel("EXAM AI", self.bg_frame)
        self.title_label.setStyleSheet("""
            color: white; 
            font-family: 'Segoe UI';
            font-size: 32px;
            font-weight: bold; 
            background: transparent;
            border: none;
        """)
        self.title_label.setAlignment(Qt.AlignCenter)

        self.slogan_label = QLabel("Слухаю • розумію • відповідаю", self.bg_frame)
        self.slogan_label.setStyleSheet("""
            color: #b8caf5;
            font-family: 'Segoe UI';
            font-size: 13px;
            background: transparent;
            border: none;
        """)
        self.slogan_label.setAlignment(Qt.AlignCenter)

        self.spinner = ModernSpinner(self.bg_frame)

        bg_layout.addStretch()
        bg_layout.addWidget(self.title_label)
        bg_layout.addWidget(self.slogan_label)
        bg_layout.addSpacing(20)
        bg_layout.addWidget(self.spinner, alignment=Qt.AlignCenter)
        bg_layout.addStretch()

        layout.addWidget(self.bg_frame)

        # Keep the rounded translucent native surface stable and fade content.
        self.fade_effect = QGraphicsOpacityEffect(self.bg_frame)
        self.bg_frame.setGraphicsEffect(self.fade_effect)
        self.fade_effect.setOpacity(0.0)

        self.fade_in_anim = QPropertyAnimation(self.fade_effect, b"opacity")
        self.fade_in_anim.setDuration(240)
        self.fade_in_anim.setStartValue(0.0)
        self.fade_in_anim.setEndValue(1.0)
        self.fade_in_anim.setEasingCurve(QEasingCurve.InOutQuad)

        self.fade_out_anim = QPropertyAnimation(self.fade_effect, b"opacity")
        self.fade_out_anim.setDuration(260)
        self.fade_out_anim.setStartValue(1.0)
        self.fade_out_anim.setEndValue(0.0)
        self.fade_out_anim.setEasingCurve(QEasingCurve.InOutQuad)
        
        self.fade_out_anim.finished.connect(self.finish_splash)

    def start_splash(self):
        self.show()
        self.fade_in_anim.start()
        QTimer.singleShot(850, self.fade_out_anim.start)

    def finish_splash(self):
        # Stop tracking before close destroys the splash's native HWND.
        self.spinner.timer.stop()
        self.finished.emit()
        self.close()
        self.main_window.show()


class DecodeMailbox:
    """One fresh preview plus one final utterance, with final priority.

    Whisper cannot safely be interrupted mid-inference.  Keeping just one
    replaceable preview prevents old text from queuing ahead of a completed
    question; a final utterance is always selected first.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._preview: Utterance | None = None
        self._final: Utterance | None = None
        self._closed = False

    def submit(self, utterance: Utterance) -> None:
        with self._condition:
            if self._closed:
                return
            if utterance.is_final:
                self._final = utterance
                self._preview = None
            else:
                self._preview = utterance
            self._condition.notify()

    def get(self) -> Utterance | None:
        with self._condition:
            while not self._closed and self._final is None and self._preview is None:
                self._condition.wait()
            if self._final is not None:
                utterance, self._final = self._final, None
                return utterance
            if self._preview is not None:
                utterance, self._preview = self._preview, None
                return utterance
            return None

    def close(self, *, discard_finals: bool) -> None:
        with self._condition:
            self._closed = True
            self._preview = None
            if discard_finals:
                self._final = None
            self._condition.notify_all()


class AudioWorker(QThread):
    text_updated = Signal(str)
    question_finished = Signal(str)
    timed_question = Signal(str, object, str)
    voice_endpoint = Signal(float)
    speech_started = Signal(float)
    status = Signal(str)

    def __init__(self, stt, language, device_index, loopback, *,
                 previews=False, neural_vad=False, end_silence_seconds=0.42,
                 gemini_key="", pre_roll_seconds=0.24):
        super().__init__()
        self.stt = stt
        self.language = language
        self.device_index = device_index
        self.loopback = loopback
        self.previews = previews
        self.neural_vad = neural_vad
        self.end_silence_seconds = end_silence_seconds
        self.pre_roll_seconds = pre_roll_seconds
        self.gemini_key = gemini_key
        self.corrector = SpeechCorrector(Path(__file__).with_name("speech_glossary.json"))
        self.capturer = AudioCapturer()
        self._mailbox: DecodeMailbox | None = None
        self._trace_session = uuid4().hex[:12]
        self._cloud_speech = None
        self._latest_final_id = 0
        self._latest_delivered_id = 0
        self._pending_cloud_finals: dict[int, Utterance] = {}
        self._hedged_cloud_ids: set[int] = set()
        self._auto_language_ids: set[int] = set()
        self._pending_lock = threading.Lock()
        self.cloud = None
        self._cloud_label = "Gemini Live"

    def run(self):
        if self.gemini_key:
            self.status.emit("Підключення Gemini Live для української мови…")
            try:
                from stt_gemini import GeminiRealtimeSTT
                self.cloud = GeminiRealtimeSTT(
                    self.gemini_key, self._cloud_preview, self._cloud_final,
                    self.status.emit, keywords=self.corrector.terms, trace_id=self._trace_id,
                )
            except ImportError:
                self.status.emit("Gemini Live недоступний. Використовую локальний Whisper.")
            if self.cloud is not None and not self.cloud.start():
                self.cloud = None
        if self.cloud is None:
            try:
                self.status.emit("Підготовка Whisper…")
                self.stt.prepare()
            except Exception:
                self.status.emit("Whisper не завантажився. Перевірте модель та налаштування.")
                return
        if self.isInterruptionRequested():
            self._close_cloud()
            return
        try:
            self.capturer.start(self.device_index, self.loopback, activate=False)
        except Exception as exc:
            self._close_cloud()
            self.status.emit(f"Не вдалося відкрити джерело аудіо: {exc}")
            return
        try:
            vad = self._create_vad()
            if self.isInterruptionRequested():
                return
            try:
                self.capturer.activate()
            except Exception as exc:
                audio_log.exception("capture stream activation failed")
                self.status.emit(f"Не вдалося запустити аудіопотік: {exc}")
                return
            self.status.emit("EXAM AI слухає…")
            self.process_audio(vad=vad)
        except Exception:
            self.status.emit("Помилка аудіозахоплення або обробки звуку.")
        finally:
            self._close_cloud()
            try:
                self.capturer.stop()
            except Exception:
                self.status.emit("Помилка закриття аудіопристрою.")

    def _close_cloud(self, *, wait=True) -> None:
        cloud, self.cloud = self.cloud, None
        if cloud is not None:
            if wait:
                cloud.close()
            else:
                cloud.close(wait=False)

    def _trace_id(self, utterance_id):
        return f'{self._trace_session}-{utterance_id}'

    def _decode_loop(self):
        """Decode away from capture; favour a final phrase over a preview."""
        assert self._mailbox is not None
        while True:
            utterance = self._mailbox.get()
            if utterance is None:
                return
            if utterance.is_final:
                with self._pending_lock:
                    auto_language = utterance.utterance_id in self._auto_language_ids
                    self._auto_language_ids.discard(utterance.utterance_id)
                    if utterance.utterance_id <= self._latest_delivered_id:
                        continue
            else:
                auto_language = False
            started = perf_counter()
            try:
                text, _ = self.stt.transcribe_chunk(
                    utterance.audio,
                    language="auto" if auto_language else self.language,
                    sample_rate=utterance.sample_rate,
                    preview=not utterance.is_final,
                )
            except Exception:
                self.status.emit("Помилка розпізнавання окремої фрази.")
                if utterance.is_final:
                    self.timed_question.emit("", utterance.speech_ended_at,
                                             self._trace_id(utterance.utterance_id))
                continue
            timing.info("request_id=%s stt final=%s queue_ms=%.1f decode_ms=%.1f audio_ms=%.1f",
                        self._trace_id(utterance.utterance_id), utterance.is_final, (started - utterance.queued_at) * 1000,
                        (perf_counter() - started) * 1000,
                        len(utterance.audio) / utterance.sample_rate * 1000)
            text = self.corrector.correct(text)
            if not text:
                continue
            # A preview that finished after its final chunk was queued is stale.
            if not utterance.is_final and utterance.utterance_id <= self._latest_final_id:
                continue
            if utterance.is_final:
                self._publish_final(utterance.utterance_id, text, utterance.speech_ended_at)
            else:
                timing.info("request_id=%s stt_partial=1", self._trace_id(utterance.utterance_id))
                self.text_updated.emit(text)

    def _publish_final(self, utterance_id: int, text: str, speech_ended_at=None) -> None:
        with self._pending_lock:
            if utterance_id <= self._latest_delivered_id or self.isInterruptionRequested():
                return
            self._latest_delivered_id = utterance_id
            self.text_updated.emit(text)
            self.question_finished.emit(text)
            trace_id = self._trace_id(utterance_id)
            timing.info('request_id=%s stt_final=1 stt_final_from_speech_end_ms=%.1f', trace_id,
                        (perf_counter() - speech_ended_at) * 1000 if speech_ended_at is not None else -1)
            self.timed_question.emit(text, speech_ended_at, trace_id)

    def _cloud_preview(self, text: str) -> None:
        if not self.isInterruptionRequested() and not has_non_ukrainian_cyrillic(text):
            speech = self._cloud_speech
            if speech is not None:
                utterance_id, started = speech
                timing.info("request_id=%s stt_cloud_partial_ms=%.1f",
                            self._trace_id(utterance_id), (perf_counter() - started) * 1000)
            timing.info("request_id=%s stt_partial=1", self._trace_id(speech[0]) if speech else "-")
            self.text_updated.emit(self.corrector.correct(text))

    def _cloud_final(self, utterance_id: int, text: str) -> None:
        with self._pending_lock:
            utterance = self._pending_cloud_finals.pop(utterance_id, None)
            hedged = utterance_id in self._hedged_cloud_ids
            self._hedged_cloud_ids.discard(utterance_id)
            if utterance_id <= self._latest_delivered_id or utterance is None:
                return
        if not text.strip() or has_non_ukrainian_cyrillic(text):
            if not hedged and not self.isInterruptionRequested():
                if text.strip():
                    with self._pending_lock:
                        self._auto_language_ids.add(utterance_id)
                    self.status.emit("Мішана або російська мова. Перевіряю локальним Whisper.")
                self._submit(utterance)
            return
        timing.info("request_id=%s stt_cloud_final_ms=%.1f", self._trace_id(utterance_id),
                    (perf_counter() - utterance.queued_at) * 1000)
        self._publish_final(utterance_id, self.corrector.correct(text), utterance.speech_ended_at)

    def _submit(self, utterance: Utterance) -> None:
        if utterance.is_final:
            self._latest_final_id = max(self._latest_final_id, utterance.utterance_id)
        assert self._mailbox is not None
        self._mailbox.submit(utterance)

    def _dispatch_utterance(self, utterance: Utterance, *, cloud_streamed=True) -> None:
        if utterance.is_final and utterance.speech_ended_at is not None:
            self.voice_endpoint.emit(utterance.speech_ended_at)
            timing.info("request_id=%s pause=1 vad_endpoint_ms=%.1f", self._trace_id(utterance.utterance_id),
                        (utterance.queued_at - utterance.speech_ended_at) * 1000)
        if self.cloud is not None and self.cloud.ready and cloud_streamed and utterance.is_final:
            with self._pending_lock:
                self._pending_cloud_finals[utterance.utterance_id] = utterance
            self.cloud.commit(utterance.utterance_id)
        elif self.cloud is None or not self.cloud.ready or not cloud_streamed:
            self._submit(utterance)
            if self.cloud is not None and self.cloud.ready and utterance.is_final:
                with self._pending_lock:
                    stale = bool(self._hedged_cloud_ids)
                if stale:
                    self.cloud.reset_after_missing_final()

    def _fallback_slow_cloud(self) -> None:
        cutoff = perf_counter() - CLOUD_FINAL_TIMEOUT_SECONDS
        with self._pending_lock:
            expired = [utterance for utterance in self._pending_cloud_finals.values()
                       if utterance.queued_at <= cutoff
                       and utterance.utterance_id not in self._hedged_cloud_ids]
            self._hedged_cloud_ids.update(utterance.utterance_id for utterance in expired)
        for utterance in expired:
            self._submit(utterance)
        if expired:
            self.status.emit(f"{self._cloud_label} затримується. Пробую локальний Whisper.")

    def _create_vad(self):
        if not self.neural_vad:
            return None
        try:
            return NeuralVAD(self.capturer.sample_rate)
        except Exception:
            self.status.emit("Silero недоступний. Використовую детектор гучності.")
            return None

    def process_audio(self, vad=_VAD_NOT_PREPARED):
        """Continuously segment PCM while a dedicated thread runs Whisper."""
        if vad is _VAD_NOT_PREPARED:
            vad = self._create_vad()
        segmenter = SpeechSegmenter(
            previews=self.previews, vad=vad, end_silence_seconds=self.end_silence_seconds,
            pre_roll_seconds=self.pre_roll_seconds,
        )
        capture_generation = None
        streaming = False
        reconnected = False
        interrupted = False
        self._mailbox = DecodeMailbox()
        decoder = threading.Thread(target=self._decode_loop, name="ExamAI-Whisper", daemon=True)
        decoder.start()
        try:
            while not self.isInterruptionRequested():
                if self.cloud is not None and not self.cloud.failed:
                    self._fallback_slow_cloud()
                if self.cloud is not None and self.cloud.failed:
                    previous = self.cloud
                    self._close_cloud(wait=False)
                    streaming = False
                    with self._pending_lock:
                        pending = [item for key, item in self._pending_cloud_finals.items()
                                   if key not in self._hedged_cloud_ids]
                        self._pending_cloud_finals.clear()
                        self._hedged_cloud_ids.clear()
                    for utterance in pending:
                        self._submit(utterance)
                    if previous.reconnectable and not reconnected:
                        reconnected = True
                        timing.info("live_reconnect_started=1")
                        self.cloud = type(previous)(
                            self.gemini_key, self._cloud_preview, self._cloud_final, self.status.emit,
                            keywords=self.corrector.terms, trace_id=self._trace_id,
                        )
                        self.cloud.start(wait=False)
                block = self.capturer.get_audio_data(timeout=0.05)
                if block is None:
                    stream = self.capturer.stream
                    if stream is not None and not stream.is_active():
                        raise RuntimeError("Audio stream stopped")
                    for utterance in segmenter.flush_if_idle(perf_counter()):
                        self._dispatch_utterance(utterance, cloud_streamed=streaming)
                        streaming = False
                    continue
                generation, audio, rms = block
                if capture_generation is not None and generation != capture_generation:
                    segmenter.reset()
                    self._cloud_speech = None
                    streaming = False
                    if self.cloud is not None:
                        self.cloud.clear()
                    self.status.emit("Пропущено частину аудіо. Слухаю далі…")
                capture_generation = generation
                vad_started = perf_counter()
                utterances = segmenter.push(audio, self.capturer.sample_rate, rms,
                                            self.capturer.last_block_at)
                if segmenter.started_audio is not None:
                    self.speech_started.emit(vad_started)
                    self._cloud_speech = (segmenter.utterance_id, vad_started)
                    device_ms = self.capturer.last_device_latency_ms
                    timing.info("request_id=%s speech_start=1 audio_device_ms=%.1f audio_queue_ms=%.1f vad_first_block_ms=%.1f",
                                self._trace_id(segmenter.utterance_id),
                                device_ms if device_ms is not None else -1,
                                (vad_started - self.capturer.last_block_at) * 1000
                                if self.capturer.last_block_at is not None else -1,
                                (perf_counter() - vad_started) * 1000)
                if self.cloud is not None and self.cloud.ready:
                    if streaming:
                        self.cloud.push(audio, self.capturer.sample_rate)
                    else:
                        # Open a cloud turn only once the segment is long enough
                        # to produce a final, including the buffered opening audio.
                        with self._pending_lock:
                            pending_final = bool(self._pending_cloud_finals)
                        # The protocol has no per-turn ID. Avoid assigning a
                        # later final to a preceding turn that never finished.
                        if not pending_final:
                            confirmed = segmenter.confirmed_audio()
                            if confirmed is None and utterances and utterances[-1].is_final:
                                confirmed = utterances[-1].audio
                            if confirmed is not None:
                                self.cloud.push(confirmed, self.capturer.sample_rate,
                                                utterance_id=segmenter.utterance_id)
                                streaming = True
                for utterance in utterances:
                    self._dispatch_utterance(utterance, cloud_streamed=streaming)
                    if utterance.is_final:
                        streaming = False
            interrupted = True
        finally:
            self._close_cloud()
            self._mailbox.close(discard_finals=interrupted)
            decoder.join()


class TaskWorker(QThread):
    result = Signal(object, str)
    progress = Signal(str, float)
    answer_ready = Signal(str)

    def __init__(self, task, failure_message, *, request_id="-"):
        super().__init__()
        self.task = task
        self.failure_message = failure_message
        self.request_id = request_id

    def run(self):
        try:
            value = self.task()
        except GenerationError as exc:
            self.result.emit(None, str(exc))
        except Exception as exc:
            timing.info("request_id=%s worker_error=%s", self.request_id, type(exc).__name__)
            self.result.emit(None, self.failure_message)
        else:
            self.result.emit(value, "")


class MainController(QObject):
    hotkey = Signal()
    recover_window_hotkey = Signal()

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config = ConfigManager()
        self.db = DatabaseManager()
        self.rag = RAGEngine(self.db)
        self.stt = self.stt_settings = None
        self.ui = MainWindow(self.config, self.db)
        self.audio = self.api = self.docs = None
        self.llm = self.llm_settings = None
        self.pending_question = None
        self._pending_options = {}
        self._selecting_screen = False
        self._screen_overlays = []
        self._pending_screen_instruction = ""
        self._last_manual_instruction = ""
        self._retired_answers = {}
        self._answer_history = []
        self._history_index = -1
        self._current_question = ""
        self._current_answer_text = ""
        self._answer_hint_timer = QTimer(self)
        self._voice_timer = QTimer(self)
        self._question_timer = QTimer(self)
        self._question_aggregator = QuestionAggregator(mode=self.config.get("speech_pace_mode", "auto"))
        self._recent_voice_item = None
        self.timing_window = None
        self._dev_speech_started_at = None
        self._dev_request_started_at = None
        self._last_imported_source = None
        self._voice_wait_at = None
        for timer in (self._answer_hint_timer, self._voice_timer, self._question_timer):
            timer.setSingleShot(True)
            timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._answer_hint_timer.timeout.connect(self._show_answer_delay)
        self._voice_timer.timeout.connect(self._expire_voice)
        self._question_timer.timeout.connect(self._finalize_voice_question)
        self.answer_started = False
        self._speech_ended_at = None
        self._answer_cleared = False
        self._last_auto_question = ""
        self._last_auto_at = 0.0
        self.closing = False
        self.hotkey_handle = None
        self.recover_hotkey_handle = None
        self.privacy_enabled = False
        self.capture_privacy = CapturePrivacy(app, self)
        self.capture_privacy.register(self.ui)
        self.capture_privacy.state_changed.connect(self.on_privacy_state)
        self.ui.btn_ghost.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ui.btn_ghost.customContextMenuRequested.connect(self.privacy_menu)

        self.meter = QTimer(self)
        self.meter.setInterval(50)
        self.meter.timeout.connect(self.update_meter)
        self.ui.start_listening_signal.connect(self.start_listening)
        self.ui.stop_listening_signal.connect(self.stop_listening)
        self.ui.file_uploaded_signal.connect(self.handle_file_upload)
        self.ui.file_deleted_signal.connect(self.handle_file_deletion)
        self.ui.ghost_toggled_signal.connect(self.apply_privacy)
        self.ui.search_keyword_signal.connect(self.handle_search_keyword)
        self.ui.auto_summary_signal.connect(self.handle_auto_summary)
        self.ui.question_submitted.connect(self.submit_manual_question)
        self.ui.screen_select_signal.connect(self.start_screen_selection)
        self.ui.clear_all_signal.connect(self.on_clear_all)
        self.ui.history_step_signal.connect(self.on_history_step)
        self.ui.history_selected_signal.connect(self.on_history_select)
        self.ui.developer_timing_signal.connect(self.open_timing_window)
        self.ui.close_requested.connect(self.request_close)
        self.hotkey.connect(self.ui.on_toggle_listen)
        self.recover_window_hotkey.connect(self.recover_window, Qt.ConnectionType.QueuedConnection)
        try:
            self.hotkey_handle = keyboard.add_hotkey("ctrl+alt+r", self.hotkey.emit)
        except Exception:
            self.ui.lbl_status.setText("Глобальна комбінація клавіш недоступна.")
        try:
            self.recover_hotkey_handle = keyboard.add_hotkey("ctrl+shift+f12", self.recover_window_hotkey.emit)
        except Exception:
            self.ui.window_mode.enable_local_recovery()
        self.app.aboutToQuit.connect(self.shutdown)

    @Slot()
    def recover_window(self):
        if not self.closing:
            self.ui.window_mode.recover()

    def update_meter(self):
        self.ui.update_audio_indicator(self.audio.capturer.current_rms if self.audio else 0.0)

    @Slot(bool)
    def apply_privacy(self, enabled, persist=True):
        if persist and self.ui.btn_ghost.locked:
            self.on_privacy_state(self.capture_privacy.verified, self.capture_privacy.message)
            return
        previous = self.capture_privacy.requested
        succeeded = self.capture_privacy.set_enabled(enabled)
        # Also update after a repeated click whose verified state is unchanged.
        self.on_privacy_state(self.capture_privacy.verified, self.capture_privacy.message)
        if persist and previous != enabled:
            self.ui.show_toast(("Ghost Mode ON" if enabled else "Ghost Mode OFF")
                               if succeeded else "Ghost Mode: не вдалося підтвердити зміну")
        if persist and succeeded:
            try:
                self.config.set("privacy_mode", enabled)
            except OSError:
                self.ui.lbl_status.setText("Не вдалося зберегти налаштування.")

    @Slot(bool, str)
    def on_privacy_state(self, enabled, error):
        self.privacy_enabled = enabled
        self.ui.set_privacy_button(enabled, self.capture_privacy.requested, error)
        self.ui.btn_ghost.setToolTip(error or (
            "Ghost v2.1. Windows підтвердив прапор виключення. Правий клік: діагностика."
            if enabled else "Ghost v2.1 вимкнено. Правий клік: діагностика."
        ))
        if error:
            self.ui.lbl_status.setText(f"Ghost Mode: {error}")
        elif enabled:
            self.ui.lbl_status.setText("Ghost Mode: Windows підтвердив виключення із захоплення.")
        else:
            self.ui.lbl_status.setText("Ghost Mode вимкнено.")

    def privacy_menu(self, position):
        menu = QMenu(self.ui)
        action = menu.addAction("Скопіювати діагностику Ghost Mode")
        action.triggered.connect(self.copy_privacy_diagnostics)
        menu.exec(self.ui.btn_ghost.mapToGlobal(position))
        menu.deleteLater()

    @Slot()
    def copy_privacy_diagnostics(self):
        QApplication.clipboard().setText(self.capture_privacy.diagnostics())
        self.ui.show_toast("Діагностику скопійовано. Ключі API не включені.")

    @Slot()
    def open_timing_window(self):
        if self.timing_window is None:
            self.timing_window = DeveloperTimingWindow()
            self.capture_privacy.register(self.timing_window)
        self.timing_window.show()
        self.timing_window.raise_()

    def _timing_metric(self, key, milliseconds):
        if self.timing_window is not None and self.timing_window.isVisible():
            self.timing_window.set_milliseconds(key, milliseconds)

    @Slot()
    def start_listening(self):
        if self.closing or self.audio is not None:
            return
        self._question_timer.stop()
        pace_mode = self.config.get("speech_pace_mode", "auto")
        self._question_aggregator = QuestionAggregator(mode=pace_mode)
        self._recent_voice_item = None
        index = self.config.get("audio_device_index")
        settings = tuple(self.config.get(name) for name in (
            "whisper_model_size", "whisper_device", "whisper_compute_type", "whisper_noise_reduction",
        ))
        if settings != self.stt_settings:
            self.stt = STTEngine(*settings)
            self.stt_settings = settings
        language = self.config.get("whisper_language", "uk")
        if language not in {"uk", "ru", "en", "auto"}:
            language = "uk"
        self.audio = AudioWorker(
            self.stt,
            language,
            index if index >= 0 else None,
            self.config.get("audio_mode") == "loopback",
            previews=self.config.get("local_previews") or (pace_mode == "auto" and self.ui.profile != "eco"),
            neural_vad=self.config.get("vad_mode") != "energy",
            end_silence_seconds=max(
                0.40 if pace_mode == "complete" else 0.42 if pace_mode == "slow" else 0.30,
                max(300, min(1500, self.config.get("speech_end_silence_ms"))) / 1000,
            ),
            pre_roll_seconds=0.32 if pace_mode == "complete" else 0.24,
            gemini_key=self.config.get("gemini_api_key") if language == "uk" else "",
        )
        self.audio.status.connect(self.on_audio_status)
        self.audio.text_updated.connect(self.on_live_text)
        self.audio.timed_question.connect(self.handle_question)
        self.audio.voice_endpoint.connect(self._watch_voice_endpoint)
        self.audio.speech_started.connect(self._on_speech_started)
        self.audio.finished.connect(self.audio_done)
        self.audio.start()
        self.meter.start()
        self.ui.set_listening(True)

    @Slot()
    def stop_listening(self):
        self._finalize_voice_question(force=True)
        self._question_timer.stop()
        self._voice_timer.stop()
        self._voice_wait_at = None
        if self.pending_question and not self.pending_question[1]:
            self.pending_question = None
            self._pending_options = {}
        if self.audio is not None:
            self.audio.requestInterruption()
            self.ui.btn_toggle_listen.setEnabled(False)
            self.ui.lbl_status.setText("Зупиняю…")

    @Slot(str)
    def on_audio_status(self, status):
        if self.audio and not self.audio.isInterruptionRequested() and not self.closing:
            self.ui.lbl_status.setText("EXAM AI слухає…" if status == "Слухаю…" else status)

    @Slot(str)
    def on_live_text(self, text):
        if self.audio and not self.audio.isInterruptionRequested() and not self.closing:
            preview = self._question_aggregator.preview(text)
            if not self._selecting_screen and not self.ui.txt_question.hasFocus():
                self.ui.txt_question.setPlainText(preview)

    @Slot(float)
    def _on_speech_started(self, started_at):
        if not self._question_aggregator.pending:
            self._dev_speech_started_at = started_at
            if self.timing_window is not None and self.timing_window.isVisible():
                self.timing_window.reset()
        if self._question_aggregator.pending:
            self._question_aggregator.speech_started(now=started_at)
            self._arm_question_timer()

    def _arm_question_timer(self):
        if self._question_aggregator.speech_active and self._question_aggregator.mode != "auto":
            self._question_timer.stop()
            return
        deadline = self._question_aggregator.deadline
        if deadline is not None:
            self._question_timer.start(max(1, ceil((deadline - perf_counter()) * 1000)))

    def _finalize_voice_question(self, *, force=False):
        item = self._question_aggregator.finalize(force=force)
        if item is None:
            self._arm_question_timer()
            return
        self._question_timer.stop()
        if item.speech_ended_at is not None:
            self._timing_metric("assembly", (perf_counter() - item.speech_ended_at) * 1000)
        timing.info("request_id=%s question_finalized=1 fragments=%d wait_ms=%.1f",
                    item.request_id, item.fragments,
                    (perf_counter() - item.speech_ended_at) * 1000
                    if item.speech_ended_at is not None else -1)
        self.ui.txt_question.setPlainText(item.text)
        self._submit_auto_question(item.text, item.speech_ended_at, item.request_id)

    @Slot()
    def audio_done(self):
        stopped = self.audio.isInterruptionRequested()
        self.audio.deleteLater()
        self.audio = None
        self.meter.stop()
        self.update_meter()
        self.ui.set_listening(False)
        if stopped and not self.closing:
            self.ui.lbl_status.setText("Зупинено")
        self.finish_close()

    def get_llm(self, mode="fast"):
        model = self.config.get("gemini_model")
        if mode == "smart" and model in {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}:
            model = "gemini-3.5-flash"
        key = self.config.get("gemini_api_key")
        if key != self.llm_settings:
            if self.llm is not None:
                self.llm.close()
            self.llm = LLMEngine(key, model)
            self.llm_settings = key
        self.llm.gemini_model = model
        return self.llm

    @Slot()
    def start_screen_selection(self):
        if self.closing or self._selecting_screen:
            return
        if not self.config.get("gemini_api_key"):
            self.ui.lbl_status.setText("Додайте ключ Gemini у налаштуваннях.")
            return
        self._pending_screen_instruction = select_screen_instruction(
            self.ui.txt_question.toPlainText(), self._last_manual_instruction,
        )
        self._question_timer.stop()
        self._question_aggregator.clear()
        self._selecting_screen = True
        self._voice_timer.stop()
        self._voice_wait_at = None
        self.ui.btn_select_screen.setEnabled(False)
        self.ui.hide()
        # Let Windows repaint the desktop without this window before grabbing it.
        QTimer.singleShot(180, self._show_screen_selection)

    def _show_screen_selection(self):
        if not self._selecting_screen or self.closing:
            return
        try:
            shots = [(screen, screen.grabWindow(0)) for screen in self.app.screens()]
            if not shots or any(pixmap.isNull() for _, pixmap in shots):
                raise RuntimeError("screen capture unavailable")
            # Capture every monitor before drawing any selection window.
            self._screen_overlays = [ScreenSelection(screen, pixmap) for screen, pixmap in shots]
            for overlay in self._screen_overlays:
                overlay.selected.connect(self._on_screen_selected)
                overlay.cancelled.connect(self._cancel_screen_selection)
                overlay.show()
            self._screen_overlays[0].activateWindow()
        except Exception:
            self._finish_screen_selection()
            self.ui.lbl_status.setText("Не вдалося захопити екран. Спробуйте ще раз.")

    def _finish_screen_selection(self):
        for overlay in self._screen_overlays:
            overlay.close()
            overlay.deleteLater()
        self._screen_overlays = []
        self._selecting_screen = False
        self.ui.btn_select_screen.setEnabled(True)
        if not self.closing:
            self.ui.show()
            self.ui.raise_()
            self.ui.activateWindow()

    @Slot()
    def _cancel_screen_selection(self):
        if self._selecting_screen:
            self._finish_screen_selection()
            self._pending_screen_instruction = ""
            self.ui.lbl_status.setText("Виділення скасовано.")

    @Slot(object)
    def _on_screen_selected(self, image):
        if not self._selecting_screen:
            return
        self._finish_screen_selection()
        instruction = self._pending_screen_instruction
        self._pending_screen_instruction = ""
        label = f"Зображення з екрана ({image.width()} × {image.height()})"
        display = instruction + "\n" + label if instruction else label
        self.ui.txt_question.setPlainText(display)
        request_id = uuid4().hex[:12]
        timing.info("request_id=%s screen_image_captured=1 width=%d height=%d",
                    request_id, image.width(), image.height())
        self.submit_question(display, manual=True, image=image, image_instruction=instruction,
                             request_id=request_id)

    @Slot(float)
    def _watch_voice_endpoint(self, speech_ended_at):
        if self._dev_speech_started_at is not None:
            self._timing_metric("voice", (speech_ended_at - self._dev_speech_started_at) * 1000)
        if (self.closing or self._selecting_screen or not self.audio or self.audio.isInterruptionRequested()
                or not self.ui.auto_send.isChecked() or self.ui.txt_question.hasFocus()):
            return
        self._voice_wait_at = speech_ended_at
        remaining = speech_ended_at + ANSWER_TARGET_SECONDS - perf_counter()
        self._voice_timer.start(max(1, ceil(remaining * 1000)))

    @Slot()
    def _expire_voice(self):
        self._voice_wait_at = None
        if not self.closing and self.api is None and self.ui.auto_send.isChecked():
            self.ui.lbl_status.setText("Ще розпізнаю питання…")

    @Slot(str, object, str)
    def handle_question(self, question, speech_ended_at=None, request_id=None):
        if speech_ended_at is not None:
            self._timing_metric("stt", (perf_counter() - speech_ended_at) * 1000)
        if speech_ended_at is not None and speech_ended_at == self._voice_wait_at:
            self._voice_timer.stop()
            self._voice_wait_at = None
        if (not self.audio or self.audio.isInterruptionRequested()
                or self._selecting_screen or not self.ui.auto_send.isChecked()
                or self.ui.txt_question.hasFocus()):
            return
        recent = self._recent_voice_item
        if (recent and not self._question_aggregator.pending
                and perf_counter() - recent[3] <= 4.0
                and is_continuation_fragment(question)):
            self._question_aggregator.add_final(recent[0], recent[1], recent[2])
            self._question_aggregator.speech_started()
            self._recent_voice_item = None
            self._cancel_active_answer()
            timing.info("request_id=%s question_reopened=1", recent[2])
        accepted = self._question_aggregator.add_final(question, speech_ended_at, request_id or "")
        if accepted:
            self.ui.txt_question.setPlainText(self._question_aggregator.text)
        if self._question_aggregator.pending:
            timing.info("request_id=%s aggregator_waiting=1 fragments=%d",
                        self._question_aggregator.request_id, self._question_aggregator.fragments)
            self._arm_question_timer()
        return

    def _submit_auto_question(self, question, speech_ended_at, request_id):
        if self.closing or not self.ui.auto_send.isChecked():
            return
        if is_unreliable_transcript(question):
            timing.info("request_id=%s aggregator_rejected=unreliable", request_id)
            self.ui.lbl_status.setText("Розпізнавання непевне. Перевірте текст і натисніть «Надіслати питання».")
            return
        normalized = " ".join(question.casefold().split())
        now = perf_counter()
        if normalized == self._last_auto_question and now - self._last_auto_at < 8:
            return
        self._last_auto_question = normalized
        self._last_auto_at = now
        self.submit_question(question, manual=False, speech_ended_at=speech_ended_at,
                             request_id=request_id or None)
        self._recent_voice_item = (question, speech_ended_at, request_id or "", now)

    @Slot(str)
    def submit_manual_question(self, question):
        self._recent_voice_item = None
        if self._question_aggregator.pending and not self.ui.txt_question.hasFocus():
            item = self._question_aggregator.finalize(force=True)
            question = item.text
        self._question_aggregator.clear()
        self._question_timer.stop()
        self._last_manual_instruction = question.strip()
        self.submit_question(question, manual=True)

    def submit_question(self, question, manual, speech_ended_at=None, *, request_id=None,
                        mode=None, enqueued_at=None, image=None, image_bytes=None, image_mime=None,
                        image_instruction=""):
        question = question.strip()
        if self.closing or not question:
            return
        submitted = perf_counter()
        request_id = request_id or uuid4().hex[:12]
        mode = mode or self.config.get("answer_mode", "fast")
        enqueued_at = submitted if enqueued_at is None else enqueued_at
        has_image = image is not None or image_bytes is not None
        if self.api is not None:
            if manual or not (self.pending_question and self.pending_question[1]):
                self.pending_question = ((question, manual, speech_ended_at)
                                         if speech_ended_at is not None else (question, manual))
                if self._pending_options:
                    timing.info("request_id=%s queue_replaced=1", self._pending_options["request_id"])
                self._pending_options = dict(request_id=request_id, mode=mode, enqueued_at=enqueued_at,
                                             image=image, image_bytes=image_bytes, image_mime=image_mime,
                                             image_instruction=image_instruction)
                timing.info("request_id=%s answer_queued=1", request_id)
            self.ui.lbl_status.setText("Наступне питання в черзі…")
            return
        if len(self._retired_answers) >= MAX_RETIRED_ANSWERS:
            self.ui.lbl_status.setText("Мережа затримує попередні запити. Повторіть спробу.")
            return
        llm = self.get_llm(mode)
        self._dev_request_started_at = submitted
        self._answer_request_id = request_id
        self._current_question = question
        self._current_answer_text = ""
        self._history_index = -1
        self._sync_history_controls(busy=True)
        self._answer_enqueued_at = enqueued_at
        self.answer_started = False
        self._speech_ended_at = speech_ended_at
        self._answer_cleared = False
        self.ui.txt_answer.setPlainText("")
        self.ui.lbl_status.setText("EXAM AI аналізує…")
        target_from = submitted if speech_ended_at is None else speech_ended_at
        remaining_to_hint = target_from + ANSWER_TARGET_SECONDS - submitted
        if remaining_to_hint <= 0:
            self._show_answer_delay()
        else:
            self._answer_hint_timer.start(ceil(remaining_to_hint * 1000))

        cancelled = threading.Event()
        save_history = self.config.get("save_answer_history", False)
        selected_document = self.ui.list_docs.currentItem()
        source_hint = selected_document.text() if selected_document else self._last_imported_source

        def task():
            rag_started = perf_counter()
            context = ""
            if not has_image:
                try:
                    context = self.rag.retrieve_relevant_context(
                        question, limit=4 if mode == 'smart' else 2,
                        source_hint=source_hint, request_id=request_id,
                    )
                except ReferenceLookupError as exc:
                    answer = str(exc)
                    timing.info("request_id=%s rag_reference_unavailable=1", request_id)
                    worker.answer_ready.emit(answer)
                    return answer, "Готово"
                except sqlite3.Error:
                    context = ""
            requested = perf_counter()
            last_update = None
            latest_text = ""
            timing.info("request_id=%s queue_ms=%.1f rag_ms=%.1f", request_id,
                        (rag_started - enqueued_at) * 1000, (requested - rag_started) * 1000)

            def on_text(text):
                nonlocal last_update, latest_text
                if cancelled.is_set():
                    raise GenerationError("Запит скасовано.")
                latest_text = text
                now = perf_counter()
                if last_update is None:
                    timing.info("request_id=%s llm_first_text_ms=%.1f", request_id, (now - requested) * 1000)
                refresh_interval = (0.12 if self.ui.profile == "eco"
                                    else 0.05)
                if last_update is None or now - last_update >= refresh_interval:
                    last_update = now
                    worker.progress.emit(text, now)

            if cancelled.is_set():
                raise GenerationError('Запит скасовано.')
            if speech_ended_at is not None:
                timing.info("request_id=%s llm_start_from_speech_end_ms=%.1f", request_id,
                            (perf_counter() - speech_ended_at) * 1000)
            timing.info("request_id=%s llm_request=1", request_id)
            try:
                if not has_image:
                    answer = llm.generate_answer(question, context, on_text=on_text,
                                                 mode=mode, request_id=request_id)
                else:
                    try:
                        prepared_bytes, prepared_mime = (encode_image(image) if image is not None
                                                         else (image_bytes, image_mime))
                    except ValueError as exc:
                        raise GenerationError(str(exc)) from None
                    intent = image_intent(image_instruction)
                    image_context = ""
                    if intent == EXPLAIN_CODE and source_hint:
                        try:
                            image_context = self.rag.get_source_preview(source_hint)
                        except sqlite3.Error:
                            pass
                    timing.info("request_id=%s screen_image_sent=1 intent=%s context_chars=%d",
                                request_id, intent, len(image_context))
                    answer = llm.generate_image_answer(
                        prepared_bytes, prepared_mime, on_text=on_text,
                        mode=mode, request_id=request_id, user_instruction=image_instruction,
                        context=image_context, intent=intent,
                    )
            except GenerationError:
                if latest_text and not cancelled.is_set():
                    worker.progress.emit(latest_text, perf_counter())
                raise
            timing.info("request_id=%s llm_complete_ms=%.1f", request_id, (perf_counter() - requested) * 1000)
            if cancelled.is_set():
                raise GenerationError("Запит скасовано.")
            worker.answer_ready.emit(answer)
            if not save_history or has_image:
                return answer, "Готово"
            try:
                self.db.save_qa(question, answer, llm.used_model)
            except Exception:
                return answer, "Відповідь отримано. Історію не збережено."
            return answer, "Готово"

        worker = TaskWorker(task, "Помилка обробки запиту AI.", request_id=request_id)
        worker.cancel_answer = cancelled
        worker.progress.connect(self.on_answer_progress)
        worker.answer_ready.connect(self._answer_ready)
        self.start_api(worker, self.on_answer)

    @Slot()
    def _show_answer_delay(self):
        if not self.closing and not self._answer_cleared and not self.answer_started:
            self.ui.lbl_status.setText("Відповідь затримується…")

    def _cancel_active_answer(self):
        worker = self.api
        cancelled = getattr(worker, "cancel_answer", None)
        if cancelled is None or len(self._retired_answers) >= MAX_RETIRED_ANSWERS:
            return
        cancelled.set()
        self._retired_answers[worker] = self.llm
        self.llm = self.llm_settings = None
        self.api = None

    def _accept_answer_signal(self):
        sender = self.sender()
        return (sender is None or sender is self.api) and not self._answer_cleared

    def _sync_history_controls(self, *, busy=False):
        total = len(self._answer_history)
        position = self._history_index + 1 if self._history_index >= 0 else 0
        self.ui.set_history_position(position, total, busy=busy or self.api is not None)

    def _remember_answer(self, answer):
        if not answer or (self._answer_history and self._answer_history[-1][0] == self._answer_request_id):
            return
        self._answer_history.append((self._answer_request_id, self._current_question, answer))
        if len(self._answer_history) > MAX_SESSION_ANSWERS:
            del self._answer_history[0]
        self._history_index = len(self._answer_history) - 1
        self.ui.set_history_items(self._answer_history)
        self._sync_history_controls()

    @Slot(int)
    def on_history_select(self, index):
        if self.api is not None or not 0 <= index < len(self._answer_history):
            return
        self._history_index = index
        _, question, answer = self._answer_history[index]
        self.ui.txt_question.setPlainText(question)
        self.ui.txt_answer.show_response(answer)
        self.ui.tabs.setCurrentWidget(self.ui.tab_live)
        self._sync_history_controls()

    @Slot(int)
    def on_history_step(self, step):
        if self.api is not None or not self._answer_history:
            return
        index = self._history_index if self._history_index >= 0 else len(self._answer_history)
        index = max(0, min(len(self._answer_history) - 1, index + step))
        self._history_index = index
        _, question, answer = self._answer_history[index]
        self.ui.txt_question.setPlainText(question)
        self.ui.txt_answer.show_response(answer)
        self._sync_history_controls()

    @Slot(str)
    def _answer_ready(self, answer):
        if self._accept_answer_signal():
            self._answer_hint_timer.stop()
            self._current_answer_text = answer
            self._remember_answer(answer)
            self.ui.txt_answer.show_response(answer)
            now = perf_counter()
            if not self.answer_started:
                if self._dev_request_started_at is not None:
                    self._timing_metric("first_text", (now - self._dev_request_started_at) * 1000)
                if self._speech_ended_at is not None:
                    self._timing_metric("speech_to_first", (now - self._speech_ended_at) * 1000)
            if self._dev_request_started_at is not None:
                self._timing_metric("complete", (now - self._dev_request_started_at) * 1000)
            timing.info("request_id=%s ui_complete_from_submit_ms=%.1f end_to_end_complete_ms=%.1f",
                        self._answer_request_id, (now - self._answer_enqueued_at) * 1000,
                        (now - self._speech_ended_at) * 1000 if self._speech_ended_at is not None else -1)
            self.ui.lbl_status.setText("Відповідь EXAM AI")

    @Slot(str, float)
    def on_answer_progress(self, text, emitted=None):
        if not self._accept_answer_signal():
            return
        self._answer_hint_timer.stop()
        first = not self.answer_started
        self.answer_started = True
        self._current_answer_text = text
        self.ui.txt_answer.show_streaming_response(text)
        if first:
            now = perf_counter()
            if self._dev_request_started_at is not None:
                self._timing_metric("first_text", (now - self._dev_request_started_at) * 1000)
            if self._speech_ended_at is not None:
                self._timing_metric("speech_to_first", (now - self._speech_ended_at) * 1000)
            timing.info("request_id=%s ui_answer_updated=1 ui_update_ms=%.1f voice first_ui_text_ms=%.1f",
                        self._answer_request_id, (now - emitted) * 1000 if emitted is not None else -1,
                        (now - self._speech_ended_at) * 1000 if self._speech_ended_at is not None else -1)
        self.ui.lbl_status.setText("EXAM AI відповідає…")

    @Slot(object, str)
    def on_answer(self, value, error):
        if not self._accept_answer_signal():
            return
        self._answer_hint_timer.stop()
        if error:
            if self.answer_started:
                self._remember_answer(self._current_answer_text)
                self.ui.lbl_status.setText(error)
            else:
                self.ui.txt_answer.setPlainText(error)
                self.ui.lbl_status.setText("Помилка AI")
        else:
            _, status = value
            self.ui.lbl_status.setText("Відповідь EXAM AI" if status == "Готово" else status)

    @Slot()
    def on_clear_all(self):
        self._recent_voice_item = None
        self._question_timer.stop()
        self._question_aggregator.clear()
        self._pending_screen_instruction = ""
        self._last_manual_instruction = ""
        self._history_index = -1
        self.pending_question = None
        self._voice_timer.stop()
        self._voice_wait_at = None
        self._answer_cleared = True
        self._answer_hint_timer.stop()
        self._pending_options = {}
        self._cancel_active_answer()
        self._sync_history_controls()
        self._last_auto_question = ""
        self.ui.lbl_status.setText("Готовий" if self.ui.tr.current_lang == "UA" else "Ready")

    @Slot(str)
    def handle_auto_summary(self, filename):
        if self.closing:
            return
        if not filename:
            self.ui.txt_docs_result.setPlainText("Виберіть документ.")
            return
        if self.api is not None:
            self.ui.txt_docs_result.setPlainText("AI зайнятий. Повторіть запит після завершення.")
            return
        llm = self.get_llm()
        self.ui.txt_docs_result.setPlainText("Створення автоконспекту…")

        def task():
            context = self.rag.get_summary_context(filename)
            if not context:
                raise GenerationError("У вибраному документі немає тексту.")
            return llm.generate_summary(context)

        self.start_api(TaskWorker(task, "Помилка обробки запиту AI."), self.on_summary_result)

    @Slot(object, str)
    def on_summary_result(self, value, error):
        if error:
            self.ui.txt_docs_result.setPlainText(error)
        else:
            self.ui.txt_docs_result.show_response(value)

    def start_api(self, worker, on_result):
        self.api = worker
        self.api.result.connect(on_result)
        self.api.finished.connect(self.api_done)
        self.api.start()

    @Slot()
    def api_done(self):
        worker = self.sender() or self.api
        if worker in self._retired_answers:
            engine = self._retired_answers.pop(worker)
            if engine is not None:
                engine.close()
            worker.deleteLater()
        elif worker is not None and worker is self.api:
            worker.deleteLater()
            self.api = None
            self._answer_hint_timer.stop()
            self._sync_history_controls()
            self._start_pending_answer()
        self.finish_close()

    def _start_pending_answer(self):
        pending, self.pending_question = self.pending_question, None
        options, self._pending_options = self._pending_options, {}
        if pending and (pending[1] or self.ui.auto_send.isChecked()):
            self.submit_question(*pending, **options)

    def docs_available(self):
        if self.closing:
            return False
        if self.docs is not None:
            self.ui.txt_docs_result.setPlainText("Зачекайте завершення операції з документом.")
            return False
        return True

    @Slot(str)
    def handle_search_keyword(self, keyword):
        keyword = keyword.strip()
        if keyword and self.docs_available():
            self.start_docs_task(lambda: self.rag.search_keyword(keyword))

    @Slot(str)
    def handle_file_deletion(self, filename):
        if self.docs_available():
            def task():
                self.db.delete_document_source(filename)
                return f"Конспект «{filename}» видалено."

            self.start_docs_task(task)

    def start_docs_task(self, task):
        worker = TaskWorker(task, "Помилка доступу до конспектів.")
        worker.result.connect(self.on_doc_result)
        self.start_docs(worker)

    @Slot(object, str)
    def on_doc_result(self, value, error):
        self.ui.txt_docs_result.setPlainText(error or value)

    @Slot(str)
    def handle_file_upload(self, filepath):
        if self.docs_available():
            self.ui.txt_docs_result.setPlainText("Індексація файлу…")
            worker = FileIndexerThread(self.rag, filepath)
            worker.result.connect(self.on_index_result)
            self.start_docs(worker)

    @Slot(str, int, str)
    def on_index_result(self, filename, count, error):
        message = error or f"«{filename}» збережено ({count} блоків)."
        self.ui.txt_docs_result.setPlainText(message)
        if not error:
            self._last_imported_source = filename

    def start_docs(self, worker):
        self.docs = worker
        self.docs.finished.connect(self.docs_done)
        self.docs.start()

    @Slot()
    def docs_done(self):
        self.docs.deleteLater()
        self.docs = None
        if not self.closing:
            self.ui.refresh_documents_list()
        self.finish_close()

    @Slot()
    def request_close(self):
        self.closing = True
        if self._selecting_screen:
            self._finish_screen_selection()
        self._voice_timer.stop()
        self._question_timer.stop()
        self._question_aggregator.clear()
        self.pending_question = None
        if self.audio:
            self.audio.requestInterruption()
        self._cancel_active_answer()
        self.ui.centralWidget().setEnabled(False)
        self.ui.lbl_status.setText("Завершую активні операції…")
        self.finish_close()

    def finish_close(self):
        if self.closing and not self._retired_answers and all(
                worker is None for worker in (self.audio, self.api, self.docs)):
            self.app.quit()

    @Slot()
    def shutdown(self):
        if self.timing_window is not None:
            self.capture_privacy.unregister(self.timing_window)
            self.timing_window.close()
        for handle in (self.hotkey_handle, self.recover_hotkey_handle):
            if handle is not None:
                try:
                    keyboard.remove_hotkey(handle)
                except (KeyError, ValueError):
                    pass
        if self.audio is not None:
            self.audio.requestInterruption()
        for worker in (self.audio, self.api, self.docs, *self._retired_answers):
            if worker is not None:
                worker.wait()
        for engine in self._retired_answers.values():
            if engine is not None:
                engine.close()
        if self.llm is not None:
            self.llm.close()

    def run(self):
        self.splash = SplashScreen(self.ui)
        self.capture_privacy.register(self.splash)
        self.splash.finished.connect(lambda: self.capture_privacy.unregister(self.splash))
        if self.config.get("privacy_mode"):
            self.apply_privacy(True, persist=False)
        self.splash.start_splash()
        return self.app.exec()


if __name__ == "__main__":
    if os.environ.get("PHANTOM_TIMING") == "1":
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        timing.addHandler(handler)
        timing.setLevel(logging.INFO)
    if os.environ.get("PHANTOM_AUDIO_DIAGNOSTICS") == "1":
        audio_log.addHandler(logging.StreamHandler())
        audio_log.setLevel(logging.INFO)
    app = QApplication(sys.argv)
    controller = MainController(app)
    sys.exit(controller.run())
