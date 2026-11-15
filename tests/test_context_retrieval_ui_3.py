"""Reproduce the reported follow-up, uploaded code, and UI paths."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEventLoop, QTimer, Qt

from examai.ai.conversation import Conversation
from examai.ai.rag_engine import RAGEngine, ReferenceLookupError, requested_lines
from examai.config import ConfigManager
from examai.database import DatabaseManager
from examai.ui.appearance import THEMES
from tests.test_context_stability import controller


CODE = Path(__file__).parent / "fixtures" / "code.txt"


def wait_for(app, worker):
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    expired = []
    timeout = QTimer(singleShot=True, interval=5000)
    timeout.timeout.connect(lambda: (expired.append(True), loop.quit()))
    timeout.start()
    loop.exec()
    timeout.stop()
    app.processEvents()
    assert not expired, "The operation did not complete within five seconds"


def test_answer_memory_keeps_list_and_drops_old_topic():
    memory = Conversation()
    answer = ("Рекурсія — це виклик функції з самої себе. Перед переліком наведено "
              "декілька уточнень. " * 2 +
              "Основні види: пряма, непряма, хвостова, деревоподібна рекурсія.")
    assert len(answer) > 105
    memory.remember("Які існують види рекурсії?", answer, memory.generation)
    assert "деревоподібна" in memory.context("Чим вони відрізняються?")
    memory.remember("Як працює SQLite?", "SQLite зберігає таблиці у файлі.", memory.generation)
    context = memory.context("Чому це так?")
    assert "SQLite" in context and "рекурсії" not in context


def test_third_question_refers_to_an_earlier_type():
    memory = Conversation()
    memory.remember("Що таке пряма і взаємна рекурсія?",
                    "Пряма викликає себе, взаємна викликає іншу функцію.", memory.generation)
    memory.remember("Чим вони відрізняються?",
                    "У взаємній рекурсії бере участь кілька функцій.", memory.generation)
    for question in ("Покажи короткий приклад другого виду.",
                     "Как это работает во втором случае?",
                     "Show an example of the second kind."):
        context = memory.context(question)
        assert "Що таке пряма і взаємна рекурсія?" in context
        assert "Чим вони відрізняються?" in context
    assert not memory.context("Що таке похідна другого порядку?")


def test_supplied_code_exact_lines_and_ambiguity(tmp_path):
    rag = RAGEngine(DatabaseManager(tmp_path / "notes.db"))
    rag.index_file_sync(CODE)
    for question, line in (
        ("Що в коді в першій строчці?", "1: #include <stdio.h>"),
        ("Що на першому рядку?", "1: #include <stdio.h>"),
        ("Что написано в первой строке?", "1: #include <stdio.h>"),
        ("Що робить 27-ма строчка у коді?", '27:     printf("Coefficients'),
        ("Що написано в 28 рядку?", "28:     for (int i = 0;"),
        ("What does line 27 do?", '27:     printf("Coefficients'),
        ("Explain line 28 in code.txt", "28:     for (int i = 0;"),
        ("В соти, що в коді в сотій строчці?", '100:     puts("Equivalence: OK");'),
    ):
        assert requested_lines(question) is not None
        assert line in rag.retrieve_relevant_context(question, limit=2)
    rows = rag.retrieve_relevant_context("Поясни рядки 25–30", limit=2)
    assert "25: void print_array" in rows and "29:         printf" in rows and "30:     }" in rows
    assert "24:" not in rows and "31:" not in rows
    count = rag.db_mgr.get_line_sources()[0][1]
    with pytest.raises(ReferenceLookupError, match=rf"{count} рядків"):
        rag.retrieve_relevant_context(f"Що робить {count + 1}-ма строчка?")

    second = tmp_path / "other.txt"
    second.write_text("int other(void) {\n\nreturn 0;\n}\n", encoding="utf-8")
    rag.index_file_sync(second)
    with pytest.raises(ReferenceLookupError, match="Уточніть назву"):
        rag.retrieve_relevant_context("Що робить 2-ма строчка?")
    assert "2: " in rag.retrieve_relevant_context("Що робить 2-ма строчка в other.txt?")


def test_import_then_typed_and_voice_requests_use_actual_code(tmp_path):
    app, ctl = controller(tmp_path)
    calls = []

    class Engine:
        used_model = "fixture"

        def generate_answer(self, question, context, **_options):
            calls.append((question, context))
            return "Рядок 27 друкує заголовок списку коефіцієнтів."

    ctl.get_llm = lambda _mode: Engine()
    try:
        ctl.handle_file_upload(str(CODE))
        assert ctl.docs is not None
        wait_for(app, ctl.docs)
        selected = ctl.ui.list_docs.currentItem()
        assert selected is not None and selected.text() == "code.txt"

        ctl.ui.input_manual_question.setText("Що в коді в першій строчці?")
        ctl.ui.btn_send_q.click()
        assert ctl.api is not None
        wait_for(app, ctl.api)
        assert "1: #include <stdio.h>" in calls[-1][1]

        ctl.ui.input_manual_question.setText("Що робить 27-ма строчка у коді?")
        ctl.ui.btn_send_q.click()
        assert ctl.api is not None
        wait_for(app, ctl.api)
        assert '27:     printf("Coefficients' in calls[-1][1]
        assert "Рядок 27" in ctl.ui.txt_answer.toPlainText()

        ctl.audio = MagicMock()
        ctl.audio.isInterruptionRequested.return_value = False
        ctl.ui.auto_send.setChecked(True)
        ctl.handle_question("Що написано в 28 рядку?", request_id="voice-line-28")
        ctl._finalize_voice_question(force=True)
        assert ctl.api is not None
        wait_for(app, ctl.api)
        assert "28:     for (int i = 0;" in calls[-1][1]

        another = tmp_path / "second.txt"
        another.write_text("one\n\ntwo\n", encoding="utf-8")
        ctl.handle_file_upload(str(another))
        wait_for(app, ctl.docs)
        assert ctl.ui.list_docs.currentItem().text() == "second.txt"
        assert ctl.config.get("active_document_source") == "second.txt"
        assert ConfigManager(ctl.config.filepath).get("active_document_source") == "second.txt"
        before = len(calls)
        ctl.ui.input_manual_question.setText("Що робить 2-ма строчка?")
        ctl.ui.btn_send_q.click()
        wait_for(app, ctl.api)
        assert len(calls) == before + 1
        assert "2: " in calls[-1][1]

        ctl.ui.input_manual_question.setText("В соти, що в коді в сотій строчці?")
        ctl.ui.btn_send_q.click()
        wait_for(app, ctl.api)
        assert '100:     puts("Equivalence: OK");' in calls[-1][1]
        assert "code.txt" in calls[-1][1]

        item = ctl.ui.list_docs.findItems("code.txt", Qt.MatchFlag.MatchExactly)[0]
        ctl.ui.list_docs.setCurrentItem(item)
        assert ctl.config.get("active_document_source") == "code.txt"
        ctl.ui.input_manual_question.setText("Що робить 27-ма строчка?")
        ctl.ui.btn_send_q.click()
        wait_for(app, ctl.api)
        assert '27:     printf("Coefficients' in calls[-1][1]
    finally:
        ctl.audio = None
        app.aboutToQuit.disconnect(ctl.shutdown)
        app.removeEventFilter(ctl.capture_privacy)
        ctl.ui.deleteLater()


def test_topic_index_routes_notes_and_survives_restart(tmp_path):
    db_path = tmp_path / "notes.db"
    rag = RAGEngine(DatabaseManager(db_path))
    physics = tmp_path / "physics.txt"
    physics.write_text("Електромагнітна індукція виникає при зміні магнітного потоку.\n" * 5,
                       encoding="utf-8")
    biology = tmp_path / "biology.txt"
    biology.write_text("Фотосинтез перетворює енергію сонячного світла на хімічну.\n" * 5,
                       encoding="utf-8")
    rag.index_file_sync(physics)
    rag.index_file_sync(biology)
    profiles = rag.db_mgr.get_document_profiles()
    assert set(profiles) == {"physics.txt", "biology.txt"}
    assert profiles["biology.txt"]["char_count"] > 100
    assert "фотоси" in profiles["biology.txt"]["keywords"]

    # An active file is remembered, but a clearly different topic wins.
    answer_context = rag.retrieve_relevant_context(
        "Як відбувається фотосинтез?", source_hint="physics.txt"
    )
    assert "biology.txt" in answer_context and "Фотосинтез" in answer_context
    assert "physics.txt" not in answer_context
    assert "physics.txt" in rag.retrieve_relevant_context(
        "Що у файлі?", source_hint="physics.txt"
    )

    reopened = RAGEngine(DatabaseManager(db_path))
    assert "biology.txt" in reopened.retrieve_relevant_context("Поясни фотосинтез")
    with reopened.db_mgr.connection() as conn:
        conn.execute("DELETE FROM document_profiles WHERE source = 'biology.txt'")
    migrated = DatabaseManager(db_path)
    assert "biology.txt" in migrated.get_document_profiles()
    migrated.delete_document_source("biology.txt")
    assert "biology.txt" not in migrated.get_document_profiles()


def test_same_named_lectures_keep_distinct_sources(tmp_path):
    rag = RAGEngine(DatabaseManager(tmp_path / "notes.db"))
    first = tmp_path / "physics" / "lecture.txt"
    second = tmp_path / "biology" / "lecture.txt"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("Магнітний потік змінюється.", encoding="utf-8")
    second.write_text("Фотосинтез відбувається у хлоропластах.", encoding="utf-8")
    _, first_source = rag.index_file_sync(first, return_source=True)
    _, second_source = rag.index_file_sync(second, return_source=True)
    assert first_source != second_source
    assert set(rag.db_mgr.get_document_profiles()) == {first_source, second_source}
    assert "хлоропластах" in rag.retrieve_relevant_context(
        "Фотосинтез у lecture.txt", source_hint=second_source
    )
    first.write_text("Індукція залежить від зміни магнітного потоку.", encoding="utf-8")
    _, reindexed = rag.index_file_sync(first, return_source=True)
    assert reindexed == first_source
    assert "Індукція" in rag.retrieve_relevant_context(
        "індукція у lecture.txt", source_hint=first_source
    )


def test_named_long_note_uses_relevant_passage_not_only_its_beginning(tmp_path):
    rag = RAGEngine(DatabaseManager(tmp_path / "notes.db"))
    note = tmp_path / "lecture.txt"
    note.write_text("Вступ до курсу. " * 160 +
                    "\nФотосинтез у хлоропластах утворює глюкозу з вуглекислого газу.\n",
                    encoding="utf-8")
    rag.index_file_sync(note)
    context = rag.retrieve_relevant_context("Що в lecture.txt про фотосинтез?")
    assert "Фотосинтез" in context and "lecture.txt" in context


def test_completed_answer_reaches_next_voice_question(tmp_path):
    app, ctl = controller(tmp_path)
    calls = []
    long_answer = ("Рекурсія викликає ту саму функцію з меншим випадком. "
                   "Перед класифікацією треба зазначити базовий випадок. "
                   "Основні види: пряма, взаємна, хвостова, деревоподібна рекурсія.")

    class Engine:
        used_model = "fixture"

        def generate_answer(self, question, context, **_options):
            calls.append((question, context))
            return long_answer if len(calls) == 1 else "Види розрізняються формою виклику."

    ctl.get_llm = lambda _mode: Engine()
    try:
        ctl.ui.input_manual_question.setText("Які існують види рекурсії?")
        ctl.ui.btn_send_q.click()
        wait_for(app, ctl.api)
        ctl.audio = MagicMock()
        ctl.audio.isInterruptionRequested.return_value = False
        ctl.ui.auto_send.setChecked(True)
        ctl.handle_question("Чим вони відрізняються?", request_id="voice-followup")
        ctl._finalize_voice_question(force=True)
        wait_for(app, ctl.api)
        assert "деревоподібна рекурсія" in calls[-1][1]
        assert len(ctl.conversation.turns) == 2
        ctl.on_clear_all()
        assert not ctl.conversation.turns
    finally:
        ctl.audio = None
        app.aboutToQuit.disconnect(ctl.shutdown)
        app.removeEventFilter(ctl.capture_privacy)
        ctl.ui.deleteLater()


def test_subscription_language_badge_theme_and_header(tmp_path):
    app, ctl = controller(tmp_path)
    ui = ctl.ui
    try:
        ui.config_mgr.save_config({"window_mode": "floating", "theme": "frosted_light"})
        ui.apply_aurora_theme()
        ui.show()
        ui.window_mode.toggle() if ui.window_mode.docked else None
        ui.resize(1040, 660)
        ui.tabs.setCurrentWidget(ui.tab_subscriptions)
        app.processEvents()
        page = ui.tab_subscriptions
        card = page.cards[1]
        assert card.badge.geometry().bottom() < card.card.geometry().top()
        assert card.badge.text() == "Найвигідніше"
        assert not ui.lbl_brand_icon.pixmap().isNull()
        assert ui.btn_pin.text() == "" and not ui.btn_pin.icon().isNull()
        assert ui.btn_toggle_listen.height() == ui.btn_select_screen.height()
        assert "border-radius: 17px" in ui.btn_save_cfg.styleSheet()
        for theme in THEMES:
            ui.config_mgr.set("theme", theme)
            ui.apply_aurora_theme()
            app.processEvents()
            assert card.badge.geometry().bottom() < card.card.geometry().top()
            assert card.hover_colors[0] == THEMES[theme][2]
        for lang, title, badge, price, button in (
            ("ENG", "Subscriptions", "Best value", "UAH 399", "Unavailable"),
            ("UA", "Підписки", "Найвигідніше", "399 грн", "Недоступно"),
        ):
            ui.change_language(lang)
            app.processEvents()
            assert page.heading.text() == title
            assert card.badge.text() == badge
            assert card.price_label.text() == price
            assert card.order_button.text() == button
            assert not card.order_button.isEnabled()
        ui.btn_ghost.setFocus(Qt.FocusReason.TabFocusReason)
        assert ui.btn_ghost.hasFocus()
        ui.config_mgr.set("animation_intensity", "off")
        ui.apply_performance_mode("premium")
        card.set_hover(1.0)
        assert card.hover_animation.state() == card.hover_animation.State.Stopped
    finally:
        app.aboutToQuit.disconnect(ctl.shutdown)
        app.removeEventFilter(ctl.capture_privacy)
        ui.deleteLater()
