import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from branding import ANSWER_PREFIX, brand_answer
from config import ConfigManager
from database import DatabaseManager
from main import MainController
from question_aggregator import QuestionAggregator, is_noise, is_unreliable_transcript


def test_speech_sequences_and_repeated_final():
    for fillers in ("еее, ну, ем", "ээ, ну, то есть", "м-м-м, ага", "ну… тобто"):
        assert is_noise(fillers)
    for question in ("RAG", "SQL", "Що таке SQL?", "Что такое RAG?", "Що таке runtime?"):
        assert not is_unreliable_transcript(question)
    for fragment in ("Поясни", "Что это такое", "Це було і"):
        assert is_unreliable_transcript(fragment)
    agg = QuestionAggregator(mode="auto")
    agg.add_final("Що таке", now=1)
    assert agg.preview("SQL?") == "Що таке SQL?"
    agg.add_final("SQL?", now=2)
    agg.speech_started(now=2.2)
    assert not agg.add_final("Що таке SQL?", now=2.4)
    assert agg.finalize(now=3).text == "Що таке SQL?"


def test_settings_reused_and_transcript_logging_is_opt_in(tmp_path, capsys):
    app = QApplication.instance() or QApplication([])
    config = ConfigManager(tmp_path / "config.json")
    with (patch("main.ConfigManager", return_value=config),
          patch("main.DatabaseManager", return_value=DatabaseManager(tmp_path / "db.sqlite")),
          patch("main.keyboard.add_hotkey", return_value=None),
          patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
        controller = MainController(app)
    ui = controller.ui
    ui.show()
    window = ui.settings_window
    controller.audio = MagicMock()
    controller.audio.isInterruptionRequested.return_value = False
    ui.set_listening(True)
    for _ in range(20):
        ui.compact_settings.click()
        assert ui.settings_window is window and window.isVisible() and not window.isModal()
        assert ui.input_gemini_key.echoMode() == QLineEdit.EchoMode.Password
        window.close()
        assert ui.isVisible() and ui.compact_listen.text() == "СТОП"

    controller.on_live_text("Що таке SQL")
    assert "SQL" not in capsys.readouterr().err
    ui.transcript_diagnostics.setChecked(True)
    controller.on_live_text("Що таке SQL")
    controller.handle_question("Що таке SQL?", request_id="test")
    logs = capsys.readouterr().err
    assert "[STT partial]" in logs and "[STT final] request=test" in logs
    assert brand_answer("⚡ [Gemini]\nSQL") == ANSWER_PREFIX + "SQL"
    assert brand_answer("Термін [Gemini] у цитаті") == "Термін [Gemini] у цитаті"
    controller._question_timer.stop()
    app.aboutToQuit.disconnect(controller.shutdown)
    controller.audio = None
    ui.deleteLater()
    controller.deleteLater()
