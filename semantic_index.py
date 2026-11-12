"""Local E5 encoding and persistent passage vectors shared by indexing and search."""

import hashlib
from pathlib import Path

import numpy as np


MODEL_DIR = Path(__file__).resolve().parent / "models" / "semantic-e5-small"
MODEL_FILE = "model_quantized.onnx"
MODEL_ID = "xenova-e5-small:761b726dd34fb83930e26aab4e9ac3899aa1fa78:quantized:512"
DIMENSIONS = 384
MAX_SEARCH_CHUNKS = 50_000
BATCH_SIZE = 16


def content_key(text):
    return hashlib.sha256(text.encode("utf-8")).digest()


class E5Encoder:
    def __init__(self, model_dir=MODEL_DIR):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_dir / MODEL_FILE), sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}

    def encode(self, texts, *, query=False):
        prefix = "query: " if query else "passage: "
        tokens = self.tokenizer.encode_batch([prefix + text for text in texts])
        mask = np.asarray([token.attention_mask for token in tokens], dtype=np.int64)
        inputs = {
            "input_ids": np.asarray([token.ids for token in tokens], dtype=np.int64),
            "attention_mask": mask,
            "token_type_ids": np.zeros_like(mask),
        }
        hidden, = self.session.run(
            ["last_hidden_state"], {name: inputs[name] for name in self.input_names},
        )
        pooled = (hidden * mask[..., None].astype(np.float32)).sum(axis=1)
        return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)


def build_cache(db, make_encoder=E5Encoder):
    """Encode missing unique passages in batches without holding a write lock."""
    with db.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rag_embedding_cache (
                model TEXT NOT NULL, digest BLOB NOT NULL, vector BLOB NOT NULL,
                PRIMARY KEY (model, digest)
            ) WITHOUT ROWID
        """)
    encoder = None
    after_id = count = 0
    while True:
        with db.connection() as conn:
            rows = conn.execute("""
                SELECT id, content FROM documents
                WHERE id > ? AND content IS NOT NULL ORDER BY id LIMIT ?
            """, (after_id, BATCH_SIZE)).fetchall()
            if not rows:
                return count
            after_id = rows[-1][0]
            passages = {content_key(content): content for _, content in rows}
            placeholders = ",".join("?" for _ in passages)
            cached = conn.execute(f"""
                SELECT digest FROM rag_embedding_cache
                WHERE model = ? AND digest IN ({placeholders})
                  AND length(vector) = ?
            """, (MODEL_ID, *passages, DIMENSIONS * 4))
            for digest, in cached:
                del passages[digest]
        if not passages:
            continue
        if encoder is None:
            encoder = make_encoder()
        vectors = np.asarray(encoder.encode(list(passages.values())), dtype="<f4")
        if vectors.shape != (len(passages), DIMENSIONS) or not np.isfinite(vectors).all():
            raise ValueError("Invalid passage embeddings")
        with db.connection() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO rag_embedding_cache (model, digest, vector)
                VALUES (?, ?, ?)
            """, ((MODEL_ID, digest, vector.tobytes())
                  for digest, vector in zip(passages, vectors)))
        count += len(passages)


def has_cache(db):
    with db.connection(timeout=0) as conn:
        return conn.execute("""
            SELECT 1 FROM sqlite_master WHERE name = 'rag_embedding_cache'
        """).fetchone() is not None


def load_snapshot(db):
    with db.connection() as conn:
        conn.create_function("rag_digest", 1, content_key, deterministic=True)
        rows = conn.execute("""
            SELECT d.id, e.vector FROM documents d
            JOIN rag_embedding_cache e
              ON e.model = ? AND e.digest = rag_digest(coalesce(d.content, ''))
            WHERE length(e.vector) = ?
            ORDER BY d.id DESC LIMIT ?
        """, (MODEL_ID, DIMENSIONS * 4, MAX_SEARCH_CHUNKS)).fetchall()
    if not rows:
        return (), None
    ids, vectors = zip(*rows)
    return ids, np.frombuffer(b"".join(vectors), dtype="<f4").reshape(-1, DIMENSIONS)
