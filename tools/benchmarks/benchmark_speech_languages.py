"""Paired synthetic-speech WER/CER for the previous and current speech presets.

Piper 1.4.2 and its voice files are optional QA dependencies. Run from the
repository root with python -m tools.benchmarks.benchmark_speech_languages VOICES.
No audio is played or captured from the workstation.
"""
import argparse
import json
import re
import tempfile
import wave
from pathlib import Path
from time import perf_counter

import numpy as np
from faster_whisper.audio import decode_audio
from piper import PiperVoice, SynthesisConfig

from examai.audio.speech_correction import SPEECH_GLOSSARY, SpeechCorrector, has_non_ukrainian_cyrillic
from examai.audio.stt_engine import STTEngine


VOICES = {"uk": "uk_UA-lada-x_low", "ru": "ru_RU-irina-medium", "en": "en_US-lessac-low"}
CASES = {
    "uk": ("Що таке матриця?", "Поясни різницю між інтегралом і похідною.",
           "Як працює рекурсивний метод і коли він використовує стек викликів?"),
    "ru": ("Что такое матрица?", "Объясни разницу между интегралом и производной.",
           "Как работает рекурсивный метод и когда он использует стек вызовов?"),
    "en": ("What is a matrix?", "Explain the difference between an integral and a derivative.",
           "How does a recursive method work and when does it use the call stack?"),
}


def distance(reference, actual):
    row = list(range(len(actual) + 1))
    for i, expected in enumerate(reference, start=1):
        following = [i]
        for j, observed in enumerate(actual, start=1):
            following.append(min(row[j] + 1, following[-1] + 1, row[j-1] + (expected != observed)))
        row = following
    return row[-1]


def words(text):
    return re.findall(r"[^\W_]+", text.casefold().replace("’", "").replace("'", ""), flags=re.UNICODE)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("voices", type=Path)
    args.add_argument("--model", default="base")
    options = args.parse_args()
    engine = STTEngine(options.model)
    engine.prepare()
    corrector = SpeechCorrector(SPEECH_GLOSSARY)
    samples = []
    synthesis = SynthesisConfig(noise_scale=0.0, noise_w_scale=0.0)
    with tempfile.TemporaryDirectory() as folder:
        file = Path(folder) / "sample.wav"
        for language, cases in CASES.items():
            voice = PiperVoice.load(options.voices / (VOICES[language] + ".onnx"))
            for phrase in cases:
                with wave.open(str(file), "wb") as out:
                    voice.synthesize_wav(phrase, out, syn_config=synthesis)
                samples.append((language, phrase, decode_audio(str(file), sampling_rate=48000)))
    left, right = samples[1], samples[5]
    samples.append(("mixed", left[1] + " " + right[1], np.concatenate((left[2], right[2]))))
    rows = []
    for group, phrase, signal in samples:
        for preset in ("uk", "auto"):
            started = perf_counter()
            text, detected = engine.transcribe_chunk(signal, language=preset, sample_rate=48000)
            if preset == "uk" or (detected == "uk" and not has_non_ukrainian_cyrillic(text)):
                text = corrector.correct(text)
            milliseconds = (perf_counter() - started) * 1000
            expected, actual = words(phrase), words(text)
            expected_chars, actual_chars = " ".join(expected), " ".join(actual)
            rows.append(dict(group=group, preset=preset, detected=detected, reference=phrase, text=text,
                             word_errors=distance(expected, actual), words=len(expected),
                             character_errors=distance(expected_chars, actual_chars), characters=len(expected_chars),
                             decode_ms=round(milliseconds)))
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
