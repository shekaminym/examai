"""Requested settings labels and full transparency remain usable."""

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.ui_main import MainWindow


def test_settings_labels_and_sections(tmp_path):
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "config.json")
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
        window = MainWindow(config, DatabaseManager(tmp_path / "notes.db"))
    try:
        sections = [window.settings_navigation.item(index).text()
                    for index in range(window.settings_navigation.count())]
        assert "Продуктивність" not in sections
        assert "Інтерфейс і теми" in sections
        assert window.combo_ui_performance is not None
        assert window.combo_whisper.itemText(window.combo_whisper.findData("base")) == "Швидше"
        assert window.combo_whisper.itemText(window.combo_whisper.findData("small")) == "Точніше"
        assert window.remember_gemini_key.text() == "Запам'ятати API Key"
        labels = [label.text() for label in window.settings_window.findChildren(QLabel)]
        assert not any("Ключ Gemini діє" in text or "Мова: Gemini Live" in text
                       or "Системне розмиття" in text for text in labels)
        assert not any("Системне розмиття" in box.text() for box in
                       window.settings_window.findChildren(QCheckBox))
        with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[
            {"name": "Mic", "index": 2, "loopback": False}
        ]):
            window.combo_audio_source.setCurrentIndex(1)
        assert window.combo_device_select.findText("Mic") >= 0
        window.change_language("ENG")
        assert window.remember_gemini_key.text() == "Remember API Key"
    finally:
        window.hide()
        app.processEvents()


def test_full_transparency_and_blur_disabled(tmp_path):
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "config.json")
    config.set("system_blur", True)  # Old saved preferences cannot re-enable it.
    assert config.get("system_blur") is False
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
        window = MainWindow(config, DatabaseManager(tmp_path / "notes.db"))
    try:
        assert window.transparency_slider.maximum() == 100
        window.transparency_slider.setValue(100)
        assert window.windowOpacity() == 0.0
        assert ConfigManager(config.filepath).get("window_transparency") == 100
        with patch("examai.ui.ui_main.set_windows_backdrop", return_value=False) as backdrop:
            window.apply_backdrop()
            backdrop.assert_called_with(window, False)
        window.transparency_slider.setValue(0)
        assert window.windowOpacity() == 1.0
    finally:
        window.hide()
        app.processEvents()
