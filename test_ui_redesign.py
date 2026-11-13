"""The redesigned pages remain reachable and the low-load setting does work."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from config import ConfigManager
from database import DatabaseManager
from ui_main import MainWindow


class RedesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = ConfigManager(Path(self.folder.name) / "config.json")
        with patch("ui_main.AudioCapturer.get_available_devices", return_value=[]):
            self.window = MainWindow(self.config, DatabaseManager(Path(self.folder.name) / "db.sqlite"))
        self.addCleanup(self.window.deleteLater)
        self.window.config_mgr.set("reduce_motion", True)
        self.window.window_mode.expand_toggle()
        self.window.show()
        self.app.processEvents()

    def test_sidebar_composer_and_ghost_are_available(self):
        ui = self.window
        self.assertTrue(ui.btn_ghost.isVisible())
        self.assertTrue(ui.btn_ghost_lock.isVisible())
        ui.nav_buttons["docs"].click()
        self.assertIs(ui.tabs.currentWidget(), ui.tab_docs)
        ui.nav_buttons["history"].click()
        self.assertIs(ui.tabs.currentWidget(), ui.tab_history)
        ui.nav_buttons["live"].click()
        submitted = []
        ui.question_submitted.connect(submitted.append)
        ui.input_manual_question.setText("Що таке рекурсія?")
        ui.btn_send_q.click()
        self.assertEqual(submitted, ["Що таке рекурсія?"])

    def test_performance_mode_reduces_animation_work_and_persists(self):
        ui = self.window
        ui.nav_buttons["settings"].click()
        ui.combo_ui_performance.setCurrentIndex(
            ui.combo_ui_performance.findData("eco")
        )
        ui.save_settings()
        self.assertFalse(ui.audio_visualizer.timer.isActive())
        self.assertFalse(ui.btn_ghost._animation_enabled)
        self.assertFalse(ui.fade_effect.isEnabled())
        self.assertEqual(ConfigManager(self.config.filepath).get("ui_performance_mode"),
                         "eco")


if __name__ == "__main__":
    unittest.main()
