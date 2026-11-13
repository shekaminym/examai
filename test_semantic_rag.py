import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter
import unittest
from unittest.mock import patch

import numpy as np

from database import DatabaseManager
from rag_engine import RAGEngine, MAX_CONTEXT_CHARS
from semantic_index import DIMENSIONS, E5Encoder, build_cache
from semantic_search import SemanticSearch


class FixtureEncoder:
    def __init__(self):
        self.query_calls = 0
        self.block = None

    def encode(self, texts, *, query=False):
        if query:
            self.query_calls += 1
            if self.block is not None:
                self.block.wait(5)
        vectors = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        for index, text in enumerate(texts):
            vectors[index, int("matrix" in text or "table" in text)] = 1
        return vectors


class SemanticRAGTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = DatabaseManager(Path(directory.name) / "notes.db")

    def ready_search(self, make_encoder):
        search = SemanticSearch(self.db, make_encoder)
        self.addCleanup(search._worker.shutdown)
        search._preparing.result(timeout=15)
        return search

    def test_cache_reuses_duplicate_passages_and_hybrid_promotes_overlap(self):
        self.db.replace_document("a.txt", ["matrix", "matrix"])
        self.db.replace_document("b.txt", ["matrix matrix"])
        encoder = FixtureEncoder()
        self.assertEqual(build_cache(self.db, lambda: encoder), 2)
        self.assertEqual(build_cache(self.db, lambda: self.fail("cache miss")), 0)
        search = self.ready_search(lambda: encoder)
        rag = RAGEngine(self.db, semantic=search)
        with self.assertLogs("phantom.timing.rag", level="INFO") as captured:
            self.assertTrue(rag.retrieve_relevant_context("matrix", limit=1).startswith("[b.txt]"))
        self.assertIn("semantic_hits=3", captured.output[0])
        self.assertNotIn("matrix", captured.output[0])
        rag.retrieve_relevant_context("matrix", limit=1)
        self.assertEqual(encoder.query_calls, 2)  # warmup and one unique question
        self.db.replace_document("b.txt", ["new passage"])
        self.db.delete_document_source("a.txt")
        self.assertEqual(rag.retrieve_relevant_context("table"), "")
        self.assertIn("new passage", rag.retrieve_relevant_context("passage"))

    def test_context_budget_and_punctuation(self):
        self.db.replace_document("notes.txt", ["matrix " * 1000] * 10)
        build_cache(self.db, FixtureEncoder)
        rag = RAGEngine(self.db, semantic=self.ready_search(FixtureEncoder))
        self.assertEqual(rag.retrieve_relevant_context(') " * -'), "")
        for limit, budget in ((2, 1600), (4, MAX_CONTEXT_CHARS)):
            self.assertEqual(len(rag.retrieve_relevant_context("matrix", limit=limit)), budget)

    def test_slow_encoder_falls_back_and_does_not_queue_more_inference(self):
        self.db.replace_document("notes.txt", ["matrix"])
        encoder = FixtureEncoder()
        build_cache(self.db, lambda: encoder)
        rag = RAGEngine(self.db, semantic=self.ready_search(lambda: encoder))
        encoder.block = Event()
        self.addCleanup(encoder.block.set)
        started = perf_counter()
        with self.assertLogs("phantom.timing.rag", level="INFO") as captured:
            self.assertIn("matrix", rag.retrieve_relevant_context("matrix"))
            self.assertIn("matrix", rag.retrieve_relevant_context("matrix more"))
        self.assertLess(perf_counter() - started, 0.30)
        self.assertIn("semantic_status=deadline", captured.output[0])
        self.assertIn("semantic_status=busy", captured.output[1])
        self.assertEqual(encoder.query_calls, 2)

    def test_warming_model_does_not_delay_bm25(self):
        self.db.replace_document("notes.txt", ["matrix"])
        build_cache(self.db, FixtureEncoder)
        waiting = Event()
        release = Event()

        def make_encoder():
            waiting.set()
            release.wait(5)
            return FixtureEncoder()

        search = SemanticSearch(self.db, make_encoder)
        self.addCleanup(search._worker.shutdown)
        self.addCleanup(release.set)
        self.assertTrue(waiting.wait(5))
        started = perf_counter()
        with self.assertLogs("phantom.timing.rag", level="INFO") as captured:
            self.assertIn("matrix", RAGEngine(self.db, semantic=search).retrieve_relevant_context("matrix"))
        self.assertLess(perf_counter() - started, 0.10)
        self.assertIn("semantic_status=warming", captured.output[0])

    def test_missing_or_failing_model_preserves_bm25(self):
        self.db.replace_document("notes.txt", ["matrix"])
        with patch("semantic_search.MODEL_DIR", Path("missing-model")):
            self.assertIn("matrix", RAGEngine(self.db).retrieve_relevant_context("matrix"))
        build_cache(self.db, FixtureEncoder)
        def unavailable_encoder():
            raise ImportError("Optional dependency missing")

        search = self.ready_search(unavailable_encoder)
        self.assertIn("matrix", RAGEngine(self.db, semantic=search).retrieve_relevant_context("matrix"))

    def test_failed_inference_preserves_bm25(self):
        self.db.replace_document("notes.txt", ["matrix"])
        encoder = FixtureEncoder()
        build_cache(self.db, lambda: encoder)
        rag = RAGEngine(self.db, semantic=self.ready_search(lambda: encoder))
        with patch.object(encoder, "encode", side_effect=RuntimeError):
            self.assertIn("matrix", rag.retrieve_relevant_context("matrix"))

    @unittest.skipUnless(os.environ.get("EXAMAI_TEST_REAL_RAG") == "1", "requires local E5 model")
    def test_local_model_retrieves_multilingual_paraphrases(self):
        notes = {
            "biology": "Photosynthesis converts sunlight, carbon dioxide and water into glucose and oxygen.",
            "driving": "Гальмівний шлях автомобіля збільшується на слизькій дорозі через зменшення тертя.",
            "physics": "При нагревании металлы расширяются, потому что их частицы движутся активнее.",
            "math": "The derivative measures the instantaneous rate of change of a function.",
        }
        for source, passage in notes.items():
            self.db.replace_document(source, [passage])
        encoder = E5Encoder()
        self.assertEqual(build_cache(self.db, lambda: encoder), len(notes))
        rag = RAGEngine(self.db)
        self.addCleanup(rag.semantic._worker.shutdown)
        self.assertIsNotNone(rag.semantic._preparing.result(timeout=15))
        cases = {
            "Как растения получают сахар из света?": "biology",
            "Чому машина довше зупиняється на льоду?": "driving",
            "Why does a heated iron rod get longer?": "physics",
            "What is the slope of a curve at a single point?": "math",
        }
        for question, source in cases.items():
            with self.subTest(question=question):
                self.assertTrue(rag.retrieve_relevant_context(question, limit=1).startswith(f"[{source}]"))
        self.assertEqual(rag.retrieve_relevant_context("What is the capital of Japan?"), "")
