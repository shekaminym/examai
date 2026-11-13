"""Regression coverage for line references, question assembly, and screen intent."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from database import DatabaseManager
from image_intent import EXPLAIN_CODE, image_intent, select_screen_instruction
from llm_engine import LLMEngine
from question_aggregator import QuestionAggregator
from rag_engine import RAGEngine, ReferenceLookupError
from screen_selection import encode_image


class CodeReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = DatabaseManager(self.root / "notes.db")
        self.semantic = MagicMock()
        self.semantic.search.return_value = ([], "disabled")
        self.rag = RAGEngine(self.db, semantic=self.semantic)

    def _import(self, name="lab.txt", *, marker="scanf", encoding="utf-8"):
        lines = [f"// коментар {number}" for number in range(1, 27)]
        lines[1] = ""
        lines[16] = "    // перевірка вводу"
        lines[17] = f"    if ({marker}(\"%lf\", &a[i]) != 1 || !isfinite(a[i])) {{"
        source = self.root / name
        source.write_text("\n".join(lines), encoding=encoding)
        self.rag.index_file_sync(source)
        return lines

    def test_utf8_txt_preserves_blank_comment_indentation_and_line_18(self):
        self._import()
        context = self.rag.retrieve_relevant_context("Що знаходиться в 18-му рядку мого коду?")
        self.assertIn("lab.txt", context)
        self.assertIn('18:     if (scanf("%lf", &a[i])', context)
        self.assertIn("17:     // перевірка вводу", context)
        self.assertEqual(self.db.get_line_window("lab.txt", 2, radius=0), [(2, "")])
        self.semantic.search.assert_not_called()

    def test_windows_1251_txt_and_out_of_range(self):
        self._import("source.txt", encoding="cp1251")
        self.assertIn("18:     if", self.rag.retrieve_relevant_context("Поясни 18-й рядок"))
        with self.assertRaisesRegex(ReferenceLookupError, "немає рядка 99"):
            self.rag.retrieve_relevant_context("Що в рядку 99?")

    def test_multiple_files_require_name_or_selection(self):
        self._import("alpha.txt", marker="scanf")
        self._import("beta.txt", marker="fscanf")
        with self.assertRaisesRegex(ReferenceLookupError, "назву TXT-файлу"):
            self.rag.retrieve_relevant_context("Поясни рядок 18")
        selected = self.rag.retrieve_relevant_context("Поясни рядок 18", source_hint="beta.txt")
        named = self.rag.retrieve_relevant_context("Поясни рядок 18 у alpha.txt", source_hint="beta.txt")
        self.assertIn("fscanf", selected)
        self.assertIn("scanf", named)
        self.assertNotIn("fscanf", named)

    def test_function_memory_and_loop_search_keep_line_numbers(self):
        source = self.root / "program.txt"
        source.write_text("#include <stdlib.h>\nvoid createMatrix(void) {\n"
                          "  double *a = malloc(16);\n  for (int i = 0; i < 4; i++) {}\n}\n",
                          encoding="utf-8")
        self.rag.index_file_sync(source)
        for question, expected in (
            ("Для чого потрібна функція createMatrix?", "createMatrix"),
            ("Де в моєму коді виділяється пам'ять?", "malloc"),
            ("Який цикл використовується для обробки масиву?", "for"),
        ):
            with self.subTest(question=question):
                context = self.rag.retrieve_relevant_context(question)
                self.assertIn("Файл «program.txt», рядок", context)
                self.assertIn(expected, context)

    def test_semantic_retrieval_remains_for_paraphrased_notes(self):
        self.semantic.search.return_value = ([(8, "biology.txt", "Фотосинтез утворює цукор")], "ready")
        with patch.object(self.rag, "_lexical_rows", return_value=[]):
            context = self.rag.retrieve_relevant_context("Як рослина живиться світлом?")
        self.assertIn("Фотосинтез", context)
        self.semantic.search.assert_called_once()


class AggregatorTests(unittest.TestCase):
    def test_pauses_filler_and_continuation_form_one_question(self):
        agg = QuestionAggregator()
        self.assertTrue(agg.add_final("Що таке рекурсія", 10.0, "voice-1", now=10.0))
        agg.speech_started(now=10.6)
        self.assertFalse(agg.ready(now=11.1))
        self.assertFalse(agg.add_final("еее", 11.2, "voice-2", now=11.2))
        self.assertTrue(agg.add_final("еее і рекурсивний спуск", 12.0, "voice-3", now=12.0))
        agg.speech_started(now=12.5)
        self.assertTrue(agg.add_final("і підйом?", 13.0, "voice-4", now=13.0))
        self.assertIsNone(agg.finalize(now=13.3))
        question = agg.finalize(now=13.7)
        self.assertEqual(question.text, "Що таке рекурсія і рекурсивний спуск і підйом?")
        self.assertEqual(question.request_id, "voice-1")
        self.assertEqual(question.fragments, 3)

    def test_filler_alone_does_not_submit_and_short_question_does(self):
        agg = QuestionAggregator()
        for filler in ("еее", "ммм", "ааа", "ну", "так", "це"):
            self.assertFalse(agg.add_final(filler, now=1.0))
        self.assertFalse(agg.pending)
        self.assertTrue(agg.add_final("Чому?", 2.0, "short", now=2.0))
        self.assertFalse(agg.ready(now=2.5))
        self.assertEqual(agg.finalize(now=2.7).text, "Чому?")

    def test_open_conjunction_extends_wait_but_wait_is_bounded(self):
        agg = QuestionAggregator(max_wait=2.0)
        agg.add_final("Що таке рекурсія і", 1.0, "open", now=1.0)
        self.assertFalse(agg.ready(now=2.0))
        agg.speech_started(now=2.1)
        self.assertFalse(agg.ready(now=3.0))
        agg.add_final("рекурсивний спуск?", 3.1, "next", now=3.1)
        self.assertTrue(agg.ready(now=3.1))
        self.assertEqual(agg.finalize(now=3.1).text, "Що таке рекурсія і рекурсивний спуск?")


class ImageAndHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_narrow_code_image_is_enlarged_without_changing_ratio(self):
        image = QImage(467, 26, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        data, mime = encode_image(image)
        decoded = QImage.fromData(data)
        self.assertEqual(mime, "image/png")
        self.assertEqual((decoded.width(), decoded.height()), (1868, 104))

    def test_code_instruction_survives_and_changes_vision_prompt(self):
        user_text = "Коли я виділю рядок коду, поясни кожен оператор"
        instruction = select_screen_instruction(user_text)
        self.assertEqual(instruction, user_text)
        self.assertEqual(image_intent(instruction), EXPLAIN_CODE)
        engine = LLMEngine("synthetic")
        engine._generate = MagicMock(return_value="пояснення")
        answer = engine.generate_image_answer(b"image", "image/png", user_instruction=instruction,
                                              context="Файл «lab.txt»: 18: if (x)", intent=EXPLAIN_CODE)
        self.assertEqual(answer, "пояснення")
        system, parts = engine._generate.call_args.args[:2]
        self.assertIn("EXPLAIN_CODE", system)
        self.assertIn("оператора", system)
        self.assertIn(user_text, parts[0])
        self.assertIn("lab.txt", parts[0])
        self.assertEqual(len(parts), 2)

    def _controller(self):
        from main import MainController
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config = __import__("config").ConfigManager(Path(temporary.name) / "config.json")
        db = DatabaseManager(Path(temporary.name) / "db.sqlite")
        with (patch("main.ConfigManager", return_value=config),
              patch("main.DatabaseManager", return_value=db),
              patch("main.keyboard.add_hotkey", return_value=None),
              patch("ui_main.AudioCapturer.get_available_devices", return_value=[])):
            controller = MainController(self.app)
        self.addCleanup(self.app.aboutToQuit.disconnect, controller.shutdown)
        def dispose():
            controller._question_timer.stop()
            controller._answer_hint_timer.stop()
            controller.ui.hide()
            controller.capture_privacy.unregister(controller.ui)
            self.app.removeEventFilter(controller.capture_privacy)
            controller.ui.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.addCleanup(dispose)
        return controller

    def test_screen_selection_passes_previous_user_instruction(self):
        controller = self._controller()
        controller.config.set("gemini_api_key", "synthetic")
        controller.ui.txt_question.setPlainText("Коли я виділю рядок коду, поясни його")
        with patch("main.QTimer.singleShot"):
            controller.start_screen_selection()
        self.assertEqual(controller._pending_screen_instruction,
                         "Коли я виділю рядок коду, поясни його")
        controller.submit_question = MagicMock()
        image = QImage(467, 26, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        controller._on_screen_selected(image)
        self.assertEqual(controller.submit_question.call_args.kwargs["image_instruction"],
                         "Коли я виділю рядок коду, поясни його")
        self.assertIn("рядок коду", controller.ui.txt_question.toPlainText())

    def test_controller_waits_for_continuation_then_submits_once(self):
        controller = self._controller()
        controller.audio = MagicMock()
        controller.audio.isInterruptionRequested.return_value = False
        controller.submit_question = MagicMock()
        controller.handle_question("Що таке рекурсія", 1.0, "voice-1")
        controller.handle_question("еее")
        self.assertEqual(controller.ui.txt_question.toPlainText(), "Що таке рекурсія")
        controller.submit_question.assert_not_called()
        controller.handle_question("і рекурсивний спуск?", 2.0, "voice-2")
        QTest.qWait(760)
        controller.submit_question.assert_called_once()
        self.assertEqual(controller.submit_question.call_args.args[0],
                         "Що таке рекурсія і рекурсивний спуск?")
        self.assertEqual(controller.submit_question.call_args.kwargs["request_id"], "voice-1")

    def test_unreliable_auto_question_keeps_previous_answer_and_allows_manual_send(self):
        controller = self._controller()
        controller.ui.auto_send.setChecked(True)
        controller.ui.txt_answer.setPlainText("Попередня відповідь")
        controller.submit_question = MagicMock()
        controller._submit_auto_question("Джі, якщо було княста ф", 1.0, "bad-stt")
        controller.submit_question.assert_not_called()
        self.assertEqual(controller.ui.txt_answer.toPlainText(), "Попередня відповідь")
        controller.submit_manual_question("Що таке рекурсія?")
        controller.submit_question.assert_called_once()
        self.assertEqual(controller.submit_question.call_args.args,
                         ("Що таке рекурсія?",))
        self.assertTrue(controller.submit_question.call_args.kwargs["manual"])

    def test_continuation_reopens_recent_voice_question_with_its_first_words(self):
        controller = self._controller()
        controller.audio = MagicMock()
        controller.audio.isInterruptionRequested.return_value = False
        controller.submit_question = MagicMock()
        controller._submit_auto_question("Що таке рекурсія?", 1.0, "part-1")
        controller.handle_question("і рекурсивний спуск?", 2.0, "part-2")
        self.assertEqual(controller._question_aggregator.text,
                         "Що таке рекурсія і рекурсивний спуск?")
        controller._finalize_voice_question(force=True)
        self.assertEqual(controller.submit_question.call_count, 2)
        self.assertEqual(controller.submit_question.call_args.args[0],
                         "Що таке рекурсія і рекурсивний спуск?")

    def test_completed_answers_remain_navigable_after_clear(self):
        controller = self._controller()
        controller._answer_request_id = "one"
        controller._current_question = "Перше?"
        controller._remember_answer("Відповідь 1")
        controller._answer_request_id = "two"
        controller._current_question = "Друге?"
        controller._remember_answer("Відповідь 2")
        controller.on_history_step(-1)
        self.assertEqual(controller.ui.txt_answer.toPlainText(), "Відповідь 1")
        controller.on_history_step(1)
        self.assertEqual(controller.ui.txt_question.toPlainText(), "Друге?")
        controller.ui.clear_both()
        self.assertEqual(controller.ui.txt_answer.toPlainText(), "")
        self.assertTrue(controller.ui.btn_history_prev.isEnabled())
        controller.on_history_step(-1)
        self.assertEqual(controller.ui.txt_answer.toPlainText(), "Відповідь 2")

    def test_sidebar_history_opens_selected_answer(self):
        controller = self._controller()
        controller._answer_request_id = "one"
        controller._current_question = "Перше питання?"
        controller._remember_answer("Перша відповідь")
        controller._answer_request_id = "two"
        controller._current_question = "Друге питання?"
        controller._remember_answer("Друга відповідь")
        controller.ui.tabs.setCurrentWidget(controller.ui.tab_history)
        self.assertEqual(controller.ui.history_list.count(), 2)
        controller.on_history_select(0)
        self.assertIs(controller.ui.tabs.currentWidget(), controller.ui.tab_live)
        self.assertEqual(controller.ui.txt_question.toPlainText(), "Перше питання?")
        self.assertEqual(controller.ui.txt_answer.toPlainText(), "Перша відповідь")

    def test_polished_controls_and_settings_fit_window(self):
        controller = self._controller()
        ui = controller.ui
        ui.config_mgr.set("reduce_motion", True)
        ui.window_mode.expand_toggle()
        ui.show()
        ui.tabs.setCurrentWidget(ui.tab_settings)
        self.app.processEvents()
        self.assertEqual(ui.settings_scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(ui.lbl_status.width(), ui.sidebar.width())
        self.assertEqual(ui.answer_mode.currentText(), "FAST")
        for name, page in (("about", ui.tab_about), ("subscriptions", ui.tab_subscriptions)):
            ui.nav_buttons[name].click()
            self.assertIs(ui.tabs.currentWidget(), page)
            if name == "about":
                labels = [label.text() for label in page.findChildren(QLabel)]
                self.assertTrue(any("Oleksandr Avramenko" in text for text in labels))
            else:
                self.assertEqual(page.findChildren(QLabel), [])

    def test_recording_animation_respects_performance_mode(self):
        controller = self._controller()
        ui = controller.ui
        self.assertFalse(ui.audio_visualizer.timer.isActive())
        ui.btn_toggle_listen.set_recording(True)
        self.assertTrue(ui.btn_toggle_listen.pulse_timer.isActive())
        ui.audio_visualizer.set_active(True)
        self.assertTrue(ui.audio_visualizer.timer.isActive())
        ui.apply_performance_mode("performance")
        self.assertFalse(ui.btn_toggle_listen.pulse_timer.isActive())
        self.assertFalse(ui.audio_visualizer.timer.isActive())
        ui.apply_performance_mode("visual")
        self.assertTrue(ui.btn_toggle_listen.pulse_timer.isActive())
        self.assertTrue(ui.audio_visualizer.timer.isActive())
        ui.btn_toggle_listen.set_recording(False)
        ui.audio_visualizer.set_active(False)
        self.assertFalse(ui.audio_visualizer.timer.isActive())

    def test_optional_timing_window_only_updates_when_open(self):
        controller = self._controller()
        controller.open_timing_window()
        timing_window = controller.timing_window
        try:
            self.assertIn(timing_window, controller.capture_privacy._required)
            controller._timing_metric("first_text", 123.4)
            self.assertEqual(timing_window.values["first_text"].text(), "123 мс")
            timing_window.close()
            controller._timing_metric("first_text", 456.7)
            self.assertEqual(timing_window.values["first_text"].text(), "123 мс")
        finally:
            controller.capture_privacy.unregister(timing_window)
            timing_window.close()

    def test_exact_line_reaches_llm_and_missing_line_does_not(self):
        controller = self._controller()
        source = Path(controller.db.db_path).with_name("code.txt")
        source.write_text("#include <stdio.h>\n" + "int x = 0;\n" * 16 +
                          "    return x;\n", encoding="utf-8")
        controller.rag.index_file_sync(source)
        controller._last_imported_source = source.name
        engine = MagicMock(used_model="synthetic")
        engine.generate_answer.return_value = "Рядок повертає x"
        controller.get_llm = MagicMock(return_value=engine)
        controller.start_api = MagicMock()
        controller.submit_question("Поясни 18-й рядок", manual=True)
        worker, _ = controller.start_api.call_args.args
        self.assertIn("Рядок повертає", worker.task()[0])
        self.assertIn("18:     return x;", engine.generate_answer.call_args.args[1])

        controller.submit_question("Поясни рядок 99", manual=True)
        worker, _ = controller.start_api.call_args.args
        engine.generate_answer.reset_mock()
        answer, _ = worker.task()
        self.assertIn("немає рядка 99", answer)
        engine.generate_answer.assert_not_called()

    def test_selected_code_file_reaches_vision_request(self):
        controller = self._controller()
        source = Path(controller.db.db_path).with_name("code.txt")
        source.write_text("#include <stdio.h>\nint main(void) { return 0; }\n", encoding="utf-8")
        controller.rag.index_file_sync(source)
        controller._last_imported_source = source.name
        engine = MagicMock(used_model="synthetic")
        engine.generate_image_answer.return_value = "Пояснення коду"
        controller.get_llm = MagicMock(return_value=engine)
        controller.start_api = MagicMock()
        controller.submit_question("Поясни код на зображенні", manual=True,
                                   image_bytes=b"synthetic", image_mime="image/png",
                                   image_instruction="Поясни рядок коду")
        worker, _ = controller.start_api.call_args.args
        self.assertIn("Пояснення", worker.task()[0])
        self.assertIn("code.txt", engine.generate_image_answer.call_args.kwargs["context"])
        self.assertEqual(engine.generate_image_answer.call_args.kwargs["intent"], EXPLAIN_CODE)


if __name__ == "__main__":
    unittest.main()
