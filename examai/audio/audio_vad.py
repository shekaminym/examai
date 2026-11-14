"""Low-latency, dependency-free speech segmentation.

The audio callback must stay small.  This module runs in the audio worker and
turns captured blocks into complete utterances.  It deliberately has no Qt or
Whisper dependency, which makes its timing behaviour easy to test.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import gcd
from time import perf_counter
from typing import Deque

import numpy as np


@dataclass(frozen=True)
class Utterance:
    """Audio ready for one Whisper pass."""

    audio: np.ndarray
    sample_rate: int
    utterance_id: int
    is_final: bool
    queued_at: float = field(default_factory=perf_counter, compare=False)
    speech_ended_at: float | None = field(default=None, compare=False)


class AdaptiveEnergyVAD:
    """A conservative VAD that learns the current device's noise floor.

    It uses hysteresis: starting an utterance requires more energy than keeping
    one alive.  That avoids cutting quiet syllables while rejecting stationary
    fan/room noise.  Values are RMS levels for normalised float32 PCM.
    """

    def __init__(
        self,
        floor: float = 0.006,
        start_multiplier: float = 2.4,
        continue_multiplier: float = 1.55,
    ) -> None:
        self.floor = floor
        self.start_multiplier = start_multiplier
        self.continue_multiplier = continue_multiplier
        self.noise_rms = floor
        self._in_speech = False

    def reset(self) -> None:
        self._in_speech = False

    def is_speech(self, audio: np.ndarray, rms: float | None = None) -> bool:
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not samples.size:
            return False
        if rms is None:
            rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        if not np.isfinite(rms):
            return False

        multiplier = self.continue_multiplier if self._in_speech else self.start_multiplier
        threshold = max(self.floor, self.noise_rms * multiplier)
        speech = rms >= threshold
        # Learn only clearly non-speech blocks.  Otherwise normal speech would
        # quickly raise the floor and make its tail disappear.
        if not speech and rms < max(self.floor * 2.0, self.noise_rms * 1.25):
            self.noise_rms = 0.96 * self.noise_rms + 0.04 * rms
        self._in_speech = speech
        return speech


class NeuralVAD:
    """Silero speech probabilities using the model bundled with faster-whisper."""

    def __init__(self, sample_rate: int) -> None:
        from faster_whisper.vad import get_vad_model
        from scipy.signal import resample_poly

        self.session = get_vad_model().session
        self.resample = resample_poly
        divisor = gcd(sample_rate, 16000)
        self.up, self.down = 16000 // divisor, sample_rate // divisor
        self.reset()

    def reset(self) -> None:
        self.hidden = np.zeros((1, 1, 128), dtype=np.float32)
        self.cell = np.zeros_like(self.hidden)
        self.context = np.zeros(64, dtype=np.float32)
        self.pending = np.empty(0, dtype=np.float32)
        self.speech = False

    def is_speech(self, audio: np.ndarray, rms: float | None = None) -> bool:
        if self.up != self.down:
            audio = self.resample(audio, self.up, self.down)
        frames = np.concatenate((self.pending, audio))
        count = len(frames) // 512
        voiced = False
        for index in range(count):
            frame = frames[index * 512:(index + 1) * 512]
            model_input = np.concatenate((self.context, frame))[None, :]
            scores, self.hidden, self.cell = self.session.run(
                None, {"input": model_input, "h": self.hidden, "c": self.cell}
            )
            self.context = frame[-64:].copy()
            self.speech = float(scores.item()) >= (0.35 if self.speech else 0.5)
            voiced |= self.speech
        self.pending = frames[count * 512:].copy()
        return voiced if count else self.speech


class SpeechSegmenter:
    """Builds utterances, previews, and final chunks from PCM blocks."""

    def __init__(
        self,
        *,
        previews: bool = True,
        vad: AdaptiveEnergyVAD | NeuralVAD | None = None,
        pre_roll_seconds: float = 0.24,
        min_speech_seconds: float = 0.28,
        end_silence_seconds: float = 0.55,
        keep_tail_seconds: float = 0.16,
        preview_after_seconds: float = 1.25,
        preview_interval_seconds: float = 1.20,
        max_utterance_seconds: float = 15.0,
    ) -> None:
        self.previews = previews
        self.pre_roll_seconds = pre_roll_seconds
        self.min_speech_seconds = min_speech_seconds
        self.end_silence_seconds = end_silence_seconds
        self.keep_tail_seconds = keep_tail_seconds
        self.preview_after_seconds = preview_after_seconds
        self.preview_interval_seconds = preview_interval_seconds
        self.max_utterance_seconds = max_utterance_seconds
        self.vad = AdaptiveEnergyVAD() if vad is None else vad
        self._rate: int | None = None
        self._pre_roll: Deque[np.ndarray] = deque()
        self._pre_roll_samples = 0
        self._chunks: list[np.ndarray] = []
        self._samples = self._speech_samples = self._silence_samples = 0
        self._speech_ended_at = None
        self._last_preview_samples = 0
        self._utterance_id = 0
        self._last_audio_at: float | None = None
        self.started_audio: np.ndarray | None = None

    @property
    def active(self) -> bool:
        return bool(self._chunks)

    def reset(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_samples = 0
        self._chunks.clear()
        self._samples = self._speech_samples = self._silence_samples = 0
        self._last_preview_samples = 0
        self._last_audio_at = None
        self.started_audio = None
        self.vad.reset()

    @property
    def utterance_id(self) -> int:
        return self._utterance_id

    def confirmed_audio(self) -> np.ndarray | None:
        if self._rate is None or self._speech_samples < int(self.min_speech_seconds * self._rate):
            return None
        return np.concatenate(self._chunks)

    def push(
        self, audio: np.ndarray, sample_rate: int, rms: float | None = None, now: float | None = None
    ) -> list[Utterance]:
        """Add one audio block and return due preview/final utterances."""
        if sample_rate <= 0:
            return []
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not samples.size:
            return []
        self.started_audio = None
        if self._rate is not None and sample_rate != self._rate:
            # A device format change makes the partially collected phrase
            # ambiguous; do not decode a stitched, corrupted question.
            self.reset()
        self._rate = sample_rate
        self._last_audio_at = perf_counter() if now is None else now
        voiced = self.vad.is_speech(samples, rms)

        if not self.active:
            if not voiced:
                self._add_pre_roll(samples)
                return []
            self._chunks.extend(self._pre_roll)
            self._samples = self._pre_roll_samples
            self.started_audio = np.concatenate((*self._pre_roll, samples))
            self._pre_roll.clear()
            self._pre_roll_samples = 0
            self._utterance_id += 1

        self._chunks.append(samples)
        self._samples += len(samples)
        if voiced:
            self._speech_ended_at = self._last_audio_at
            self._speech_samples += len(samples)
            self._silence_samples = 0
        else:
            self._silence_samples += len(samples)

        messages: list[Utterance] = []
        can_decode = self._speech_samples >= int(self.min_speech_seconds * sample_rate)
        final_due = (
            self._silence_samples >= int(self.end_silence_seconds * sample_rate)
            or self._samples >= int(self.max_utterance_seconds * sample_rate)
        )
        preview_due = (
            self.previews and voiced
            and self._samples >= int(self.preview_after_seconds * sample_rate)
            and self._samples - self._last_preview_samples >= int(self.preview_interval_seconds * sample_rate)
        )
        if final_due:
            if can_decode:
                messages.append(self._make_utterance(is_final=True))
            # Do not let a brief click/noise keep the segmenter "active" until
            # its maximum duration; it would swallow the next real phrase.
            self._clear_active()
        elif can_decode and preview_due:
            messages.append(self._make_utterance(is_final=False))
            self._last_preview_samples = self._samples
        return messages

    def flush_if_idle(self, now: float | None = None) -> list[Utterance]:
        """Finish an utterance when a capture backend stopped delivering data."""
        if not self.active or self._rate is None or self._last_audio_at is None:
            return []
        current = perf_counter() if now is None else now
        if current - self._last_audio_at < self.end_silence_seconds:
            return []
        if self._speech_samples < int(self.min_speech_seconds * self._rate):
            self._clear_active()
            return []
        utterance = self._make_utterance(is_final=True)
        self._clear_active()
        return [utterance]

    def _add_pre_roll(self, samples: np.ndarray) -> None:
        assert self._rate is not None
        self._pre_roll.append(samples)
        self._pre_roll_samples += len(samples)
        maximum = int(self.pre_roll_seconds * self._rate)
        while self._pre_roll and self._pre_roll_samples > maximum:
            removed = self._pre_roll.popleft()
            self._pre_roll_samples -= len(removed)

    def _make_utterance(self, *, is_final: bool) -> Utterance:
        assert self._rate is not None
        audio = np.concatenate(self._chunks)
        if is_final and self._silence_samples:
            keep = min(self._silence_samples, int(self.keep_tail_seconds * self._rate))
            trim = self._silence_samples - keep
            if trim > 0:
                audio = audio[:-trim]
        return Utterance(audio, self._rate, self._utterance_id, is_final,
                         speech_ended_at=self._speech_ended_at)

    def _clear_active(self) -> None:
        self._chunks.clear()
        self._samples = self._speech_samples = self._silence_samples = 0
        self._last_preview_samples = 0
        self._last_audio_at = None
        if isinstance(self.vad, AdaptiveEnergyVAD):
            self.vad.reset()
