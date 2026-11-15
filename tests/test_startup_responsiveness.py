"""The splash must keep painting while startup storage work is slow."""

import os
from time import monotonic, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from unittest.mock import patch

from examai.app import BackgroundCall, SplashScreen, pump_startup_splash
from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.ui_main import MainWindow


def test_splash_animates_during_slow_background_preparation():
    app = QApplication.instance() or QApplication([])
    splash = SplashScreen(reduced_motion=False)
    splash.show_loading()
    worker = BackgroundCall(lambda: (sleep(.3), "ready")[1])
    completed = []
    worker.ready.connect(lambda value: completed.append((value, QThread.currentThread() == app.thread())))
    worker.start()
    QTest.qWait(125)
    assert splash.spinner._angle != 0
    deadline = monotonic() + 2
    while not completed and monotonic() < deadline:
        QTest.qWait(20)
    assert completed == [("ready", True)]
    splash.finish_splash()
    assert not splash.spinner.timer.isActive()


def test_startup_window_does_not_wait_for_audio_device_scan(tmp_path):
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "config.json")
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices") as scan:
        window = MainWindow(config, DatabaseManager(tmp_path / "notes.db"), startup_devices=[])
        scan.assert_not_called()
        assert window.combo_device_select.count() == 1
        window.refresh_audio_devices([{"name": "Speakers", "index": 4, "loopback": True}])
        assert window.combo_device_select.count() == 2
        window.hide()
        app.processEvents()


def test_splash_keeps_moving_while_main_window_is_built(tmp_path):
    app = QApplication.instance() or QApplication([])
    splash = SplashScreen(reduced_motion=False)
    splash.show_loading()
    app.processEvents()
    first_frame = splash.grab().toImage()
    config = ConfigManager(tmp_path / "config.json")
    database = DatabaseManager(tmp_path / "notes.db")
    try:
        with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
            window = MainWindow(config, database, startup_devices=[],
                                startup_pump=lambda: pump_startup_splash(app, splash))
        assert splash.spinner._angle >= 24
        assert splash.grab().toImage() != first_frame
        window.hide()
    finally:
        splash.finish_splash()
