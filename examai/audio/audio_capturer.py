"""Non-blocking PyAudioWPatch capture for microphone and WASAPI loopback."""
from __future__ import annotations

import logging
import queue
import threading
from time import perf_counter
from typing import Any

import numpy as np

try:
    import pyaudiowpatch as pa
except ImportError:  # pragma: no cover - diagnostic for incomplete installs
    import pyaudio as pa


audio_log = logging.getLogger('phantom.audio')


class AudioCapturer:
    """Keep PortAudio's callback tiny and process PCM in the worker thread."""

    MAX_BLOCKS = 40
    FRAME_SECONDS = 0.02

    def __init__(self) -> None:
        self.audio_queue: queue.Queue[tuple[int, float, Any, bytes]] = queue.Queue(self.MAX_BLOCKS)
        self.audio: Any | None = None
        self.stream: Any | None = None
        self.sample_rate = 16000
        self.channels = 1
        self.current_rms = 0.0
        self.last_block_at: float | None = None
        self.last_device_latency_ms: float | None = None
        self.dropped = 0
        self._sample_dtype = np.float32
        self._lock = threading.Lock()

    @staticmethod
    def get_available_devices() -> list[dict[str, Any]]:
        backend = pa.PyAudio()
        try:
            devices = []
            for info in backend.get_device_info_generator():
                loopback = bool(info.get("isLoopbackDevice", False))
                if loopback or int(info.get("maxInputChannels", 0)) > 0:
                    devices.append({
                        "name": str(info.get("name", "Unknown device")),
                        "index": int(info["index"]),
                        "loopback": loopback,
                    })
            return devices
        finally:
            backend.terminate()

    def start(self, device_index: int | None = None, loopback: bool = True,
              *, activate: bool = True) -> None:
        self.stop()
        self.audio_queue = queue.Queue(self.MAX_BLOCKS)
        self.dropped = 0
        self.current_rms = 0.0
        self.last_block_at = None
        self.last_device_latency_ms = None
        self.audio = pa.PyAudio()
        try:
            audio_log.info("backend=%s version=%s requested_index=%s loopback=%s",
                           pa.__name__, getattr(pa, "__version__", "?"), device_index, loopback)
            info = self._select_device(device_index, loopback)
            self.sample_rate = int(info["defaultSampleRate"])
            native_channels = int(info["maxInputChannels"])
            if native_channels < 1 or self.sample_rate < 1:
                raise ValueError("Selected capture endpoint has no usable input format")
            audio_log.info("endpoint=%r input_index=%s host_api=%s loopback=%s native_channels=%d rate=%d",
                           info.get("name"), info["index"], info.get("hostApi"),
                           info.get("isLoopbackDevice"), native_channels, self.sample_rate)
            self.channels = native_channels if loopback else min(2, native_channels)
            options = dict(
                channels=self.channels, rate=self.sample_rate, input=True,
                input_device_index=int(info["index"]),
                frames_per_buffer=max(160, round(self.sample_rate * self.FRAME_SECONDS)),
                stream_callback=self._on_audio,
                start=False,
            )
            for audio_format, dtype in ((pa.paFloat32, np.float32), (pa.paInt16, np.int16)):
                audio_log.info("open input_index=%d channels=%d rate=%d format=%s frames=%d start=False",
                               options["input_device_index"], self.channels, self.sample_rate,
                               dtype.__name__, options["frames_per_buffer"])
                try:
                    self.stream = self.audio.open(format=audio_format, **options)
                except OSError as exc:
                    audio_log.warning("open rejected input_index=%d channels=%d rate=%d format=%s error=%r",
                                      options["input_device_index"], self.channels, self.sample_rate,
                                      dtype.__name__, exc)
                    if audio_format == pa.paInt16:
                        raise
                else:
                    self._sample_dtype = dtype
                    break
            if activate:
                self.activate()
        except Exception:
            audio_log.exception('capture initialization failed')
            self.stop()
            raise

    def activate(self) -> None:
        if self.stream is None:
            raise RuntimeError("Audio stream is not open")
        if not self.stream.is_active():
            self.stream.start_stream()
            audio_log.info('capture stream started channels=%d rate=%d', self.channels, self.sample_rate)

    def stop(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
                stream.close()
            except Exception:
                pass
        audio, self.audio = self.audio, None
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass

    def get_audio_data(self, timeout: float = 0.05):
        """Return only PCM from the newest continuous capture generation."""
        while True:
            try:
                generation, captured_at, time_info, raw = self.audio_queue.get(timeout=timeout)
            except queue.Empty:
                return None
            with self._lock:
                newest_generation = self.dropped
            if generation != newest_generation:
                # A full queue means a hole in the waveform.  Decoding the
                # remaining old fragments as a single phrase is worse than
                # restarting from the newest continuous data.
                continue
            samples = np.frombuffer(raw, dtype=self._sample_dtype)
            usable = len(samples) - (len(samples) % self.channels)
            if not usable:
                continue
            samples = samples[:usable].reshape(-1, self.channels)
            if self._sample_dtype == np.int16:
                samples = samples.astype(np.float32) / 32768.0
            samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
            mono = np.mean(samples, axis=1, dtype=np.float32) if self.channels > 1 else samples[:, 0]
            power = np.mean(np.square(mono, dtype=np.float64))
            if self.channels > 1:
                channel_power = np.mean(np.square(samples, dtype=np.float64), axis=0)
                strongest = int(np.argmax(channel_power))
                # Preserve speech from phase-inverted stereo. A single active
                # channel still passes through the ordinary stereo downmix.
                if power < channel_power[strongest] / 16:
                    mono = samples[:, strongest].copy()
                    power = channel_power[strongest]
            rms = float(np.sqrt(power))
            self.current_rms = rms
            self.last_block_at = captured_at
            self.last_device_latency_ms = None
            if time_info:
                adc = time_info.get("input_buffer_adc_time", 0)
                current = time_info.get("current_time", 0)
                if adc > 0 and 0 <= current - adc < 5:
                    self.last_device_latency_ms = (current - adc) * 1000
            return generation, mono, rms

    def _select_device(self, device_index: int | None, loopback: bool) -> dict[str, Any]:
        if loopback:
            if not hasattr(self.audio, "get_default_wasapi_loopback"):
                raise RuntimeError("WASAPI loopback requires PyAudioWPatch")
            if device_index is None or device_index == -1:
                wasapi = self.audio.get_host_api_info_by_type(pa.paWASAPI)
                audio_log.info("WASAPI host_index=%s default_render_index=%s",
                               wasapi.get("index"), wasapi.get("defaultOutputDevice"))
                candidate = self.audio.get_default_wasapi_loopback()
            else:
                candidate = self.audio.get_device_info_by_index(device_index)
                if not candidate.get("isLoopbackDevice"):
                    host = self.audio.get_host_api_info_by_index(candidate["hostApi"])
                    if host["type"] != pa.paWASAPI or candidate["maxOutputChannels"] < 1:
                        raise ValueError("Selected index is not a WASAPI playback or loopback endpoint")
                    audio_log.info("mapping WASAPI render_index=%d to loopback", device_index)
                    candidate = self.audio.get_wasapi_loopback_analogue_by_dict(candidate)
            if not candidate.get("isLoopbackDevice"):
                raise ValueError("Resolved endpoint is not a WASAPI loopback input")
            return candidate
        if device_index is not None:
            candidate = self.audio.get_device_info_by_index(device_index)
            if int(candidate.get("maxInputChannels", 0)) > 0 and not candidate.get("isLoopbackDevice"):
                return candidate
        return self.audio.get_default_input_device_info()

    def _on_audio(self, in_data, frame_count, time_info, status_flags):
        # Do not resample, decode, or calculate RMS here: PortAudio needs this
        # callback returned promptly or it will insert gaps in the stream.
        captured_at = perf_counter()
        with self._lock:
            if status_flags & pa.paInputOverflow:
                self.dropped += 1
            generation = self.dropped
        try:
            self.audio_queue.put_nowait((generation, captured_at, time_info, bytes(in_data)))
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            with self._lock:
                self.dropped += 1
                generation = self.dropped
            try:
                self.audio_queue.put_nowait((generation, captured_at, time_info, bytes(in_data)))
            except queue.Full:
                pass
        return None, pa.paContinue
