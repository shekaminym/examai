"""The notes tab stays usable at the application's compact window size."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.ui_main import MainWindow


class DocsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, folder):
        with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
            return MainWindow(ConfigManager(Path(folder) / "config.json"),
                              DatabaseManager(Path(folder) / "db.sqlite"))

    def test_notes_controls_fit_without_clipped_buttons(self):
        with tempfile.TemporaryDirectory() as folder:
            window = self.make_window(folder)
            window.resize(520, 700)
            window.config_mgr.set("reduce_motion", True)
            window.window_mode.expand_toggle()
            window.show()
            window.tabs.setCurrentWidget(window.tab_docs)
            self.app.processEvents()
            self.assertGreaterEqual(window.btn_search.width(), 88)
            self.assertLessEqual(window.btn_search.mapTo(window.tab_docs,
                                window.btn_search.rect().bottomRight()).x(), window.tab_docs.width())
            self.assertGreater(window.btn_summary.width(), window.btn_search.width())
            self.assertLessEqual(window.btn_summary.mapTo(window.tab_docs,
                                window.btn_summary.rect().bottomRight()).x(), window.tab_docs.width())
            window.hide()

    def test_header_flags_and_search_remain_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            ConfigManager(Path(folder) / "config.json").set("window_mode", "floating")
            window = self.make_window(folder)
            window.resize(640, 700)
            window.show()
            window.tabs.setCurrentWidget(window.tab_docs)
            self.app.processEvents()
            self.assertFalse(window.btn_ua.icon().isNull())
            self.assertFalse(window.btn_eng.icon().isNull())
            self.assertEqual(window.btn_ua.text(), "")
            self.assertEqual(window.btn_eng.text(), "")
            self.assertEqual(window.btn_ua.iconSize().width(), 20)
            self.assertEqual(window.btn_eng.iconSize().height(), 13)
            self.assertLess(window.lbl_ai_provider.geometry().right(),
                            window.btn_ghost.geometry().left())
            self.assertIs(window.btn_send_q.parentWidget(), window.composer_shell)
            self.assertLess(window.btn_send_q.width(), window.composer_shell.height())
            self.assertGreaterEqual(window.input_search.height(), 38)
            self.assertGreaterEqual(window.btn_search.height(), 38)
            self.assertIn("border-radius: 19px", window.styleSheet())
            window.hide()

    def test_upload_uses_windows_dialog_and_emits_selected_file(self):
        with tempfile.TemporaryDirectory() as folder:
            window = self.make_window(folder)
            uploaded = []
            window.file_uploaded_signal.connect(uploaded.append)
            with patch("examai.ui.ui_main.QFileDialog.getOpenFileName", return_value=("C:/notes/lesson.pdf", "")) as choose:
                window.upload_file()
            self.assertEqual(uploaded, ["C:/notes/lesson.pdf"])
            self.assertNotIn("options", choose.call_args.kwargs)
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
