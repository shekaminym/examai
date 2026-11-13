"""Summarize timing-only EXAM AI logs without displaying question text."""
import argparse
import re
from collections import defaultdict
from pathlib import Path
from statistics import median, quantiles

METRICS = (
    "vad_first_block_ms", "stt_final_from_speech_end_ms", "decode_ms", "wait_ms",
    "rag_ms", "llm_ttft_ms",
    "dock_first_paint_ms", "llm_first_text_ms", "first_ui_text_ms", "llm_complete_ms",
    "end_to_end_complete_ms", "gui_lag_ms",
    "expand_ms", "animation_fps", "cpu_pct", "rss_mib",
)
PATTERN = re.compile(r"\b(" + "|".join(METRICS) + r")=(-?\d+(?:\.\d+)?)\b")


def summarize(path):
    samples = defaultdict(list)
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        for name, value in PATTERN.findall(line):
            if float(value) >= 0:
                samples[name].append(float(value))
    for name in METRICS:
        values = samples[name]
        if values:
            p95 = quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0]
            print(f"{name:24} n={len(values):4} p50={median(values):8.2f} p95={p95:8.2f}")
        else:
            print(f"{name:24} no samples")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    summarize(parser.parse_args().log)
