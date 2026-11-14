"""Ukrainian text correction and Gemini Live protocol without network calls."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
from PySide6.QtCore import Qt

from examai.audio.audio_vad import Utterance
from examai.app import AudioWorker, DecodeMailbox
from examai.audio.speech_correction import SPEECH_GLOSSARY, SpeechCorrector, has_non_ukrainian_cyrillic
from examai.audio.stt_gemini import GeminiRealtimeSTT, friendly_gemini_error


class FakeSession:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()

    async def send_realtime_input(self, **event):
        self.sent.append(event)
        if "activity_end" in event:
            await self.incoming.put(SimpleNamespace(server_content=SimpleNamespace(
                interim_input_transcription=None,
                input_transcription=SimpleNamespace(text="дизюнкція"),
            )))

    async def receive(self):
        # The SDK completes this iterator after each model turn.
        yield await self.incoming.get()


class FakeClient:
    def __init__(self):
        self.session = FakeSession()
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=self.connect))

    @asynccontextmanager
    async def connect(self, model, config):
        self.model = model
        self.config = config
        yield self.session

    def close(self):
        pass


class GeminiPipelineTests(unittest.TestCase):
    def test_screenshot_question_and_apostrophe_variants(self):
        correct = SpeechCorrector(SPEECH_GLOSSARY).correct
        self.assertEqual(correct("Що така дезномція."), "Що таке диз’юнкція.")
        self.assertEqual(correct("Дизʼюнкція і конюнкція"), "Диз’юнкція і кон’юнкція")
        self.assertEqual(correct("Тіматрізняється X і кон'юнкція?"),
                         "Чим відрізняється X і кон’юнкція?")
        self.assertEqual(correct("Дизʻюнкція, диз′юнкція"), "Диз’юнкція, диз’юнкція")
        self.assertEqual(correct("Компютер та обєднання"), "Комп’ютер та об’єднання")
        self.assertEqual(correct("дизюнція"), "диз’юнкція")
        self.assertEqual(correct("дислокація"), "дислокація")
        self.assertEqual(correct("Шо таке рекусривний метод?"), "Що таке рекурсивний метод?")

    def test_cloud_ukrainian_vocabulary_includes_recursion(self):
        corrector = SpeechCorrector(SPEECH_GLOSSARY)
        self.assertIn("рекурсивний метод", corrector.terms)
        self.assertTrue(has_non_ukrainian_cyrillic("Это ты не"))
        self.assertFalse(has_non_ukrainian_cyrillic("Що таке рекурсивний метод"))

    def test_russian_cloud_fragment_is_hidden_and_saved_audio_retried_locally(self):
        worker = AudioWorker(MagicMock(), "uk", None, False)
        worker._mailbox = DecodeMailbox()
        utterance = Utterance(np.ones(16000, np.float32), 16000, 1, True)
        worker._pending_cloud_finals[1] = utterance
        previews, questions = [], []
        worker.text_updated.connect(previews.append, Qt.ConnectionType.DirectConnection)
        worker.question_finished.connect(questions.append, Qt.ConnectionType.DirectConnection)
        worker._cloud_preview("Это ты не")
        worker._cloud_final(1, "Это ты не")
        self.assertEqual(previews, [])
        self.assertEqual(questions, [])
        self.assertIs(worker._mailbox.get(), utterance)

    def test_gemini_live_uses_ukrainian_and_handles_two_turns(self):
        client = FakeClient()
        finals = []
        arrived = threading.Event()

        def on_final(utterance_id, text):
            finals.append((utterance_id, text))
            arrived.set()

        cloud = GeminiRealtimeSTT(
            "placeholder", lambda text: None, on_final, lambda error: None,
            keywords=("диз’юнкція",), client_factory=lambda **kwargs: client,
        )
        self.assertTrue(cloud.start())
        try:
            for utterance_id in (7, 8):
                arrived.clear()
                cloud.push(np.full(4800, .25, np.float32), 48000)
                cloud.commit(utterance_id)
                self.assertTrue(arrived.wait(2))
            self.assertEqual(finals, [(7, "дизюнкція"), (8, "дизюнкція")])
            self.assertEqual(client.model, "gemini-3.5-transcribe-live")
            options = client.config.input_audio_transcription
            self.assertEqual(options.language_codes, ["uk-UA"])
            self.assertEqual(options.custom_vocabulary, ["диз’юнкція"])
            self.assertEqual(options.mode, "SMART")
            self.assertEqual(len(client.session.sent), 6)
            self.assertIn("activity_start", client.session.sent[0])
            audio = client.session.sent[1]["audio"]
            self.assertEqual(audio.mime_type, "audio/pcm;rate=16000")
            self.assertEqual(len(audio.data), 3200)
            self.assertIn("activity_end", client.session.sent[2])
        finally:
            cloud.close()

    def test_transcribe_finals_do_not_require_receive_iterator_boundary(self):
        async def exercise():
            finals = []
            cloud = GeminiRealtimeSTT("synthetic", lambda _: None,
                                      lambda *item: finals.append(item), lambda _: None)
            cloud._pending_ids.extend((11, 12))

            class Session:
                async def receive(self):
                    for text in ("first", "second"):
                        yield SimpleNamespace(server_content=SimpleNamespace(
                            input_transcription=SimpleNamespace(text=text),
                        ))
                    cloud._closed.set()

            await cloud._receive_loop(Session())
            return finals

        self.assertEqual(asyncio.run(exercise()), [(11, "first"), (12, "second")])

    def test_commit_sends_buffered_tail_before_activity_end(self):
        client = FakeClient()
        finished = threading.Event()
        cloud = GeminiRealtimeSTT("synthetic", lambda _: None, lambda *_: finished.set(),
                                  lambda _: None, client_factory=lambda **_: client)
        self.assertTrue(cloud.start())
        try:
            cloud.push(np.ones(2000, np.float32), 16000)
            cloud.commit(1)
            self.assertTrue(finished.wait(2))
            sent = client.session.sent
            self.assertEqual([len(item["audio"].data) for item in sent if "audio" in item],
                             [3200, 800])
            self.assertIn("activity_end", sent[-1])
        finally:
            cloud.close()

    def test_close_cancels_an_unfinished_live_connection(self):
        client = FakeClient()
        connecting = threading.Event()
        cancelled = threading.Event()

        @asynccontextmanager
        async def connect(**kwargs):
            connecting.set()
            try:
                await asyncio.Event().wait()
                yield client.session
            finally:
                cancelled.set()

        client.aio.live.connect = connect
        results = []
        cloud = GeminiRealtimeSTT(
            "placeholder", lambda text: None, lambda *_: None, lambda _: None,
            client_factory=lambda **kwargs: client,
        )
        caller = threading.Thread(target=lambda: results.append(cloud.start()))
        caller.start()
        try:
            self.assertTrue(connecting.wait(2))
        finally:
            cloud.close()
            caller.join(2)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(results, [False])

    def test_empty_and_unfinished_turn_events_do_not_shift_the_next_transcript(self):
        async def exercise():
            cloud = GeminiRealtimeSTT("placeholder", lambda text: None, record,
                                      lambda error: None)
            cloud._pending_ids.extend((1, 2, 3))

            class Session:
                def __init__(self):
                    self.turns = iter((
                        [(None, True)],
                        [("second", False), ("duplicate", False), (None, True)],
                        [("third", False), (None, True)],
                    ))

                async def receive(self):
                    for text, complete in next(self.turns):
                        yield SimpleNamespace(server_content=SimpleNamespace(
                            input_transcription=None if text is None else SimpleNamespace(
                                text=text, finished=text != "duplicate"),
                            turn_complete=complete,
                        ))
                    if not cloud._pending_ids:
                        cloud._closed.set()

            await cloud._receive_loop(Session())

        finals = []

        def record(utterance_id, text):
            finals.append((utterance_id, text))

        asyncio.run(exercise())
        self.assertEqual(finals, [(1, ""), (2, "second"), (3, "third")])

    def test_429_is_api_quota_and_does_not_expose_details(self):
        message = friendly_gemini_error(RuntimeError("1008 RESOURCE_EXHAUSTED HTTP 429 private"))
        self.assertIn("ліміту API", message)
        self.assertNotIn("private", message)

    def test_corrected_final_reaches_question_once(self):
        worker = AudioWorker(MagicMock(), "uk", None, False)
        worker._pending_cloud_finals[1] = Utterance(
            np.ones(16000, np.float32), 16000, 1, True,
        )
        questions = []
        worker.question_finished.connect(questions.append, Qt.ConnectionType.DirectConnection)
        worker._cloud_final(1, "Що така дезномція.")
        worker._cloud_final(1, "Що така дезномція.")
        self.assertEqual(questions, ["Що таке диз’юнкція."])

    def test_worker_uses_gemini_when_key_is_available(self):
        worker = AudioWorker(MagicMock(), "uk", None, False, gemini_key="placeholder")
        worker.capturer.start = MagicMock()
        worker.capturer.activate = MagicMock()
        worker.capturer.stop = MagicMock()
        worker.process_audio = MagicMock()
        with patch("examai.audio.stt_gemini.GeminiRealtimeSTT") as gemini:
            gemini.return_value.start.return_value = True
            worker.run()
        gemini.assert_called_once()
        worker.stt.prepare.assert_not_called()

    def test_worker_falls_back_to_local_whisper_without_key(self):
        worker = AudioWorker(MagicMock(), "uk", None, False)
        worker.capturer.start = MagicMock()
        worker.capturer.activate = MagicMock()
        worker.capturer.stop = MagicMock()
        worker.process_audio = MagicMock()
        worker.run()
        worker.stt.prepare.assert_called_once()


if __name__ == "__main__":
    unittest.main()
