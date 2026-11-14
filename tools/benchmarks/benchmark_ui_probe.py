"""Local synthetic UI probe; no microphone, API requests or secrets."""

import json
import os
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))

import psutil
from PySide6.QtWidgets import QApplication, QSlider

from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.ui_main import MainWindow


def measure(func, count=8):
    results = []
    for _ in range(count):
        started = perf_counter()
        func()
        app.processEvents()
        results.append((perf_counter() - started) * 1000)
    return round(statistics.median(results), 2)


app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as directory:
    config = ConfigManager(Path(directory) / "config.json")
    database = DatabaseManager(Path(directory) / "db.sqlite")
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]), \
         patch("examai.ui.ui_main.set_windows_backdrop", return_value=False):
        started = perf_counter()
        window = MainWindow(config, database)
        window.show()
        app.processEvents()
        startup_ms = (perf_counter() - started) * 1000
        window.open_settings()
        window.settings_navigation.setCurrentRow(3)
        app.processEvents()
        sliders = window.settings_window.findChildren(QSlider)
        tint = sliders[1]
        process = psutil.Process()
        before_cpu = process.cpu_times()
        slider_ms = measure(lambda: tint.setValue(75 if tint.value() == 100 else 100), 12)
        after_cpu = process.cpu_times()
        slider_cpu_ms = ((after_cpu.user + after_cpu.system) -
                         (before_cpu.user + before_cpu.system)) * 1000
        combo = window.combo_speech_pace
        combo_ms = measure(lambda: (combo.showPopup(), combo.hidePopup()), 8)
        window.settings_window.hide()
        dock_ms = measure(window.window_mode.expand_toggle, 6)
        result = {
            "startup_ms": round(startup_ms, 2),
            "tint_step_median_ms": slider_ms,
            "tint_12_steps_cpu_ms": round(slider_cpu_ms, 2),
            "combo_open_close_median_ms": combo_ms,
            "dock_toggle_median_ms": dock_ms,
            "rss_mib": round(process.memory_info().rss / 1024**2, 2),
        }
        print(json.dumps(result, ensure_ascii=False))
        window.performance_monitor.timer.stop()
        window.deleteLater()
