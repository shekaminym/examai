"""Local Whisper decoding for responsive previews and accurate final text."""
import os
from math import gcd

import numpy as np
from faster_whisper import WhisperModel
from scipy.ndimage import uniform_filter1d
from scipy.signal import istft, resample_poly, stft


ACADEMIC_PROMPT = (
    "Українська мова. Лекція, викладач, математика, фізика, "
    "радіус-вектор, радіус-вектора, координати, матриця, вектор, "
    "інтеграл, похідна, алгоритм, формула, теорема, "
    "диз’юнкція, кон’юнкція, об’єднання, комп’ютер, зв’язок, "
    "рекурсивний метод, ітеративний метод."
)


def reduce_stationary_noise(audio, sample_rate):
    """Estimate the noise spectrum from the quietest fifth of the frames."""
    window = round(sample_rate * 0.025)
    overlap = round(sample_rate * 0.015)
    _, _, spectrum = stft(audio, fs=sample_rate, nperseg=window, noverlap=overlap)
    power = np.abs(spectrum) ** 2
    frame_power = power.sum(axis=0)
    count = max(1, len(frame_power) // 5)
    quiet = np.argpartition(frame_power, count - 1)[:count]
    noise = np.median(power[:, quiet], axis=1, keepdims=True)
    gain = np.sqrt(np.maximum(0.15, 1 - 1.8 * noise / np.maximum(power, 1e-10)))
    gain = uniform_filter1d(gain, 3, axis=1)
    _, filtered = istft(spectrum * gain, fs=sample_rate, nperseg=window, noverlap=overlap)
    return filtered[:len(audio)].astype(np.float32, copy=False)


class STTEngine:
    def __init__(self, model_size="base", device="cpu", compute_type="int8", noise_reduction=False):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.noise_reduction = noise_reduction
        self.model = None

    def prepare(self):
        if self.model is None:
            options = {}
            if self.device == "cpu":
                # Avoid excessive CPU-thread contention during live capture.
                options["cpu_threads"] = min(6, max(1, os.cpu_count() or 4))
            self.model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type,
                **options,
            )

    def transcribe_chunk(self, audio_data, language="uk", sample_rate=16000, preview=False):
        if len(audio_data) < sample_rate // 4:
            return "", ""
        audio = np.asarray(audio_data, dtype=np.float32).reshape(-1)
        audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
        if self.noise_reduction:
            audio = reduce_stationary_noise(audio, sample_rate)
        if sample_rate != 16000:
            divisor = gcd(16000, sample_rate)
            audio = resample_poly(audio, 16000 // divisor, sample_rate // divisor).astype(np.float32)
        if audio.size < 4000:
            return "", ""
        # Guard against bad device samples.  Removing a material DC offset
        # helps some virtual-loopback drivers, but do not turn a very short,
        # low-variation block into artificial silence.
        centered = audio - np.float32(np.mean(audio, dtype=np.float64))
        centered_rms = float(np.sqrt(np.mean(np.square(centered, dtype=np.float64))))
        if centered_rms >= 0.0007:
            audio = centered
        audio = audio.astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
        peak = float(np.max(np.abs(audio)))
        if peak < 0.001 or rms < 0.0007:  # Avoid silence hallucinations.
            return "", ""
        # Bring quiet speech into the range used by Whisper training without
        # over-amplifying background noise or clipping strong loopback audio.
        if rms < 0.055:
            audio = audio * min(3.0, 0.075 / rms)
        audio = np.clip(audio, -1.0, 1.0)
        self.prepare()
        segments, info = self.model.transcribe(
            audio,
            beam_size=1 if preview else 3,
            best_of=1 if preview else 3,
            language=None if language == "auto" else language,
            initial_prompt=ACADEMIC_PROMPT if language == "uk" else None,
            # Audio has already been segmented by the capture worker. Running a
            # second VAD here can erase a short first/last word and adds delay.
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=True,
            temperature=0.0,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.55,
        )
        return " ".join(seg.text.strip() for seg in segments).strip(), info.language
