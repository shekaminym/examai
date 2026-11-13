"""Deterministic replays of VAD/STT timing; no microphone or API key needed."""

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from config import ConfigManager
from llm_engine import LLMEngine
from question_aggregator import (QuestionAggregator, is_continuation_fragment,
                                 is_unreliable_transcript, join_fragments)


def replay(fragments, mode="slow"):
    aggregator = QuestionAggregator(mode=mode)
    submitted = []
    for start, ended, text in fragments:
        if aggregator.ready(now=start):
            submitted.append(aggregator.finalize(now=start).text)
        aggregator.speech_started(now=start)
        aggregator.add_final(text, speech_ended_at=ended, now=ended)
    if aggregator.pending:
        submitted.append(aggregator.finalize(now=fragments[-1][1] + 6).text)
    return submitted


class SlowSpeechReplayTests(unittest.TestCase):
    def test_shorter_complete_wait_reduces_early_sends(self):
        pauses = (.4, .6, .8, .9, 1.0, 1.2, 1.4, 1.8, 2.4, 3.6)
        cases = [((0, .5, "Що таке рекурсія"),
                  (.5 + pause, .9 + pause, "і рекурсивний спуск?"))
                 for pause in pauses]
        self.assertEqual(sum(len(replay(case, "normal")) > 1 for case in cases), 5)
        self.assertEqual(sum(len(replay(case, "complete")) > 1 for case in cases), 2)
        self.assertTrue(is_continuation_fragment("і рекурсивний спуск?"))
        self.assertFalse(is_continuation_fragment("Що таке ітерація?"))
        self.assertFalse(is_continuation_fragment("Чи потрібна рекурсія?"))

    def test_old_normal_setting_upgrades_to_complete_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"speech_pace_mode": "normal"}), encoding="utf-8")
            config = ConfigManager(path)
            self.assertEqual(config.get("speech_pace_mode"), "complete")
            config.save_config({"speech_pace_mode": "normal"})
            self.assertEqual(ConfigManager(path).get("speech_pace_mode"), "normal")

    def test_existing_opacity_is_upgraded_once_without_overriding_user_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"window_opacity": 0.949}), encoding="utf-8")
            config = ConfigManager(path)
            self.assertEqual(config.get("window_opacity"), 0.86)
            config.save_config({"window_opacity": 0.81})
            self.assertEqual(ConfigManager(path).get("window_opacity"), 0.81)

    def test_slow_ukrainian_question_with_five_vad_segments(self):
        fragments = [
            (0.0, 0.4, "Що таке..."),
            (1.9, 2.2, "еее"),
            (3.7, 4.0, "рекурсія"),
            (6.0, 6.3, "і рекурсивний спуск"),
            (8.3, 8.6, "та підйом?"),
        ]
        self.assertEqual(replay(fragments), ["Що таке рекурсія і рекурсивний спуск та підйом?"])

    def test_slow_russian_and_mixed_questions(self):
        russian = [(0, .4, "Что такое"), (2.1, 2.5, "рекурсия"),
                   (4.6, 5.0, "и рекурсивный спуск?")]
        mixed = [(0, .4, "Що таке recursive"), (2.1, 2.5, "method"),
                 (4.6, 5.0, "і де його застосовують?")]
        self.assertEqual(replay(russian), ["Что такое рекурсия и рекурсивный спуск?"])
        self.assertEqual(replay(mixed), ["Що таке recursive method і де його застосовують?"])

    def test_active_speech_cannot_finalize_older_fragment(self):
        aggregator = QuestionAggregator(mode="slow")
        aggregator.add_final("Що таке рекурсія", now=1.0)
        aggregator.speech_started(now=3.0)
        self.assertFalse(aggregator.ready(now=30.0))
        aggregator.add_final("і рекурсивний спуск?", now=31.0)
        self.assertEqual(aggregator.finalize(now=31.0).text,
                         "Що таке рекурсія і рекурсивний спуск?")

    def test_corrected_final_replaces_prefix_and_repeated_final_does_not_duplicate(self):
        self.assertEqual(join_fragments("Що таке рекурсія", "Що таке рекурсія і спуск?"),
                         "Що таке рекурсія і спуск?")
        self.assertEqual(join_fragments("Що таке рекурсія і спуск?", "і спуск?"),
                         "Що таке рекурсія і спуск?")

    def test_uncertain_transcription_is_not_an_auto_question(self):
        for text in ("Джі, якщо було княста ф", "еее", "Що таке", "Джі"):
            self.assertTrue(is_unreliable_transcript(text), text)
        for text in ("Що таке рекурсія?", "Що таке рекурсія і спуск?", "Рекурсія"):
            self.assertFalse(is_unreliable_transcript(text), text)

    def test_answer_prompt_cannot_invite_reconstruction(self):
        engine = LLMEngine("synthetic")
        engine._generate = MagicMock(return_value="ok")
        engine.generate_answer("Джі, якщо було княста ф", "")
        instruction = engine._generate.call_args.args[0]
        self.assertIn("Не вгадуй нерозбірливі слова", instruction)
        self.assertNotIn("Виправ очевидну помилку розшифровки", instruction)


if __name__ == "__main__":
    unittest.main()
