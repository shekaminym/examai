"""Whisper wrapper checks without loading model weights."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from examai.audio.stt_engine import STTEngine


class STTEngineTests(unittest.TestCase):
    @patch("examai.audio.stt_engine.WhisperModel")
    def test_final_decode_resamples_and_uses_quality_settings(self, model):
        model.return_value.transcribe.return_value = (
            iter([SimpleNamespace(text=" питання ")]),
            SimpleNamespace(language="uk"),
        )
        engine = STTEngine()
        audio = np.linspace(-0.1, 0.1, 44100, dtype=np.float32)
        self.assertEqual(engine.transcribe_chunk(audio, "auto", 44100), ("питання", "uk"))
        values, options = model.return_value.transcribe.call_args
        self.assertEqual(values[0].dtype, np.float32)
        self.assertEqual(len(values[0]), 16000)
        self.assertEqual(options["language"], None)
        self.assertEqual(options["beam_size"], 3)
        self.assertEqual(options["best_of"], 3)
        self.assertFalse(options["vad_filter"])
        self.assertTrue(options["without_timestamps"])

    @patch("examai.audio.stt_engine.WhisperModel")
    def test_preview_uses_single_beam(self, model):
        model.return_value.transcribe.return_value = (
            iter([SimpleNamespace(text=" чернетка ")]),
            SimpleNamespace(language="uk"),
        )
        engine = STTEngine()
        text, _ = engine.transcribe_chunk(np.ones(8000, dtype=np.float32) * 0.05, preview=True)
        self.assertEqual(text, "чернетка")
        self.assertEqual(model.return_value.transcribe.call_args.kwargs["beam_size"], 1)
        self.assertEqual(model.return_value.transcribe.call_args.kwargs["best_of"], 1)

    @patch("examai.audio.stt_engine.WhisperModel")
    def test_near_silence_never_loads_the_model(self, model):
        engine = STTEngine()
        self.assertEqual(engine.transcribe_chunk(np.zeros(8000, dtype=np.float32)), ("", ""))
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
