"""Offline capture-queue checks; PortAudio is replaced by a mock."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from audio_capturer import AudioCapturer, pa


class AudioCapturerTests(unittest.TestCase):
    @patch("audio_capturer.pa.PyAudio")
    def test_mic_retries_pcm16_when_float_capture_is_unsupported(self, factory):
        backend = factory.return_value
        backend.get_default_input_device_info.return_value = {
            "isLoopbackDevice": False, "maxInputChannels": 1,
            "defaultSampleRate": 16000, "index": 2,
        }
        backend.open.side_effect = [OSError("float unsupported"), MagicMock()]
        capture = AudioCapturer()
        capture.start(loopback=False)
        self.addCleanup(capture.stop)
        self.assertEqual(backend.open.call_count, 2)
        self.assertEqual(backend.open.call_args.kwargs["format"], pa.paInt16)
        self.assertEqual(backend.open.call_args.kwargs["frames_per_buffer"], 320)
        raw = np.full(320, 8192, dtype=np.int16).tobytes()
        capture._on_audio(raw, 320, None, 0)
        _, mono, rms = capture.get_audio_data()
        self.assertAlmostEqual(float(mono.mean()), .25, places=4)
        self.assertAlmostEqual(rms, .25, places=4)

    @patch("audio_capturer.pa.PyAudio")
    def test_capture_waits_for_vad_before_stream_activation(self, factory):
        backend = factory.return_value
        backend.get_default_input_device_info.return_value = {
            "isLoopbackDevice": False, "maxInputChannels": 1,
            "defaultSampleRate": 16000, "index": 2,
        }
        backend.open.return_value.is_active.return_value = False
        capture = AudioCapturer()
        capture.start(loopback=False, activate=False)
        self.addCleanup(capture.stop)
        self.assertFalse(backend.open.return_value.start_stream.called)
        self.assertIs(backend.open.call_args.kwargs["start"], False)
        capture.activate()
        backend.open.return_value.start_stream.assert_called_once()

    @patch("audio_capturer.pa.PyAudio")
    def test_loopback_queue_is_bounded_and_old_gap_is_discarded(self, factory):
        backend = factory.return_value
        backend.get_default_wasapi_loopback.return_value = {
            "isLoopbackDevice": True,
            "maxInputChannels": 2,
            "defaultSampleRate": 48000,
            "index": 5,
        }
        capture = AudioCapturer()
        capture.start()
        self.addCleanup(capture.stop)
        open_kwargs = backend.open.call_args.kwargs
        self.assertEqual(open_kwargs["input_device_index"], 5)
        self.assertEqual(open_kwargs["channels"], 2)
        backend.get_default_input_device_info.assert_not_called()

        raw = np.full((4800, 2), 0.25, dtype=np.float32).tobytes()
        for _ in range(45):
            open_kwargs["stream_callback"](raw, 4800, None, 0)
        self.assertEqual(capture.audio_queue.qsize(), 40)
        self.assertEqual(capture.dropped, 5)
        generation, mono, rms = capture.get_audio_data()
        self.assertEqual(generation, 5)
        self.assertEqual(mono.shape, (4800,))
        self.assertAlmostEqual(rms, 0.25)

    @patch("audio_capturer.pa.PyAudio")
    def test_wasapi_render_index_resolves_to_native_eight_channel_input(self, factory):
        backend = factory.return_value
        render = {"index": 35, "hostApi": 2, "maxOutputChannels": 8, "isLoopbackDevice": False}
        loopback = {"index": 49, "hostApi": 2, "maxInputChannels": 8,
                    "defaultSampleRate": 48000, "isLoopbackDevice": True}
        backend.get_device_info_by_index.return_value = render
        backend.get_host_api_info_by_index.return_value = {"type": pa.paWASAPI}
        backend.get_wasapi_loopback_analogue_by_dict.return_value = loopback
        capture = AudioCapturer()
        self.addCleanup(capture.stop)
        capture.start(35, activate=False)
        backend.get_wasapi_loopback_analogue_by_dict.assert_called_once_with(render)
        options = backend.open.call_args.kwargs
        self.assertEqual((options["input_device_index"], options["channels"], options["rate"]),
                         (49, 8, 48000))
        self.assertNotIn("as_loopback", options)
        samples = np.zeros((960, 8), np.float32)
        samples[:, 4] = .125
        capture._on_audio(samples.tobytes(), len(samples), None, 0)
        _, mono, _ = capture.get_audio_data()
        np.testing.assert_allclose(mono, .125)

    def test_overflow_discards_old_audio_and_recovers_inverted_stereo(self):
        capture = AudioCapturer()
        capture.channels = 2
        samples = np.linspace(-0.1, 0.1, 960, dtype=np.float32)
        stereo = np.column_stack((samples, -samples))
        capture._on_audio(bytes(stereo.nbytes), len(samples), None, 0)
        capture._on_audio(stereo.tobytes(), len(samples), None, pa.paInputOverflow)
        generation, mono, rms = capture.get_audio_data()
        self.assertEqual(generation, 1)
        np.testing.assert_array_equal(mono, samples)
        self.assertGreater(rms, 0.05)

    @patch("audio_capturer.pa.PyAudio")
    def test_open_error_releases_the_backend(self, factory):
        backend = factory.return_value
        backend.get_device_info_by_index.return_value = {
            "isLoopbackDevice": False,
            "maxInputChannels": 1,
            "defaultSampleRate": 48000,
            "index": 3,
        }
        backend.open.side_effect = OSError("unavailable")
        capture = AudioCapturer()
        with self.assertRaises(OSError):
            capture.start(3, False)
        backend.terminate.assert_called_once()
        self.assertIsNone(capture.stream)


if __name__ == "__main__":
    unittest.main(verbosity=2)
