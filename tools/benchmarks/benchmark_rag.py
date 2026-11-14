"""Compare BM25 and local semantic RAG on the same synthetic notes."""

import argparse
from pathlib import Path
from statistics import median, quantiles
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic", action="store_true", help="Use the installed E5 model")
    parser.add_argument("--source", default=Path(__file__).resolve().parents[2],
                        help="Run against another checkout for the BM25 baseline")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    sys.path.insert(0, str(source))
    packaged = (source / "examai" / "app.py").is_file()

    if packaged:
        from examai.database import DatabaseManager
        from examai.ai.rag_engine import RAGEngine
    else:
        from database import DatabaseManager
        from rag_engine import RAGEngine
    semantic_module = "examai.ai.semantic_search" if packaged else "semantic_search"

    with TemporaryDirectory() as temporary:
        db = DatabaseManager(Path(temporary) / "rag.db")
        db.replace_document("algebra.txt", [
            f"Вектор і матриця. Linear algebra notes. Chapter {i % 32}."
            for i in range(10_000)
        ])
        db.replace_document("biology.txt", [
            "Photosynthesis converts sunlight, carbon dioxide and water into glucose and oxygen."
        ])
        if args.semantic:
            if packaged:
                from examai.ai.semantic_index import E5Encoder, build_cache
                from examai.ai.semantic_search import SemanticSearch
            else:
                from semantic_index import E5Encoder, build_cache
                from semantic_search import SemanticSearch

            start = perf_counter()
            indexed = build_cache(db)
            print(f"offline_unique_passages={indexed} indexing_seconds={perf_counter() - start:.2f}")
            semantic = SemanticSearch(db, E5Encoder)
            try:
                if semantic._preparing.result(timeout=30) is None:
                    raise RuntimeError("Local semantic index unavailable")
                measure(RAGEngine(db, semantic=semantic))
            finally:
                semantic._worker.shutdown()
        elif semantic_module in sys.modules:
            with patch(f"{semantic_module}.MODEL_DIR", Path(temporary)):
                measure(RAGEngine(db))
        else:
            measure(RAGEngine(db))


def measure(rag):
    cases = {
        "lexical_uk": "вектор",
        "lexical_en": "algebra",
        "paraphrase_ru": "Как растения получают сахар из света?",
    }
    for label, query in cases.items():
        for workload in ("repeated", "unique"):
            samples = []
            for index in range(100):
                question = query if workload == "repeated" else f"{query} {index}"
                start = perf_counter()
                context = rag.retrieve_relevant_context(question, limit=2)
                samples.append((perf_counter() - start) * 1000)
            p95 = quantiles(samples, n=100, method="inclusive")[94]
            first_source = context.splitlines()[0] if context else "(empty)"
            print(f"{label} {workload} p50_ms={median(samples):.2f} "
                  f"p95_ms={p95:.2f} top={first_source}")


if __name__ == "__main__":
    main()
