"""Regression tests for the installed 3.0 feedback."""
import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from examai.ai.conversation import Conversation
from examai.ai.rag_engine import RAGEngine, ReferenceLookupError
from examai.app import MainController
from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.windows.privacy_qt import CapturePrivacy


def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    with (patch("examai.app.ConfigManager", return_value=ConfigManager(tmp_path / "config.json")),
          patch("examai.app.DatabaseManager", return_value=DatabaseManager(tmp_path / "notes.db")),
          patch("examai.app.keyboard.add_hotkey", return_value=None),
          patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[])):
        return app, MainController(app)


def test_hidden_settings_does_not_create_native_window(tmp_path):
    app, ctl = controller(tmp_path)
    window = ctl.ui
    dialog = window.settings_window
    with patch.object(dialog, "winId", wraps=dialog.winId) as native_id:
        privacy = CapturePrivacy(app)
        privacy.register(dialog, require_hidden=False)
        privacy.set_enabled(True)
        assert not native_id.called
        window.show()
        window.open_settings()
        assert dialog.isVisible()
        window.showMinimized()
        QTest.qWait(220)
        assert not dialog.isVisible()
        window.open_settings()
        assert not dialog.isVisible()
        window.showNormal()
        app.processEvents()
        assert not dialog.isVisible()
        window.open_settings()
        assert dialog.isVisible()
    app.removeEventFilter(privacy)
    app.aboutToQuit.disconnect(ctl.shutdown)
    ctl.shutdown()
    window.deleteLater()


def test_exact_spoken_line_and_range_preserve_physical_lines(tmp_path):
    import pytest
    source = tmp_path / "code.txt"
    lines = [f"// line {number}" for number in range(1, 33)]
    lines[26] = ""
    lines[27] = "    for (int i = 0; i <= n; ++i) {"
    source.write_bytes(("\r\n".join(lines) + "\r\n").encode())
    rag = RAGEngine(DatabaseManager(tmp_path / "notes.db"))
    rag.index_file_sync(source)
    for query in ("Що написано в 28 рядку?", "Що робить 28 строчка?",
                  "Поясни рядок 28 у code.txt", "What does line 28 do?", "в 28 строчке в коде"):
        context = rag.retrieve_relevant_context(query, limit=2)
        assert "цільовий рядок 28" in context and "28:     for (int i = 0;" in context
        assert "\n27: \n" in context
    context = rag.retrieve_relevant_context("Объясни строки 25–30", limit=2)
    assert "цільові рядки 25–30" in context and "\n30: // line 30" in context and "31:" not in context
    with pytest.raises(ReferenceLookupError, match="32 рядків"):
        rag.retrieve_relevant_context("Поясни рядки 30–34")


def test_followups_reach_existing_fast_engine_and_clear(tmp_path):
    app, ctl = controller(tmp_path)
    requests = []

    class Engine:
        used_model = "fixture"

        def generate_answer(self, question, context, **_kwargs):
            requests.append((question, context))
            if question.startswith(("Чим", "Порівняй")):
                if context:
                    assert "диз'юнкція" in context and "кон'юнкція" in context
                    assert "диз'юнкція" in question and "кон'юнкція" in question
                    return "Диз'юнкція істинна, якщо істинний хоча б один операнд. Кон'юнкція — лише якщо істинні обидва."
                return "Missing context"
            return "Логічна операція."

    ctl.get_llm = lambda _mode: Engine()

    def send(question):
        ctl.submit_question(question, manual=True)
        loop = QEventLoop()
        ctl.api.finished.connect(loop.quit)
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        app.processEvents()
        assert ctl.api is None
        return ctl.ui.txt_answer.toPlainText()

    send("Що таке диз'юнкція?")
    send("Що таке кон'юнкція?")
    assert "обидва" in send("Чим вони відрізняються?")
    send("Як створити таблицю в SQLite?")
    assert "диз'юнкція" not in requests[-1][1]
    ctl.on_clear_all()
    assert send("Чим вони відрізняються?") == "Missing context"
    app.aboutToQuit.disconnect(ctl.shutdown)
    ctl.shutdown()
    ctl.ui.deleteLater()


def test_old_session_cannot_restore_memory():
    memory = Conversation()
    generation = memory.generation
    memory.clear()
    memory.remember("Old topic", "Old answer", generation)
    assert not memory.context("Чим вони відрізняються?")


def test_subscription_hover_keeps_neighbor_layout_and_honors_off(tmp_path):
    app, ctl = controller(tmp_path)
    window = ctl.ui
    window.show()
    window.config_mgr.set("reduce_motion", True)
    window.apply_performance_mode("premium")
    if window.window_mode.collapsed:
        window.window_mode.expand_toggle()
    page = window.tab_subscriptions
    window.tabs.setCurrentWidget(page)
    app.processEvents()
    card, neighbor = page.cards[:2]
    QTest.qWait(100)  # Settle the scroll area after its initial column reflow.
    geometry = neighbor.geometry()
    card.set_hover(1.0)
    app.processEvents()
    assert card.hover_animation.state() == card.hover_animation.State.Stopped
    assert card.outer.contentsMargins().top() == 3
    assert neighbor.geometry() == geometry
    window.config_mgr.set("reduce_motion", False)
    window.motion.system_reduced = False
    window.apply_performance_mode("premium")
    QTest.qWait(100)
    geometry = neighbor.geometry()
    card.set_hover(0.0)
    QTest.qWait(200)
    card.set_hover(1.0)
    QTest.qWait(200)
    assert card.outer.contentsMargins().top() == 0
    assert neighbor.geometry() == geometry
    app.aboutToQuit.disconnect(ctl.shutdown)
    window.deleteLater()


def test_recording_pulse_and_floating_geometry(tmp_path):
    from examai.ui.ui_main import MainWindow
    app, ctl = controller(tmp_path)
    ui = ctl.ui
    ui.show()
    ui.motion.system_reduced = False
    ui.config_mgr.save_config({"reduce_motion": False, "animation_intensity": "full"})
    ui.apply_performance_mode("premium")
    ui.set_listening(True)
    pulse = ui.btn_toggle_listen
    initial = pulse.phase
    QTest.qWait(120)
    assert pulse.pulse_timer.isActive() and pulse.phase != initial
    ui.config_mgr.set("animation_intensity", "off")
    ui.apply_performance_mode("premium")
    assert not pulse.pulse_timer.isActive() and pulse.recording
    ui.set_listening(False)
    assert not pulse.recording
    if ui.window_mode.docked:
        ui.window_mode.toggle()
    ui.resize(710, 500)
    ui.window_mode.remember()
    ui.config_mgr.set("animation_intensity", "full")
    ui.apply_performance_mode("premium")
    ui.resize(740, 520)
    ui.config_mgr.set("animation_intensity", "off")
    ui.apply_performance_mode("premium")
    assert (ui.width(), ui.height()) == (740, 520)
    ui.window_mode.remember()
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
        restored = MainWindow(ConfigManager(ui.config_mgr.filepath), ctl.db)
    assert (restored.width(), restored.height()) == (740, 520)
    restored.deleteLater()
    app.aboutToQuit.disconnect(ctl.shutdown)
    ui.deleteLater()


def test_pronoun_code_followups_keep_anchor_after_card_limit():
    from examai.ai.conversation import Conversation
    memory = Conversation()
    for i in range(25):
        memory.remember(f"Самостійне питання {i}", f"Відповідь {i}", memory.generation)
    evidence = "Точні рядки з файлу «code.txt» (цільовий рядок 5):\n6: #define MAX_DEGREE 10"
    memory.remember("Поясни п’яту строчку, що таке max degree?", "MAX_DEGREE обмежує степінь.",
                    memory.generation, evidence)
    for question in ("Для чого вона використовується?", "А що буде, якщо її збільшити?",
                     "Як вона впливає?", "Покажи, де вона використовується в коді"):
        prior = memory.relevant_turns(question)
        assert any("MAX_DEGREE" in answer for _, answer in prior)
        assert "Самостійне питання" not in memory.format_context(prior)
        assert memory.reference_context(question) == evidence
        if question.startswith("Покажи"):
            assert memory.code_usage_reference(question, prior, evidence) == ("MAX_DEGREE", "code.txt")
        memory.remember(question, "Попередня відповідь.", memory.generation)
    memory.clear()
    assert not memory.relevant_turns("Для чого вона використовується?")


def test_application_followup_retains_two_comparison_concepts():
    from examai.ai.conversation import Conversation
    memory = Conversation()
    for q, a in (("Що таке інтегрування?", "Інтеграл"), ("Що таке диференціювання?", "Похідна"),
                 ("Чим вони відрізняються?", "Порівняння")):
        memory.remember(q, a, memory.generation)
    assert [answer for _, answer in memory.relevant_turns("А де це застосовується?")] == [
        "Інтеграл", "Похідна", "Порівняння"
    ]
