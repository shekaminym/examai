import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.ui_main import MainWindow


def test_cards_stream_history_error_and_switch_modes(tmp_path):
    app = QApplication.instance() or QApplication([])
    cfg = ConfigManager(tmp_path / "config.json")
    cfg.save_config({"reduce_motion": True})
    with patch("examai.ui.ui_main.AudioCapturer.get_available_devices", return_value=[]):
        ui = MainWindow(cfg, DatabaseManager(tmp_path / "db.sqlite"))
    ui.show()
    app.processEvents()
    cards = ui.live_cards
    base = ui.geometry()
    assert not cards.isVisible()

    cards.preview("Що таке")
    cards.preview("Що таке граф?")
    cards.fit()
    assert cards.question_text.toPlainText() == "Що таке граф?"
    assert cards.question.isVisible() and not cards.answer.isVisible()
    assert ui.header_widget.y() == 7
    assert ui.geometry().topLeft() == base.topLeft() and ui.height() > base.height()

    cards.begin("one", "Що таке граф?")
    cards.progress("one", "Граф")
    assert cards.answer.isVisible()
    cards.progress("one", "Граф — це множина вершин і ребер.")
    assert cards.answer_text.toPlainText().endswith("ребер.")
    long_answer = "\n".join(f"Рядок {i}" for i in range(200))
    cards.ready("one", "Що таке граф?", long_answer)
    app.processEvents()
    compact_height = ui.height()
    assert cards.answer_text.verticalScrollBar().maximum() > 0
    cards.toggle_expanded()
    assert ui.height() > compact_height
    assert ui.screen().availableGeometry().contains(ui.geometry())
    cards.copy.click()
    assert app.clipboard().text() == long_answer

    ui.window_mode.toggle()
    assert not cards.isVisible()
    ui.window_mode.toggle()
    assert cards.answer.isVisible() and cards.answer_text.copyable_text() == long_answer

    cards.begin("two", "Інше питання")
    cards.progress("one", "Застарілий текст")
    cards.ready("one", "Старий запит", "Застаріла відповідь")
    assert cards.answer_text.toPlainText() == ""
    cards.error("two", "Мережева помилка")
    assert cards.answer_text.copyable_text() == long_answer
    cards.dismiss()
    assert ui.height() == base.height()
    cards.ready("one", "Що таке граф?", long_answer, history=True)
    assert cards.answer.isVisible()
    cards.history.click()
    assert ui.tabs.currentWidget() is ui.tab_history
    ui.deleteLater()
    app.processEvents()
