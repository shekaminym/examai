import json
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import numpy as np
from docx import Document
from google import genai
from google.genai import types
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtWidgets import QApplication

from audio_capturer import AudioCapturer
from config import ConfigManager
from database import DatabaseManager
from llm_engine import GenerationError, LLMEngine, PartialGenerationError
from main import AudioWorker, MainController, TaskWorker
from rag_engine import RAGEngine
from response_view import ResponseView, render_formula
from stt_engine import STTEngine


class StorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_interactive_retrieval_releases_a_blocked_read_quickly(self):
        db = DatabaseManager(self.root / "locked.db")
        db.replace_document("notes.txt", ["вектор і матриця"])
        rag = RAGEngine(db)
        with db.connection() as holder:
            holder.execute("PRAGMA journal_mode=DELETE")
            holder.execute("BEGIN EXCLUSIVE")
            started = time.perf_counter()
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                rag.retrieve_relevant_context("вектор")
            self.assertLess(time.perf_counter() - started, 1)
        self.assertIn("notes.txt", rag.retrieve_relevant_context("вектор"))

    def test_legacy_database_rank_search_replace_and_delete(self):
        path = self.root / "old.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, source TEXT)")
            conn.execute("INSERT INTO documents (content, source) VALUES (?, ?)",
                         ("ВЕКТОР % та матриця", "old.txt"))
        db = DatabaseManager(path)
        rag = RAGEngine(db)
        db.replace_document("new.txt", ["Термодинаміка"] * 5)
        self.assertIn("old.txt", rag.retrieve_relevant_context("Що таке вектор?"))
        self.assertEqual(rag.retrieve_relevant_context(') " * -'), "")
        self.assertIn("ВЕКТОР", rag.search_keyword("вектор"))
        self.assertNotIn("new.txt", rag.search_keyword("%"))
        self.assertNotIn("Термодинаміка", rag.get_summary_context("old.txt"))
        db.replace_document("old.txt", ["Інтеграл"])
        self.assertEqual(rag.retrieve_relevant_context("вектор"), "")
        self.assertIn("Інтеграл", rag.retrieve_relevant_context("інтеграл"))
        db.delete_document_source("old.txt")
        self.assertEqual(rag.retrieve_relevant_context("інтеграл"), "")

    def test_index_formats_and_failed_reindex_preserve_document(self):
        rag = RAGEngine(DatabaseManager(self.root / "notes.db"))
        source = self.root / "notes.txt"
        source.write_text("Матриця " * 120, encoding="utf-8-sig")
        self.assertGreater(rag.index_file_sync(source), 1)
        source.write_bytes(b"\xff")
        with self.assertRaises(UnicodeError):
            rag.index_file_sync(source)
        self.assertIn("Матриця", rag.get_summary_context(source.name))
        docx = self.root / "notes.docx"
        doc = Document()
        doc.add_table(rows=1, cols=1).cell(0, 0).text = "Множина"
        doc.save(docx)
        self.assertEqual(rag.index_file_sync(docx), 1)
        self.assertIn("Множина", rag.retrieve_relevant_context("множина"))

    def test_pdf_with_text_and_image_only_equivalent(self):
        rag = RAGEngine(DatabaseManager(self.root / "notes.db"))
        source = self.root / "lecture.pdf"
        with PdfWriter() as writer:
            page = writer.add_blank_page(width=300, height=200)
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            })
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 30 150 Td (Linear algebra) Tj ET")
            page.replace_contents(stream)
            writer.write(source)
        self.assertEqual(rag.index_file_sync(source), 1)
        self.assertIn("Linear algebra", rag.retrieve_relevant_context("algebra"))
        with PdfWriter() as writer:
            writer.add_blank_page(width=300, height=200)
            writer.write(source)
        with self.assertRaises(ValueError):
            rag.index_file_sync(source)
        self.assertIn("Linear algebra", rag.get_summary_context(source.name))

    def test_summary_bounded_and_samples_both_ends(self):
        db = DatabaseManager(self.root / "notes.db")
        rag = RAGEngine(db)
        db.replace_document("long.txt", ["Початок"] + ["x" * 750] * 50 + ["Кінець"])
        summary = rag.get_summary_context("long.txt")
        self.assertLessEqual(len(summary), 14000)
        self.assertIn("Початок", summary)
        self.assertIn("Кінець", summary)

    def test_legacy_config_and_session_keys(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({"audio_device_index": 12, "gemini_api_key": "synthetic"}))
        config = ConfigManager(path)
        self.assertEqual(config.get("audio_device_index"), -1)
        self.assertEqual(config.get("gemini_api_key"), "synthetic")
        config.save_config({"gemini_api_key": "synthetic-other", "audio_device_index": 3})
        self.assertNotIn("synthetic", path.read_text())
        self.assertEqual(ConfigManager(path).get("audio_device_index"), 3)


class AudioTests(unittest.TestCase):
    @patch("audio_capturer.pa.PyAudio")
    def test_select_loopback_never_open_mic_and_bound_queue(self, factory):
        backend = factory.return_value
        backend.get_default_wasapi_loopback.return_value = {
            "isLoopbackDevice": True, "maxInputChannels": 2,
            "defaultSampleRate": 48000, "index": 5,
        }
        capture = AudioCapturer()
        capture.start()
        self.addCleanup(capture.stop)
        kwargs = backend.open.call_args.kwargs
        self.assertEqual(kwargs["input_device_index"], 5)
        backend.get_default_input_device_info.assert_not_called()
        data = np.full((4800, 2), 0.25, dtype=np.float32).tobytes()
        for _ in range(45):
            kwargs["stream_callback"](data, 4800, None, 0)
        self.assertEqual(capture.audio_queue.qsize(), 40)
        self.assertEqual(capture.dropped, 5)
        _, mono, rms = capture.get_audio_data()
        self.assertEqual(mono.shape, (4800,))
        self.assertAlmostEqual(rms, 0.25)

    @patch("audio_capturer.pa.PyAudio")
    def test_open_failure_terminates_backend(self, factory):
        backend = factory.return_value
        backend.get_device_info_by_index.return_value = {
            "isLoopbackDevice": False, "maxInputChannels": 2,
            "defaultSampleRate": 48000, "index": 5,
        }
        backend.open.side_effect = OSError
        capture = AudioCapturer()
        with self.assertRaises(OSError):
            capture.start(5, False)
        backend.terminate.assert_called_once()
        self.assertIsNone(capture.stream)

    @patch("stt_engine.WhisperModel")
    def test_lazy_model_resampling_and_language(self, model):
        model.return_value.transcribe.return_value = (
            iter([SimpleNamespace(text=" питання ")]), SimpleNamespace(language="uk")
        )
        engine = STTEngine()
        model.assert_not_called()
        data = np.ones(44100, dtype=np.float32) * 0.1
        self.assertEqual(engine.transcribe_chunk(data, "auto", 44100), ("питання", "uk"))
        args, kwargs = model.return_value.transcribe.call_args
        self.assertEqual(len(args[0]), 16000)
        self.assertEqual(args[0].dtype, np.float32)
        self.assertIsNone(kwargs["language"])
        self.assertEqual(kwargs["beam_size"], 3)

    def test_silence_finalizes_once_and_discards_short_noise(self):
        stt = MagicMock()
        stt.transcribe_chunk.return_value = ("готове питання", "uk")
        worker = AudioWorker(stt, "uk", None, False)
        samples = np.ones(1600, dtype=np.float32)
        levels = [0] * 15 + [0.1] + [0] * 12 + [0.1] * 5 + [0] * 25
        blocks = iter((0, samples * level, level) for level in levels)
        received = threading.Event()
        finished = []

        def on_final(text):
            finished.append(text)
            received.set()

        def read_audio(timeout):
            block = next(blocks, None)
            if block is None:
                received.wait(2)
                worker.isInterruptionRequested.return_value = True
            return block

        worker.capturer.get_audio_data = read_audio
        worker.isInterruptionRequested = MagicMock(return_value=False)
        worker.question_finished.connect(on_final, Qt.ConnectionType.DirectConnection)
        worker.process_audio()
        self.assertEqual(finished, ["готове питання"])


class APITests(unittest.TestCase):
    def test_gemini_stream_accumulates_text(self):
        def respond(request):
            self.assertIn(":streamGenerateContent", request.url.path)
            chunks = [
                {"candidates": [{"content": {"parts": [{"text": "First"}]}}]},
                {"candidates": [{"content": {"parts": [{"text": " answer"}]},
                                 "finishReason": "STOP"}]},
            ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(chunk)}\n\n"
                                                    for chunk in chunks))

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(httpx_client=transport),
        ))
        updates = []
        answer = engine.generate_answer("test", "", on_text=updates.append)
        self.assertTrue(updates[0].endswith("First"))
        self.assertTrue(answer.endswith("First answer"))
        self.assertEqual(updates[-1], answer)

    def test_gemini_retries_only_failure_to_connect_before_send(self):
        for error_type, expected_requests, expected_status in (
            (httpx.ConnectError, 2, "Answer"),
            (httpx.ReadTimeout, 1, "початок відповіді"),
            (httpx.ConnectTimeout, 1, "підключитися"),
        ):
            with self.subTest(error_type=error_type):
                requests = []

                def respond(request):
                    requests.append(request)
                    if len(requests) == 1:
                        raise error_type("synthetic")
                    chunk = {"candidates": [{"content": {"parts": [{"text": "Answer"}]},
                                             "finishReason": "STOP"}]}
                    return httpx.Response(200, text="data: " + json.dumps(chunk) + chr(10) * 2)

                engine = LLMEngine("synthetic")
                try:
                    transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
                    engine.gemini_client = engine.clients.enter_context(genai.Client(
                        api_key="synthetic", http_options=types.HttpOptions(httpx_client=transport),
                    ))
                    if expected_requests == 2:
                        self.assertIn(expected_status, engine.generate_answer("test", "", on_text=lambda _: None))
                    else:
                        with self.assertRaisesRegex(GenerationError, expected_status):
                            engine.generate_answer("test", "", on_text=lambda _: None)
                    self.assertEqual(len(requests), expected_requests)
                finally:
                    engine.close()

    def test_gemini_socket_timeout_preserves_received_text_without_retry(self):
        requests = []

        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b'data: {"candidates":[{"content":{"parts":[{"text":"First"}]}}]}\n\n'
                raise httpx.ReadTimeout("private test transport detail")

        def respond(request):
            requests.append(request)
            return httpx.Response(200, stream=BrokenStream())

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", http_options=types.HttpOptions(httpx_client=transport),
        ))
        updates = []
        with self.assertRaises(PartialGenerationError) as failure:
            engine.generate_answer("test", "", on_text=updates.append, request_id="test-id")
        self.assertTrue(updates[0].endswith("First"))
        self.assertNotIn("private", str(failure.exception))
        self.assertEqual(len(requests), 1)

    def test_gemini_sdk_sends_optional_reference_and_redacts_http_error(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(403, json={"error": {
                "code": 403, "message": "sensitive raw detail", "status": "PERMISSION_DENIED",
            }})

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(httpx_client=transport),
        ))
        with self.assertRaisesRegex(GenerationError, r"^Gemini \(HTTP 403\).+доступ") as failure:
            engine.generate_answer("Що таке радіус-вектор?", "Декартові координати")
        self.assertNotIn("sensitive", str(failure.exception))
        request, = requests
        self.assertIn("/models/gemini-3.5-flash-lite:generateContent", request.url.path)
        payload = json.loads(request.content)
        self.assertNotIn("thinkingConfig", payload["generationConfig"])
        text = payload["contents"][0]["parts"][0]["text"]
        self.assertIn("Довідкові уривки з конспектів", text)


    def test_gemini_recovers_from_rejected_parameters_and_reuses_basic_request(self):
        bodies = []

        def respond(request):
            self.assertNotIn("X-Server-Timeout", request.headers)
            body = json.loads(request.content)
            read_timeout = request.extensions["timeout"]["read"]
            self.assertGreater(read_timeout, 0)
            self.assertLessEqual(read_timeout, 8 if "generationConfig" in body else 12)
            bodies.append(body)
            if "generationConfig" in body:
                return httpx.Response(400, json={"error": {
                    "code": 400, "status": "INVALID_ARGUMENT", "message": "invalid argument",
                }})
            self.assertEqual(set(body), {"contents"})
            prompt = body["contents"][0]["parts"][0]["text"]
            self.assertIn("Запитання:", prompt)
            chunk = {"candidates": [{"content": {"parts": [{"text": "Answer"}]},
                                     "finishReason": "STOP"}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\n")

        client = httpx.Client

        def make_client(**kwargs):
            return client(transport=httpx.MockTransport(respond), **kwargs)

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        updates = []
        with patch("llm_engine.httpx.Client", side_effect=make_client):
            for _ in range(2):
                result = engine.generate_answer("test", "", on_text=updates.append,
                                                deadline=time.perf_counter() + 8)
                self.assertTrue(result.endswith("Answer"))
        self.assertEqual(len(bodies), 3)
        self.assertEqual(len(updates), 2)

    def test_gemini_uses_rest_thinking_field_and_safe_400_detail(self):
        requests = []

        def respond(request):
            requests.append(request)
            thinking = json.loads(request.content)["generationConfig"]["thinkingConfig"]
            self.assertEqual(thinking, {"thinkingLevel": "minimal"})
            return httpx.Response(400, json={"error": {
                "code": 400, "status": "INVALID_ARGUMENT", "message": "private raw detail",
                "details": [{"reason": "API_KEY_INVALID"}],
            }})

        engine = LLMEngine("synthetic", "gemini-3.5-flash")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(httpx_client=transport),
        ))
        with self.assertRaisesRegex(GenerationError, "ключ API недійсний") as failure:
            engine.generate_answer("test", "", on_text=lambda text: None,
                                   deadline=time.perf_counter() + 8)
        self.assertNotIn("private", str(failure.exception))
        self.assertEqual(len(requests), 1)

    def test_gemini_429_uses_second_model_and_remembers_cooldown(self):
        requests = []

        def respond(request):
            requests.append(request)
            if "gemini-3.5-flash-lite" in request.url.path:
                return httpx.Response(429, json={"error": {
                    "code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
                }})
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [
                    {"text": "Радіус-вектор: r = x eₓ + y eᵧ + z e_z."},
                ]},
            }]})

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(
                httpx_client=transport, retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ))
        answer = engine.generate_answer("Формола, радіус-вектор.", "")
        self.assertIn("r = x", answer)
        self.assertEqual(engine.used_model, "gemini-3.1-flash-lite")
        self.assertEqual(len(requests), 2)
        self.assertIn("/models/gemini-3.5-flash-lite:", requests[0].url.path)
        self.assertIn("/models/gemini-3.1-flash-lite:", requests[1].url.path)
        for request in requests:
            payload = json.loads(request.content)
            system = payload["systemInstruction"]["parts"][0]["text"]
            self.assertIn("Формола, радіус-вектор", system)
            self.assertIn("познач своє припущення", system)
            self.assertIn("застосування називай прикладами", system)
            text = payload["contents"][0]["parts"][0]["text"]
            self.assertEqual(text, "Запитання:\nФормола, радіус-вектор.")
        engine.generate_answer("Що таке матриця?", "")
        self.assertEqual(len(requests), 3)
        self.assertIn("/models/gemini-3.1-flash-lite:", requests[2].url.path)

    def test_gemini_server_timeout_does_not_make_second_slow_request(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(504, json={"error": {
                "code": 504, "message": "deadline", "status": "DEADLINE_EXCEEDED",
            }})

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(
                httpx_client=transport, retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ))
        with self.assertRaisesRegex(GenerationError, "HTTP 504"):
            engine.generate_answer("Питання", "")
        self.assertEqual(len(requests), 1)

    def test_fast_gemini_503_tries_alternate_model_once(self):
        requests = []

        def respond(request):
            requests.append(request)
            if "gemini-3.5-flash-lite" in request.url.path:
                return httpx.Response(503, json={"error": {
                    "code": 503, "message": "overloaded", "status": "UNAVAILABLE",
                }})
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": "Рекурсивний метод викликає себе."}]},
            }]})

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(
                httpx_client=transport, retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ))
        answer = engine.generate_answer("Що таке рекурсивний метод?", "")
        self.assertIn("Рекурсивний метод", answer)
        self.assertEqual(engine.used_model, "gemini-3.1-flash-lite")
        self.assertEqual(len(requests), 2)
        engine.generate_answer("Інше питання", "")
        self.assertEqual(len(requests), 3)
        self.assertIn("gemini-3.1-flash-lite", requests[-1].url.path)

    def test_slow_gemini_503_does_not_add_another_wait(self):
        requests = []
        clock = {"now": 0.0}

        def respond(request):
            requests.append(request)
            clock["now"] = 3.0
            return httpx.Response(503, json={"error": {
                "code": 503, "message": "overloaded", "status": "UNAVAILABLE",
            }})

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(
                httpx_client=transport, retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ))
        with patch("llm_engine.perf_counter", side_effect=lambda: clock["now"]):
            with self.assertRaisesRegex(GenerationError, "HTTP 503"):
                engine.generate_answer("Питання", "")
        self.assertEqual(len(requests), 1)

    def test_both_gemini_models_at_limit_fail_without_repeat_requests(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(429, json={"error": {
                "code": 429, "message": "private quota detail", "status": "RESOURCE_EXHAUSTED",
            }})

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(
                httpx_client=transport, retry_options=types.HttpRetryOptions(attempts=1),
            ),
        ))
        with self.assertRaisesRegex(GenerationError, "HTTP 429") as first:
            engine.generate_answer("Питання", "")
        self.assertNotIn("private", str(first.exception))
        self.assertEqual(len(requests), 2)
        with self.assertRaisesRegex(GenerationError, "обох моделей"):
            engine.generate_answer("Друге питання", "")
        self.assertEqual(len(requests), 2)


class QtTests(unittest.TestCase):
    def test_response_renders_math_and_markdown_without_loading_external_images(self):
        app = QApplication.instance() or QApplication([])
        view = ResponseView()
        view.resize(480, 240)
        source = (
            "Формула:\n\n" r"$$\vec{r} = x\vec{i} + y\vec{j} + z\vec{k}$$"
            "\n\n**Позначення**\n\n"
            "* " r"$\vec{r}$ — радіус-вектор." "\n"
            "* " r"\(x, y, z\) — координати." "\n\n"
            "![Зовнішній ресурс](https://example.invalid/probe.png)"
        )
        view.show_response(source)
        document = view.document()
        rendered = document.toHtml()
        self.assertEqual(rendered.count('src="formula:'), 3)
        self.assertIn("<ul", rendered)
        self.assertNotIn("**Позначення**", document.toPlainText())
        self.assertNotIn(r"\vec{r}", document.toPlainText())
        self.assertEqual(view.copyable_text(), source)
        view.setPlainText("Помилка Gemini")
        self.assertEqual(view.copyable_text(), "Помилка Gemini")
        view.clear()
        self.assertEqual(view.copyable_text(), "")

    def test_response_converts_pixel_font_at_widget_dpi(self):
        app = QApplication.instance() or QApplication([])
        view = ResponseView()
        font = view.document().defaultFont()
        font.setPixelSize(13)
        view.document().setDefaultFont(font)
        self.assertEqual(view.document().defaultFont().pointSize(), -1)
        view.show_response("## Heading" + chr(10) * 2 + "**Answer**")
        self.assertAlmostEqual(view.document().defaultFont().pointSizeF(),
                               13 * 72 / view.logicalDpiY())
        self.assertIn("Answer", view.toPlainText())
        self.assertIsNotNone(app)

    def test_response_keeps_unsupported_math_and_code(self):
        app = QApplication.instance() or QApplication([])
        view = ResponseView()
        view.show_response(r"$\unknowncommand{x}$ and" + " `$x$`")
        self.assertIn(r"$\unknowncommand{x}$", view.toPlainText())
        self.assertIn("$x$", view.toPlainText())
        self.assertNotIn("formula:", view.document().toHtml())

    def test_response_renders_equation_with_implies_from_screen_answer(self):
        app = QApplication.instance() or QApplication([])
        view = ResponseView()
        source = ("Підставимо:\n\n"
                  r"$$t^2 = \frac{2y}{a_y} \implies t^4 = \frac{4y^2}{a_y^2}$$")
        view.show_response(source)
        self.assertIn('src="formula:', view.document().toHtml())
        self.assertNotIn(r"\implies", view.toPlainText())
        self.assertEqual(view.copyable_text(), source)

    def test_complete_formulas_render_during_stream_without_repeated_math_work(self):
        app = QApplication.instance() or QApplication([])
        view = ResponseView()
        prefix = "Формула кон’юнкції:\n"
        source = prefix + r"$$L = A \land B$$ (або $L = A \cdot B$)"
        with patch("response_view.render_formula", wraps=render_formula) as renderer:
            view.show_streaming_response(prefix + r"$$L = A \land")
            self.assertNotIn('src="formula:', view.document().toHtml())
            view.show_streaming_response(source)
            self.assertEqual(view.document().toHtml().count('src="formula:'), 2)
            view.show_streaming_response(source + "\nПозначення: A, B.")
            view.show_response(source + "\nПозначення: A, B.")
            self.assertEqual(renderer.call_count, 2)
        self.assertEqual(view.copyable_text(), source + "\nПозначення: A, B.")

    @patch("main.keyboard.add_hotkey", return_value=None)
    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    def test_controller_shows_conjunction_formula_before_answer_finishes(self, devices, hotkey):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            with (patch("main.ConfigManager", return_value=ConfigManager(Path(folder) / "config.json")),
                  patch("main.DatabaseManager", return_value=DatabaseManager(Path(folder) / "db.sqlite"))):
                controller = MainController(app)
            controller._answer_request_id = "formula-test"
            controller.on_answer_progress(r"Формула: $$L = A \land B$$")
            self.assertIn('src="formula:', controller.ui.txt_answer.document().toHtml())
            self.assertEqual(controller.ui.txt_answer.copyable_text(),
                             r"Формула: $$L = A \land B$$")
            app.aboutToQuit.disconnect(controller.shutdown)

    def test_common_logic_aliases_render_without_changing_the_source(self):
        for expression in (r"A \land B", r"A \lor B", r"\lnot A",
                           r"A \le B", r"A \ge B", r"A \implies B"):
            with self.subTest(expression=expression):
                self.assertIsNotNone(render_formula(expression, False))

    @patch("main.keyboard.add_hotkey", return_value=None)
    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    @patch("main.DatabaseManager")
    @patch("main.ConfigManager")
    def test_manual_question_works_without_audio_and_has_queue_priority(
        self, config, database, devices, hotkey
    ):
        with tempfile.TemporaryDirectory() as directory:
            config.return_value = ConfigManager(Path(directory) / "config.json")
            database.return_value = DatabaseManager(Path(directory) / "db.sqlite")
            app = QApplication.instance() or QApplication([])
            controller = MainController(app)
            controller.start_api = MagicMock()
            controller.get_llm = MagicMock()
            controller.get_llm.return_value.generate_answer.return_value = "відповідь"
            controller.get_llm.return_value.used_model = "synthetic"
            controller.submit_manual_question("  Що таке радіус-вектор?  ")
            worker, _ = controller.start_api.call_args.args
            task = worker.task
            self.assertEqual(task(), ("відповідь", "Готово"))
            controller.get_llm.return_value.generate_answer.assert_called_once_with(
                "Що таке радіус-вектор?", "", on_text=ANY, mode="fast", request_id=ANY
            )
            controller.api = MagicMock()
            controller.submit_question("розшифровка", manual=False)
            controller.config.set("answer_mode", "smart")
            controller.submit_manual_question("виправлене питання")
            controller.config.set("answer_mode", "fast")
            controller.submit_question("інша розшифровка", manual=False)
            self.assertEqual(controller.pending_question, ("виправлене питання", True))
            controller.ui.auto_send.setChecked(False)
            controller.api_done()
            self.assertEqual(controller.start_api.call_count, 2)
            second, _ = controller.start_api.call_args.args
            second.task()
            controller.get_llm.return_value.generate_answer.assert_called_with(
                "виправлене питання", "", on_text=ANY, mode="smart", request_id=ANY
            )
            self.assertEqual(controller.db.get_history(), [])
            app.aboutToQuit.disconnect(controller.shutdown)

    @patch("main.keyboard.add_hotkey", return_value=None)
    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    @patch("main.DatabaseManager")
    @patch("main.ConfigManager")
    def test_one_clear_button_clears_both_and_ignores_late_answer(
        self, config, database, devices, hotkey
    ):
        with tempfile.TemporaryDirectory() as directory:
            config.return_value = ConfigManager(Path(directory) / "config.json")
            database.return_value = DatabaseManager(Path(directory) / "db.sqlite")
            app = QApplication.instance() or QApplication([])
            controller = MainController(app)
            self.addCleanup(app.aboutToQuit.disconnect, controller.shutdown)
            controller.ui.txt_question.setPlainText("Питання")
            controller.ui.txt_answer.setPlainText("Відповідь")
            controller.pending_question = ("наступне", False)
            controller.ui.btn_clear_all.click()
            self.assertEqual(controller.ui.txt_question.toPlainText(), "")
            self.assertEqual(controller.ui.txt_answer.toPlainText(), "")
            self.assertIsNone(controller.pending_question)
            self.assertFalse(hasattr(controller.ui, "btn_clear_q"))
            self.assertFalse(hasattr(controller.ui, "btn_clear_a"))
            controller.on_answer_progress("пізній фрагмент")
            controller.on_answer(("пізня відповідь", "Готово"), "")
            self.assertEqual(controller.ui.txt_answer.toPlainText(), "")

    @patch("main.keyboard.add_hotkey", return_value=None)
    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    @patch("main.DatabaseManager")
    @patch("main.ConfigManager")
    def test_identical_auto_question_is_not_billed_twice(
        self, config, database, devices, hotkey
    ):
        with tempfile.TemporaryDirectory() as directory:
            config.return_value = ConfigManager(Path(directory) / "config.json")
            database.return_value = DatabaseManager(Path(directory) / "db.sqlite")
            app = QApplication.instance() or QApplication([])
            controller = MainController(app)
            self.addCleanup(app.aboutToQuit.disconnect, controller.shutdown)
            controller.audio = MagicMock()
            controller.audio.isInterruptionRequested.return_value = False
            controller.submit_question = MagicMock()
            controller.handle_question("Що таке рекурсія?")
            controller.handle_question("  Що   таке рекурсія?  ")
            controller.submit_question.assert_called_once_with("Що таке рекурсія?", manual=False)
            controller.submit_manual_question("Що таке рекурсія?")
            self.assertEqual(controller.submit_question.call_count, 2)

    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    @patch("main.DatabaseManager")
    @patch("main.ConfigManager")
    def test_cancelled_stream_does_not_replace_next_answer(self, config, database, devices):
        with tempfile.TemporaryDirectory() as directory:
            config.return_value = ConfigManager(Path(directory) / "config.json")
            database.return_value = DatabaseManager(Path(directory) / "db.sqlite")
            app = QApplication.instance() or QApplication([])
            controller = MainController(app)
            self.addCleanup(app.aboutToQuit.disconnect, controller.shutdown)
            release = threading.Event()
            controller.config.set("save_answer_history", True)
            engine = MagicMock(used_model="synthetic")

            def answer(question, context, *, on_text, mode, request_id):
                if question == "slow":
                    on_text("partial")
                    release.wait(4)
                    on_text("obsolete")
                    return "obsolete"
                return "new answer"

            engine.generate_answer.side_effect = answer
            controller.get_llm = MagicMock(return_value=engine)
            loop = QEventLoop()
            observed = []
            timer = QTimer()
            timer.setInterval(5)

            def observe():
                if not observed and controller.ui.txt_answer.toPlainText() == "partial":
                    observed.append(True)
                    controller.ui.btn_clear_all.click()
                    controller.submit_manual_question("fast")
                    release.set()
                if observed and controller.api is None and not controller._retired_answers:
                    loop.quit()

            timer.timeout.connect(observe)
            controller.submit_manual_question("slow")
            try:
                timer.start()
                QTimer.singleShot(4500, loop.quit)
                loop.exec()
            finally:
                release.set()
                for worker in (*controller._retired_answers, controller.api):
                    if worker is not None:
                        worker.wait(5000)
                app.processEvents()
            self.assertTrue(observed)
            self.assertEqual(controller.ui.txt_answer.toPlainText(), "new answer")
            self.assertEqual([row["question"] for row in controller.db.get_history()], ["fast"])

    @patch("main.keyboard.add_hotkey", return_value=None)
    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    @patch("main.DatabaseManager")
    @patch("main.ConfigManager")
    def test_question_shows_text_before_generation_and_history_complete(
        self, config, database, devices, hotkey
    ):
        with tempfile.TemporaryDirectory() as directory:
            config.return_value = ConfigManager(Path(directory) / "config.json")
            database.return_value = DatabaseManager(Path(directory) / "db.sqlite")
            app = QApplication.instance() or QApplication([])
            controller = MainController(app)
            self.addCleanup(app.aboutToQuit.disconnect, controller.shutdown)
            release = threading.Event()
            engine = MagicMock(used_model="synthetic")

            def answer(question, context, *, on_text, mode, request_id):
                time.sleep(2.15)
                on_text("First")
                if not release.wait(3):
                    raise GenerationError("Test did not observe the first text.")
                return "**First answer**"

            engine.generate_answer.side_effect = answer
            controller.get_llm = lambda mode: engine
            loop = QEventLoop()
            timer = QTimer()
            timer.setInterval(5)
            early_text = []

            def observe():
                if not early_text and controller.ui.txt_answer.toPlainText() == "First":
                    early_text.append(controller.ui.lbl_status.text())
                    release.set()
                if controller.api is None:
                    loop.quit()

            timer.timeout.connect(observe)
            timeout = QTimer()
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            controller.audio = MagicMock()
            controller.audio.isInterruptionRequested.return_value = False
            with self.assertLogs("phantom.timing", level="INFO") as logs:
                controller.handle_question("test", time.perf_counter() - 2.5)
                self.assertEqual(controller.ui.lbl_status.text(), "Відповідь затримується…")
                worker = controller.api
                try:
                    timer.start()
                    timeout.start(4500)
                    loop.exec()
                finally:
                    release.set()
                    if controller.api is not None:
                        worker.wait(5000)
                        app.processEvents()
            self.assertTrue(any("voice first_ui_text_ms=" in line for line in logs.output))
            self.assertEqual(early_text, ["Відповідаю…"])
            self.assertEqual(controller.ui.txt_answer.toPlainText(), "First answer")
            self.assertEqual(controller.ui.txt_answer.copyable_text(), "**First answer**")

    @patch("main.keyboard.add_hotkey", return_value=None)
    @patch("ui_main.AudioCapturer.get_available_devices", return_value=[])
    @patch("main.DatabaseManager")
    @patch("main.ConfigManager")
    def test_close_waits_for_active_worker(self, config, database, devices, hotkey):
        with tempfile.TemporaryDirectory() as directory:
            config.return_value = ConfigManager(Path(directory) / "config.json")
            database.return_value = DatabaseManager(Path(directory) / "db.sqlite")
            app = QApplication.instance() or QApplication([])
            controller = MainController(app)
            released = threading.Event()
            controller.start_api(TaskWorker(lambda: released.wait(2) and "готово", "Помилка"),
                                 controller.on_doc_result)
            worker = controller.api
            QTimer.singleShot(0, controller.request_close)
            QTimer.singleShot(30, released.set)
            QTimer.singleShot(3000, app.quit)
            controller.run()
            self.assertTrue(released.is_set())
            self.assertTrue(worker is not controller.api)
            self.assertEqual(controller.ui.txt_docs_result.toPlainText(), "готово")
            app.aboutToQuit.disconnect(controller.shutdown)


if __name__ == "__main__":
    unittest.main()

