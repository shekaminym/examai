"""Screen selection and multimodal Gemini request tests without network or disk images."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
from google import genai
from google.genai import types
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from config import ConfigManager
from database import DatabaseManager
from llm_engine import LLMEngine
from main import MainController
from screen_selection import ScreenSelection, encode_image, map_selection_to_pixels
from ui_main import MainWindow


class ScreenSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_high_dpi_coordinates_crop_real_pixels(self):
        pixmap = QPixmap(400, 200)
        pixmap.fill(QColor("red"))
        pixmap.setDevicePixelRatio(2)
        area = map_selection_to_pixels(QRect(50, 25, 50, 25), QRect(0, 0, 200, 100), pixmap)
        self.assertEqual(area, QRect(100, 50, 100, 50))
        self.assertEqual(pixmap.copy(area).toImage().size().width(), 100)

    def test_capture_encodes_png_without_writing_a_file(self):
        image = QImage(300, 120, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        data, mime = encode_image(image)
        self.assertEqual(mime, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertFalse(QImage().loadFromData(b"garbage"))
        decoded = QImage.fromData(data)
        self.assertEqual((decoded.width(), decoded.height()), (300, 120))

    def test_drag_selects_only_requested_pixels_and_escape_cancels(self):
        screen = MagicMock()
        screen.geometry.return_value = QRect(0, 0, 200, 100)
        pixmap = QPixmap(400, 200)
        pixmap.fill(QColor("red"))
        with QPainter(pixmap) as painter:
            painter.fillRect(QRect(200, 0, 200, 200), QColor("blue"))
        pixmap.setDevicePixelRatio(2)
        overlay = ScreenSelection(screen, pixmap)
        self.addCleanup(overlay.close)
        captured, cancelled = [], []
        overlay.selected.connect(captured.append)
        overlay.cancelled.connect(lambda: cancelled.append(True))
        overlay.show()
        QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(50, 20))
        QTest.mouseMove(overlay, QPoint(150, 80))
        QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(150, 80))
        self.assertEqual(len(captured), 1)
        image = captured[0]
        self.assertEqual((image.width(), image.height()), (200, 120))
        self.assertEqual(image.pixelColor(20, 60), QColor("red"))
        self.assertEqual(image.pixelColor(180, 60), QColor("blue"))
        QTest.keyClick(overlay, Qt.Key.Key_Escape)
        self.assertEqual(cancelled, [True])

    def test_gemini_receives_image_and_streams_answer(self):
        image = QImage(120, 80, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        image_bytes, mime = encode_image(image)
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            chunk = {"candidates": [{"content": {"parts": [{"text": "x = 2"}]},
                                     "finishReason": "STOP"}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\n")

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(httpx_client=transport),
        ))
        updates = []
        answer = engine.generate_image_answer(image_bytes, mime, on_text=updates.append)
        self.assertIn("x = 2", answer)
        self.assertTrue(updates)
        payload, = requests
        parts = payload["contents"][0]["parts"]
        inline = parts[1]["inlineData"]
        self.assertEqual(inline.get("mimeType", inline.get("mime_type")), "image/png")
        encoded = inline["data"]
        self.assertEqual(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)), image_bytes)
        self.assertIn("рукописний", payload["systemInstruction"]["parts"][0]["text"])

    def test_gemini_400_basic_retry_keeps_image(self):
        bodies = []

        def respond(request):
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 1:
                return httpx.Response(400, json={"error": {
                    "code": 400, "status": "INVALID_ARGUMENT", "message": "invalid argument",
                }})
            chunk = {"candidates": [{"content": {"parts": [{"text": "Готово"}]},
                                     "finishReason": "STOP"}]}
            return httpx.Response(200, text=f"data: {json.dumps(chunk)}\n\n")

        engine = LLMEngine("synthetic")
        self.addCleanup(engine.close)
        transport = engine.clients.enter_context(httpx.Client(transport=httpx.MockTransport(respond)))
        engine.gemini_client = engine.clients.enter_context(genai.Client(
            api_key="synthetic", vertexai=False,
            http_options=types.HttpOptions(httpx_client=transport),
        ))
        answer = engine.generate_image_answer(b"small fake image", "image/png",
                                              on_text=lambda text: None)
        self.assertIn("Готово", answer)
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            self.assertIn("inlineData", body["contents"][0]["parts"][1])

    def test_image_request_uses_existing_answer_worker_without_rag_or_history(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "config.json")
            database = DatabaseManager(Path(folder) / "db.sqlite")
            with (patch("main.ConfigManager", return_value=config),
                  patch("main.DatabaseManager", return_value=database),
                  patch("main.keyboard.add_hotkey", return_value=None),
                  patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
                controller = MainController(self.app)
            controller.start_api = MagicMock()
            controller.rag.retrieve_relevant_context = MagicMock()
            controller.get_llm = MagicMock()
            controller.get_llm.return_value.generate_image_answer.return_value = "⚡ [Gemini]\nx = 2"
            controller.get_llm.return_value.used_model = "synthetic"
            controller.config.set("save_answer_history", True)
            controller.db.save_qa = MagicMock()
            controller.submit_question("Зображення з екрана", manual=True,
                                       image_bytes=b"synthetic image", image_mime="image/png")
            worker, _ = controller.start_api.call_args.args
            self.assertEqual(worker.task(), ("⚡ [Gemini]\nx = 2", "Готово"))
            controller.rag.retrieve_relevant_context.assert_not_called()
            controller.db.save_qa.assert_not_called()
            controller.get_llm.return_value.generate_image_answer.assert_called_once()
            self.app.aboutToQuit.disconnect(controller.shutdown)

    def test_button_and_busy_worker_keep_selected_image_for_next_answer(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "config.json")
            database = DatabaseManager(Path(folder) / "db.sqlite")
            with patch("ui_main.AudioCapturer.get_available_devices", return_value=[]):
                window = MainWindow(config, database)
            window.resize(520, 700)
            window.config_mgr.set("reduce_motion", True)
            window.window_mode.expand_toggle()
            window.show()
            self.app.processEvents()
            clicks = []
            window.screen_select_signal.connect(lambda: clicks.append(True))
            window.btn_select_screen.click()
            self.assertEqual(clicks, [True])
            self.assertEqual(window.btn_select_screen.text(), "ВИДІЛИТИ НА ЕКРАНІ")
            self.assertFalse(hasattr(window, "btn_copy_ans"))
            self.assertEqual(window.btn_select_screen.objectName(), "BtnPrimary")
            self.assertEqual(window.btn_select_screen.y(), window.btn_toggle_listen.y())
            self.assertGreater(window.btn_select_screen.x(), window.btn_toggle_listen.x())
            self.assertGreater(window.btn_select_screen.width(),
                               window.btn_select_screen.fontMetrics().horizontalAdvance(
                                   window.btn_select_screen.text()) + 16)
            window.hide()
            window.deleteLater()

            with (patch("main.ConfigManager", return_value=config),
                  patch("main.DatabaseManager", return_value=database),
                  patch("main.keyboard.add_hotkey", return_value=None),
                  patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
                controller = MainController(self.app)
            controller.api = MagicMock()
            controller.submit_question("Зображення з екрана", manual=True,
                                       image_bytes=b"synthetic image", image_mime="image/png")
            self.assertEqual(controller._pending_options["image_bytes"], b"synthetic image")
            self.assertEqual(controller._pending_options["image_mime"], "image/png")
            controller.on_clear_all()
            self.assertFalse(controller._pending_options)
            self.assertIsNone(controller.pending_question)
            self.app.aboutToQuit.disconnect(controller.shutdown)

    def test_qimage_is_encoded_in_answer_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "config.json")
            database = DatabaseManager(Path(folder) / "db.sqlite")
            with (patch("main.ConfigManager", return_value=config),
                  patch("main.DatabaseManager", return_value=database),
                  patch("main.keyboard.add_hotkey", return_value=None),
                  patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
                controller = MainController(self.app)
            controller.start_api = MagicMock()
            controller.get_llm = MagicMock()
            controller.get_llm.return_value.generate_image_answer.return_value = "⚡ [Gemini]\nГотово"
            image = QImage(200, 100, QImage.Format.Format_RGB32)
            image.fill(QColor("white"))
            controller.submit_question("Зображення з екрана", manual=True, image=image)
            worker, _ = controller.start_api.call_args.args
            worker.start()
            self.assertTrue(worker.wait(3000))
            data, mime = controller.get_llm.return_value.generate_image_answer.call_args.args[:2]
            self.assertTrue(data.startswith(b"\x89PNG"))
            self.assertEqual(mime, "image/png")
            self.app.aboutToQuit.disconnect(controller.shutdown)

    def test_button_hides_window_selects_region_and_restores_window(self):
        with tempfile.TemporaryDirectory() as folder:
            config = ConfigManager(Path(folder) / "config.json")
            config.set("gemini_api_key", "synthetic")
            database = DatabaseManager(Path(folder) / "db.sqlite")
            with (patch("main.ConfigManager", return_value=config),
                  patch("main.DatabaseManager", return_value=database),
                  patch("main.keyboard.add_hotkey", return_value=None),
                  patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
                controller = MainController(self.app)
            controller.submit_question = MagicMock()
            controller.ui.show()
            screen = MagicMock()
            screen.geometry.return_value = QRect(0, 0, 200, 100)
            screenshot = QPixmap(200, 100)
            screenshot.fill(QColor("green"))
            screen.grabWindow.return_value = screenshot
            with (patch.object(QApplication, "screens", return_value=[screen]),
                  patch("main.QTimer.singleShot", side_effect=lambda delay, fn: fn())):
                controller.ui.btn_select_screen.click()
            self.assertTrue(controller._selecting_screen)
            self.assertFalse(controller.ui.isVisible())
            overlay, = controller._screen_overlays
            QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(100, 70))
            self.assertFalse(controller._selecting_screen)
            self.assertTrue(controller.ui.isVisible())
            self.assertTrue(controller.ui.btn_select_screen.isEnabled())
            self.assertEqual(controller.submit_question.call_args.kwargs["image"].size().width(), 80)
            self.assertEqual(controller.submit_question.call_args.kwargs["image"].size().height(), 50)
            self.app.aboutToQuit.disconnect(controller.shutdown)
            controller.ui.hide()


if __name__ == "__main__":
    unittest.main()
