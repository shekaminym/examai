"""Offline cost of exact code references and question assembly; no Gemini call."""

from pathlib import Path
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter

from examai.database import DatabaseManager
from examai.audio.question_aggregator import QuestionAggregator
from examai.ai.rag_engine import RAGEngine


class NoSemantic:
    def search(self, *_args):
        return [], "disabled"


def report(label, samples):
    print(f"{label} p50_ms={median(samples):.3f} "
          f"p95_ms={quantiles(samples, n=100, method='inclusive')[94]:.3f}")


def main():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "program.txt"
        source.write_text("#include <stdio.h>\n" +
                          "\n".join(f"int value_{n} = {n};" for n in range(2, 5001)),
                          encoding="utf-8")
        rag = RAGEngine(DatabaseManager(root / "notes.db"), semantic=NoSemantic())
        rag.index_file_sync(source)
        samples = []
        for _ in range(200):
            started = perf_counter()
            rag.retrieve_relevant_context("Поясни 18-й рядок мого коду", request_id="benchmark")
            samples.append((perf_counter() - started) * 1000)
        report("exact_line", samples)

        samples = []
        for _ in range(200):
            started = perf_counter()
            aggregate = QuestionAggregator(mode="auto")
            aggregate.add_final("Що таке рекурсія", now=0)
            aggregate.add_final("еее і рекурсивний спуск", now=1)
            aggregate.add_final("і підйом?", now=2)
            aggregate.finalize(force=True, now=3)
            samples.append((perf_counter() - started) * 1000)
        report("three_fragment_assembly_cpu", samples)
        print("auto_base_post_stt_wait_ms complete=520 ordinary=1150 open=2600")


if __name__ == "__main__":
    main()
