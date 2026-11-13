"""Bounded, best-effort semantic retrieval for the interactive answer worker."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from functools import lru_cache
import logging
import sqlite3
from threading import Lock
from time import perf_counter

import numpy as np

from semantic_index import E5Encoder, MODEL_DIR, MODEL_FILE, has_cache, load_snapshot


log = logging.getLogger("phantom.timing.rag")
MIN_SIMILARITY = 0.75


class SemanticSearch:
    def __init__(self, db, make_encoder=None):
        self._db = db
        self._lock = Lock()
        self._worker = None
        self._pending = None
        self._preparing = None
        if make_encoder is not None or self._can_load():
            self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-semantic")
            self._preparing = self._worker.submit(self._prepare, make_encoder or E5Encoder)

    def _can_load(self):
        if not (MODEL_DIR / MODEL_FILE).is_file():
            return False
        try:
            return has_cache(self._db)
        except sqlite3.Error as exc:
            log.info("semantic_cache_error=%s", type(exc).__name__)
            return False

    def _prepare(self, make_encoder):
        try:
            ids, vectors = load_snapshot(self._db)
            if vectors is None:
                return None
            encoder = make_encoder()
            encode_query = lru_cache(maxsize=128)(
                lambda question: encoder.encode([question], query=True)[0]
            )
            encode_query("warmup")
            return ids, vectors, encode_query
        except Exception as exc:
            log.info("semantic_init_error=%s", type(exc).__name__)
            return None

    def search(self, question, limit, deadline):
        if self._worker is None:
            return [], "disabled"
        with self._lock:
            if not self._preparing.done():
                return [], "warming"
            if self._pending is not None and not self._pending.done():
                return [], "busy"
            snapshot = self._preparing.result()
            if snapshot is None:
                return [], "unavailable"
            if perf_counter() >= deadline:
                return [], "deadline"
            job = self._worker.submit(self._search, snapshot, question, limit, deadline)
            self._pending = job
        try:
            return job.result(timeout=max(0, deadline - perf_counter())), "ready"
        except TimeoutError:
            return [], "deadline"
        except Exception as exc:
            log.info("semantic_query_error=%s", type(exc).__name__)
            return [], "error"

    def _search(self, snapshot, question, limit, deadline):
        ids, matrix, encode_query = snapshot
        scores = np.einsum("ij,j->i", matrix, encode_query(question))
        candidates = np.flatnonzero(scores >= MIN_SIMILARITY)
        ranked = candidates[np.argsort(-scores[candidates], kind="stable")[:limit]]
        ranked_ids = [ids[index] for index in ranked]
        remaining = deadline - perf_counter()
        if not ranked_ids or remaining <= 0:
            return []
        # Resolve IDs against the current DB so replaced/deleted chunks cannot be returned.
        placeholders = ",".join("?" for _ in ranked_ids)
        with self._db.connection(timeout=remaining) as conn:
            conn.set_progress_handler(lambda: int(perf_counter() >= deadline), 1000)
            rows = conn.execute(f"""
                SELECT id, source, content FROM documents WHERE id IN ({placeholders})
            """, ranked_ids)
            live = {doc_id: (doc_id, source, content) for doc_id, source, content in rows}
        return [live[doc_id] for doc_id in ranked_ids if doc_id in live]
