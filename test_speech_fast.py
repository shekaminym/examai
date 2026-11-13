import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import numpy as np
from PySide6.QtCore import Qt

from config import ConfigManager
from audio_vad import SpeechSegmenter
from speech_correction import SpeechCorrector
from main import AudioWorker
from audio_vad import Utterance
from main import CLOUD_FINAL_TIMEOUT_SECONDS, DecodeMailbox


class FastSpeechTests(unittest.TestCase):
    def test_worker_prepares_whisper_and_vad_before_recording(self):
        events = []
        stt = MagicMock()
        stt.prepare.side_effect = lambda: events.append("whisper")
        worker = AudioWorker(stt, "uk", None, False, neural_vad=True)
        worker.capturer.start = MagicMock(side_effect=lambda *args, **kwargs: events.append("open_inactive"))
        worker.capturer.activate = MagicMock(side_effect=lambda: events.append("activate"))
        worker.capturer.stop = MagicMock(side_effect=lambda: events.append("close"))
        worker.process_audio = MagicMock(side_effect=lambda **kwargs: events.append("capture"))
        with patch("main.NeuralVAD", side_effect=lambda rate: events.append("vad") or object()):
            worker.run()
        self.assertEqual(events, ["whisper", "open_inactive", "vad", "activate", "capture", "close"])

    def test_cloud_timeout_keeps_phrase_for_local_decode_and_late_result_is_once(self):
        worker = AudioWorker(MagicMock(), "uk", None, False)
        worker._mailbox = DecodeMailbox()
        utterance = Utterance(np.ones(16000, dtype=np.float32), 16000, 1, True,
                              queued_at=time.perf_counter() - CLOUD_FINAL_TIMEOUT_SECONDS - .1)
        worker._pending_cloud_finals[1] = utterance
        finals = []
        worker.question_finished.connect(finals.append, Qt.ConnectionType.DirectConnection)
        worker._fallback_slow_cloud()
        self.assertIs(worker._mailbox.get(), utterance)
        worker._cloud_final(1, "радіус вектор")
        worker._publish_final(1, "запізнілий локальний текст")
        self.assertEqual(finals, ["радіус-вектор"])

    def test_empty_cloud_result_uses_saved_audio_locally(self):
        worker = AudioWorker(MagicMock(), "uk", None, False)
        worker._mailbox = DecodeMailbox()
        utterance = Utterance(np.ones(16000, dtype=np.float32), 16000, 1, True)
        worker._pending_cloud_finals[1] = utterance
        worker._cloud_final(1, "")
        self.assertIs(worker._mailbox.get(), utterance)

    def test_audio_worker_cloud_streams_speech_only_and_delivers_corrected_final(self):
        stt = MagicMock()
        worker = AudioWorker(stt, "uk", None, False)
        worker.capturer.sample_rate = 16000
        delivered = []
        worker.question_finished.connect(delivered.append, Qt.ConnectionType.DirectConnection)

        class FakeCloud:
            failed = False
            ready = True

            def __init__(self):
                self.pushed = 0
                self.voiced_samples = 0
                self.commits = []

            def push(self, audio, rate, *, utterance_id=None):
                self.pushed += len(audio)
                self.voiced_samples += np.count_nonzero(audio)

            def commit(self, utterance_id):
                self.commits.append(utterance_id)
                worker._cloud_final(utterance_id, "радіус вектор")

            def close(self):
                pass

        cloud = FakeCloud()
        worker.cloud = cloud
        # A rejected click must never become part of the next cloud turn.
        levels = [.1] * 2 + [0] * 30 + [.1] * 20 + [0] * 30
        blocks = iter((0, np.full(320, level, dtype=np.float32), level) for level in levels)

        def read_audio(timeout):
            block = next(blocks, None)
            if block is None:
                worker.isInterruptionRequested.return_value = True
            return block

        worker.capturer.get_audio_data = read_audio
        worker.isInterruptionRequested = MagicMock(return_value=False)
        worker.process_audio()
        self.assertEqual(delivered, ["радіус-вектор"])
        self.assertEqual(cloud.commits, [2])
        self.assertEqual(cloud.voiced_samples, 20 * 320)
        self.assertGreater(cloud.pushed, 20 * 320)
        stt.transcribe_chunk.assert_not_called()

    def test_disconnect_uses_local_once_and_reconnects_for_next_turn(self):
        stt = MagicMock()
        stt.transcribe_chunk.return_value = ("local answer", "uk")
        worker = AudioWorker(stt, "uk", None, False, gemini_key="synthetic")
        worker.capturer.sample_rate = 16000
        delivered = []
        local_ready = threading.Event()

        def final(text):
            delivered.append(text)
            local_ready.set()

        worker.question_finished.connect(final, Qt.ConnectionType.DirectConnection)

        class FakeCloud:
            def __init__(self, *args, **kwargs):
                self.failed = False
                self.ready = True
                self.reconnectable = True

            def close(self, *, wait=True):
                pass

            def start(self, *, wait=True):
                self.background = not wait

            def push(self, audio, rate, *, utterance_id=None):
                pass

            def commit(self, utterance_id):
                self.committed = utterance_id
                worker._cloud_final(0, "late old cloud text")
                worker._cloud_final(utterance_id, "new cloud text")

        previous = FakeCloud()
        previous.failed, previous.ready = True, False
        worker.cloud = previous
        # Production utterance IDs start at 1. Use 0 only as a distinct stale
        # session ID here and put the delivered watermark before it.
        worker._latest_delivered_id = -1
        worker._pending_cloud_finals[0] = Utterance(np.ones(16000, np.float32), 16000, 0, True)
        blocks = iter([.1] * 20 + [0] * 25)
        replacements = []

        def read_audio(timeout):
            if not replacements:
                self.assertTrue(local_ready.wait(2))
                replacements.append(worker.cloud)
            level = next(blocks, None)
            if level is None:
                worker.isInterruptionRequested.return_value = True
                return None
            return 0, np.full(320, level, dtype=np.float32), level

        worker.capturer.get_audio_data = read_audio
        worker.isInterruptionRequested = MagicMock(return_value=False)
        worker.process_audio()
        self.assertIsNot(replacements[0], previous)
        self.assertTrue(replacements[0].background)
        self.assertEqual(replacements[0].committed, 1)
        self.assertEqual(delivered, ["local answer", "new cloud text"])
        stt.transcribe_chunk.assert_called_once()

    def test_segmenter_exposes_preroll_only_when_speech_begins(self):
        class FakeVAD:
            def reset(self):
                pass

            def is_speech(self, audio, rms=None):
                return float(audio[0]) > 0

        segmenter = SpeechSegmenter(vad=FakeVAD(), previews=False)
        segmenter.push(np.zeros(320, dtype=np.float32), 16000)
        self.assertIsNone(segmenter.started_audio)
        segmenter.push(np.ones(320, dtype=np.float32), 16000)
        self.assertEqual(len(segmenter.started_audio), 640)
        segmenter.push(np.ones(320, dtype=np.float32), 16000)
        self.assertIsNone(segmenter.started_audio)

    def test_glossary_only_corrects_complete_known_phrases(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "speech_glossary.json"
            path.write_text(json.dumps({"радіус вектор": "радіус-вектор", "хештаблиця": "хеш-таблиця"}), encoding="utf-8")
            corrector = SpeechCorrector(path)
            self.assertEqual(corrector.correct("Радіус вектор і хештаблиця"), "Радіус-вектор і хеш-таблиця")
            self.assertEqual(corrector.correct("радіус векторизація"), "радіус векторизація")

    def test_existing_config_gets_auto_language_without_persisting_keys(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"whisper_language": "uk", "gemini_api_key": "session-secret"}), encoding="utf-8")
            config = ConfigManager(path)
            self.assertFalse(config.get("speech_auto_language"))
            self.assertEqual(config.get("gemini_api_key"), "session-secret")
            config.set("speech_end_silence_ms", 420)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["gemini_api_key"], "session-secret")

if __name__ == "__main__":
    unittest.main()
