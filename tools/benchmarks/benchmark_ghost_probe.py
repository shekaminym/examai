"""Short local Windows flag test, without API keys or global hotkeys."""

import json
import os
import statistics
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()))

from PySide6.QtWidgets import QApplication
from examai.app import MainController
from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.windows.privacy_utils import get_capture_exclusion

app = QApplication([])
with tempfile.TemporaryDirectory() as directory:
    config = ConfigManager(Path(directory) / "config.json")
    config.save_config({"ghost_hide_taskbar": True, "reduce_motion": True})
    database = DatabaseManager(Path(directory) / "db.sqlite")
    with patch("examai.app.ConfigManager", return_value=config), \
         patch("examai.app.DatabaseManager", return_value=database), \
         patch("examai.app.keyboard.add_hotkey", return_value=1), \
         patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
        controller = MainController(app)
        controller.ui.show()
        app.processEvents()
        timings = []
        states = []
        for enabled in (True, False, True, False, True, False):
            started = perf_counter()
            controller.apply_privacy(enabled, persist=False)
            app.processEvents()
            timings.append((perf_counter() - started) * 1000)
            states.append({
                "requested": enabled,
                "confirmed": controller.capture_privacy.verified,
                "affinity": get_capture_exclusion(int(controller.ui.winId())).affinity,
                "message": controller.capture_privacy.message,
                "window_type": int(controller.ui.windowType()),
            })
        print(json.dumps({"median_ms": round(statistics.median(timings), 2),
                          "states": states}, ensure_ascii=False))
        controller.ui.hide()
        controller.shutdown()
