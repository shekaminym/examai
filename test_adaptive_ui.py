import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from unittest.mock import patch

from config import ConfigManager
from database import DatabaseManager
from performance import Hardware, RuntimePolicy, recommend
from question_aggregator import QuestionAggregator
from ui_main import MainWindow
from window_mode import dock_geometry


def test_recommendations_and_runtime_hysteresis():
    assert recommend(Hardware(4, 2, 4, 3)) == "eco"
    assert recommend(Hardware(8, 4, 16, 8)) == "balanced"
    assert recommend(Hardware(12, 6, 32, 18, cuda_devices=1)) == "premium"
    assert recommend(Hardware(24, 12, 32, 1)) == "eco"
    policy = RuntimePolicy("premium")
    def sample(lag):
        return policy.observe(lag_ms=lag, available_gib=8, process_cpu=4)
    assert [sample(lag) for lag in [90, 90, 0, 90, 90, 90]] == [
        None, None, None, None, None, "balanced",
    ]
    assert all(sample(0) is None for _ in range(15))
    assert policy.profile == "balanced"


def test_auto_speech_fast_slow_mixed_and_capped():
    for question in ("Що таке граф?", "Что такое граф?", "Що таке двоичное дерево?"):
        speech = QuestionAggregator(mode="auto")
        speech.add_final(question, now=0)
        assert speech.finalize(now=.51) is None
        assert speech.finalize(now=.53).text == question

    speech = QuestionAggregator(mode="auto")
    speech.preview("Еее, що таке…")
    speech.add_final("Що таке…", now=0)
    speech.speech_started(now=2.2)
    assert speech.finalize(now=3) is None
    speech.add_final("мм, рекурсія і", now=3.5)
    speech.speech_started(now=5.1)
    speech.add_final("чем она отличается от циклу?", now=6.2)
    assert speech.finalize(now=10).text == "Що таке рекурсія і чем она отличается от циклу?"

    speech.add_final("Розкажи про функції", now=20)
    speech.speech_started(now=21)
    assert speech.finalize(now=31) is None
    assert speech.finalize(now=32).text == "Розкажи про функції"


def test_dock_geometry_on_non_primary_and_scaled_displays():
    for ratio in (1, 1.25, 1.5):
        area = QRect(-round(1920 / ratio), 35, round(1920 / ratio), round(1080 / ratio) - 35)
        left, center, right = (dock_geometry(area, side, 940, 620)
                               for side in ("left", "center", "right"))
        assert left.x() < center.x() < right.x()
        assert all(area.contains(rect) and rect.y() == 35 for rect in (left, center, right))


def test_config_and_ui_controls(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "settings.json")
    assert config.get("theme") == "midnight"
    assert config.get("speech_pace_mode") == "auto"
    with patch("ui_main.AudioCapturer.get_available_devices", return_value=[]), \
         patch("ui_main.set_windows_backdrop", return_value=False):
        window = MainWindow(config, DatabaseManager(tmp_path / "notes.db"))
        window.show()
        window.tabs.setCurrentWidget(window.tab_settings)
        app.processEvents()
        combo = window.combo_speech_pace
        previous = combo.currentIndex()
        wheel = QWheelEvent(QPointF(4, 4), QPointF(combo.mapToGlobal(QPoint(4, 4))),
                            QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(combo, wheel)
        assert combo.currentIndex() == previous
        assert not wheel.isAccepted()

        window.save_appearance("theme", "frosted_light")
        assert "#192337" in window.styleSheet()
        assert window.windowOpacity() == 1.0
        assert ConfigManager(config.filepath).get("theme") == "frosted_light"
        window.save_appearance("ui_performance_mode", "premium")
        window.on_performance_sample("eco", {"process_cpu": 3, "rss_mib": 200, "lag_ms": 0})
        assert window.profile == "premium"
        window.save_appearance("reduce_motion", True)
        assert window.motion_ms(260) == 0
        window.window_mode.expand_toggle()
        assert not window.tabs.isVisible()
        window.window_mode.expand_toggle()
        assert window.tabs.isVisible()
        window.window_mode.toggle()
        assert not window.window_mode.docked
        window.move(15, 25)
        window.window_mode.toggle()
        assert window.window_mode.docked
        assert config.get("floating_geometry")[:2] == [15, 25]
        window.window_mode.recover()
        assert config.get("dock_screen") == app.primaryScreen().name()
        window.deleteLater()
        app.processEvents()
