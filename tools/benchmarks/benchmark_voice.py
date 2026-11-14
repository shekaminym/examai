"""Offline A/B replay: controlled STT/LLM service times, real endpoint and hedge."""
import argparse
from math import ceil
from pathlib import Path
import statistics
import sys
from time import monotonic, sleep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    sys.path.insert(0, str(source))
    packaged = (source / "examai" / "app.py").is_file()

    import numpy as np
    if packaged:
        from examai.audio import audio_vad
        from examai.app import AudioWorker, DecodeMailbox
    else:
        import audio_vad
        from main import AudioWorker, DecodeMailbox
    SpeechSegmenter = audio_vad.SpeechSegmenter
    clock = getattr(audio_vad, "perf_counter", monotonic)

    voiced = np.full(320, .1, np.float32)
    silent = np.zeros_like(voiced)

    def trial():
        segmenter = SpeechSegmenter(previews=False, end_silence_seconds=.42)
        worker = AudioWorker(None, "uk", None, False)
        worker._mailbox = DecodeMailbox()
        for _ in range(25):
            segmenter.push(voiced, 16000, .1)
        speech_end = clock()
        for _ in range(21):
            sleep(.020)
            finals = segmenter.push(silent, 16000, 0)
        utterance, = finals
        worker._pending_cloud_finals[utterance.utterance_id] = utterance

        while True:
            worker._fallback_slow_cloud()
            # Deterministic simulation of an unresponsive cloud session.
            if (getattr(worker, "_hedged_cloud_ids", None)
                    or not worker._pending_cloud_finals):
                break
            sleep(.010)
        assert worker._mailbox.get() is utterance
        hedge = clock()
        sleep(.120)  # warm local STT fixture
        sleep(.200)  # LLM first-text fixture
        first_text = clock()
        return (utterance.queued_at - speech_end, hedge - utterance.queued_at,
                first_text - speech_end)

    samples = list(zip(*(trial() for _ in range(args.repeats))))
    for label, values in zip(("endpoint", "fallback", "first_text_simulated"), samples):
        p95 = sorted(values)[ceil(.95 * len(values)) - 1]
        print(f"{label}_p50_ms={statistics.median(values) * 1000:.1f} p95_ms={p95 * 1000:.1f}")


if __name__ == "__main__":
    main()
