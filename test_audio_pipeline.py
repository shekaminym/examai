"""Offline checks for VAD segmentation and decode prioritisation."""
from __future__ import annotations

import unittest

import numpy as np

from audio_vad import SpeechSegmenter


class SegmenterTests(unittest.TestCase):
    RATE = 16000

    @staticmethod
    def block(level: float, milliseconds: int = 100) -> np.ndarray:
        return np.full(int(SegmenterTests.RATE * milliseconds / 1000), level, dtype=np.float32)

    def test_short_noise_is_not_an_utterance(self):
        segmenter = SpeechSegmenter()
        output = []
        output += segmenter.push(self.block(0.12), self.RATE, 0.12, 0.0)
        for index in range(7):
            output += segmenter.push(self.block(0.0), self.RATE, 0.0, 0.1 + index / 10)
        self.assertEqual(output, [])
        self.assertFalse(segmenter.active)

    def test_stationary_background_learns_before_speech_starts(self):
        segmenter = SpeechSegmenter()
        for index in range(20):
            output = segmenter.push(self.block(0.01), self.RATE, 0.01, index / 10)
            self.assertEqual(output, [])
            self.assertFalse(segmenter.active)
        # Normal speech should still exceed the learned background threshold.
        segmenter.push(self.block(0.08), self.RATE, 0.08, 2.1)
        self.assertTrue(segmenter.active)

    def test_preroll_and_speech_are_kept_until_a_short_pause(self):
        segmenter = SpeechSegmenter()
        output = []
        output += segmenter.push(self.block(0.0), self.RATE, 0.0, 0.0)
        for index in range(5):
            output += segmenter.push(self.block(0.08), self.RATE, 0.08, 0.1 + index / 10)
        for index in range(6):
            output += segmenter.push(self.block(0.0), self.RATE, 0.0, 0.6 + index / 10)
        finals = [item for item in output if item.is_final]
        self.assertEqual(len(finals), 1)
        self.assertGreaterEqual(len(finals[0].audio), int(self.RATE * 0.64))
        self.assertLess(len(finals[0].audio), int(self.RATE * 0.9))

    def test_preview_starts_early_and_final_is_emitted_once(self):
        segmenter = SpeechSegmenter()
        output = []
        for index in range(15):
            output += segmenter.push(self.block(0.09), self.RATE, 0.09, index / 10)
        previews = [item for item in output if not item.is_final]
        self.assertGreaterEqual(len(previews), 1)
        for index in range(6):
            output += segmenter.push(self.block(0.0), self.RATE, 0.0, 1.5 + index / 10)
        finals = [item for item in output if item.is_final]
        self.assertEqual(len(finals), 1)
        self.assertEqual(previews[0].utterance_id, finals[0].utterance_id)

    def test_disabled_previews_keep_the_final_audio(self):
        def collect(enabled):
            segmenter = SpeechSegmenter(previews=enabled)
            output = []
            for level in [0.08] * 35 + [0.0] * 6:
                output += segmenter.push(self.block(level), self.RATE, level)
            return output

        regular = collect(True)
        final, = collect(False)
        self.assertGreater(len(regular), 1)
        self.assertTrue(final.is_final)
        np.testing.assert_array_equal(final.audio, regular[-1].audio)

    def test_gap_flushes_a_phrase_without_waiting_for_new_audio(self):
        segmenter = SpeechSegmenter()
        for index in range(4):
            segmenter.push(self.block(0.09), self.RATE, 0.09, index / 10)
        output = segmenter.flush_if_idle(1.0)
        self.assertEqual(len(output), 1)
        self.assertTrue(output[0].is_final)

    def test_rate_change_does_not_stitch_two_device_formats(self):
        segmenter = SpeechSegmenter()
        segmenter.push(self.block(0.09), self.RATE, 0.09, 0.0)
        output = segmenter.push(np.full(800, 0.09, dtype=np.float32), 8000, 0.09, 0.1)
        self.assertEqual(output, [])
        self.assertEqual(segmenter._rate, 8000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
