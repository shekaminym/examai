import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from config import ConfigManager
from database import DatabaseManager
from rag_engine import RAGEngine, ReferenceLookupError
from response_view import ResponseView
from translator import TRANSLATIONS
from ui_main import MainWindow


class NoSemantic:
    def search(self, *_args):
        raise AssertionError("A named source should bypass semantic retrieval")


def test_requirements_fast_lookup_disambiguates_and_updates(tmp_path):
    db = DatabaseManager(tmp_path / "notes.sqlite")
    engine = RAGEngine(db, semantic=NoSemantic())
    for project in ("desktop", "server"):
        (tmp_path / project).mkdir()
    first = tmp_path / "desktop" / "requirements.txt"
    first.write_text("# header\nhttpx[http2]>=0.27; python_version >= '3.10' # keep\n\n-c pins.txt\n-r child.txt\n",
                     encoding="utf-8")
    second = tmp_path / "server" / "requirements.txt"
    second.write_text("fastapi==0.100.0\n", encoding="utf-8")
    engine.index_file_sync(first)
    query = "Які бібліотеки потрібно встановити з мого файлу REQUIREMENTS.TXT?"
    context = engine.retrieve_relevant_context(query, limit=2)
    assert "httpx[http2]>=0.27; python_version" in context and "# keep" not in context
    assert "Вкладений список" in context and "Обмеження" in context
    engine.index_file_sync(second)
    with pytest.raises(ReferenceLookupError):
        engine.retrieve_relevant_context(query, limit=2)
    assert "fastapi" in engine.retrieve_relevant_context(query, source_hint="server/requirements.txt")
    first.write_text("pytest==8.4.0\n", encoding="utf-8")
    engine.index_file_sync(first)
    assert "pytest==8.4.0" in engine.retrieve_relevant_context(query, source_hint="requirements.txt")
    with pytest.raises(ReferenceLookupError):
        engine.retrieve_relevant_context("Read absent.txt")


def test_formulas_and_original_source():
    app = QApplication.instance() or QApplication([])
    view = ResponseView()
    source = r"$A \lor B$ $A \land B$ $A \rightarrow B$ $\neg A$ $\forall x$ $\exists x$ $\frac{a}{b}$ $\sum_{i=1}^{n} i$ $\alpha + \beta$ $$ E = mc^2 $$"
    view.show_response(source)
    assert view.document().toHtml().count('src="formula:') == 10
    assert view.copyable_text() == source
    view.show_streaming_response(r"Ціна $50; формула $A \lor")
    assert "$50" in view.toPlainText() and "\\lor" not in view.toPlainText()
    view.show_response("Cost $50 and code `'$value'`")
    assert "$50" in view.toPlainText() and "$value" in view.toPlainText()


def test_settings_language_save_and_failure(tmp_path):
    app = QApplication.instance() or QApplication([])
    cfg = ConfigManager(tmp_path / "config.json")
    with patch("ui_main.AudioCapturer.get_available_devices", return_value=[]):
        ui = MainWindow(cfg, DatabaseManager(tmp_path / "notes.sqlite"))
    ui.show()
    ui.open_settings()
    app.processEvents()
    assert ui.settings_window.isVisible()
    ui.change_language("ENG")
    assert ui.settings_navigation.item(0).text() == "General"
    assert ui.live_cards.question.findChild(type(ui.live_cards.question_status), "HeaderLabel").text() == "Question"
    assert set(TRANSLATIONS["UA"]) == set(TRANSLATIONS["ENG"])
    with patch.object(cfg, "save_config", side_effect=OSError):
        ui.save_settings()
    assert ui.settings_window.isVisible() and ui.settings_feedback.text()
    ui.save_settings()
    assert not ui.settings_window.isVisible()
    ui.deleteLater()
