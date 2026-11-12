import logging
import re
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QThread, Signal

from semantic_search import SemanticSearch


WORD = re.compile(r"[^\W_]+", re.UNICODE)
CHUNK_SIZE = 750
OVERLAP = 120
SUMMARY_BUDGET = 14000
INTERACTIVE_SQLITE_BUDGET_SECONDS = 0.05
RETRIEVAL_BUDGET_SECONDS = 0.100
MAX_CONTEXT_CHARS = 3200
MAX_CANDIDATES = 16
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
STOP_WORDS = set(
    "що таке який яка яке які або для при про цей ця це як чим чому "
    "что такое какой какая какие или это как чем почему "
    "the and what how why does do is are of a an".split()
)
log = logging.getLogger("phantom.timing.rag")


class RAGEngine:
    def __init__(self, db_mgr, *, semantic=None):
        self.db_mgr = db_mgr
        self.semantic = semantic if semantic is not None else SemanticSearch(db_mgr)

    def index_file_sync(self, filepath):
        path = Path(filepath)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Файл перевищує 25 МіБ.")
        parts = []
        length = 0
        for piece in self._read_text(path):
            if piece:
                length += len(piece) + 1
                if length > MAX_TEXT_CHARS:
                    raise ValueError("Документ перевищує 2 млн символів.")
                parts.append(piece)
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError("Файл не містить доступного тексту.")
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + CHUNK_SIZE, len(text))
            if end < len(text):
                boundary = max(text.rfind(" ", start + CHUNK_SIZE // 2, end),
                               text.rfind("\n", start + CHUNK_SIZE // 2, end))
                if boundary > start:
                    end = boundary
            chunks.append(text[start:end].strip())
            if end == len(text):
                break
            start = end - OVERLAP
        chunks = [chunk for chunk in chunks if chunk]
        self.db_mgr.replace_document(path.name, chunks)
        return len(chunks)

    @staticmethod
    def _read_text(path):
        suffix = path.suffix.lower()
        if suffix == ".txt":
            yield path.read_text(encoding="utf-8-sig")
        elif suffix == ".pdf":
            from pypdf import PdfReader

            with path.open("rb") as file:
                reader = PdfReader(file)
                if reader.is_encrypted:
                    raise ValueError("PDF захищено паролем.")
                for page in reader.pages:
                    yield page.extract_text()
        elif suffix == ".docx":
            from docx import Document

            doc = Document(path)
            for paragraph in doc.paragraphs:
                yield paragraph.text
            for table in doc.tables:
                for row in table.rows:
                    yield " | ".join(cell.text for cell in row.cells)
        else:
            raise ValueError("Підтримуються TXT, PDF та DOCX.")

    def retrieve_relevant_context(self, query, *, limit=4):
        started = perf_counter()
        query = query[:4000].strip()
        words = dict.fromkeys(WORD.findall(query.casefold()))
        terms = [word for word in words if len(word) >= 2 and word not in STOP_WORDS][:24]
        if not terms or limit <= 0:
            return ""
        lexical = self._lexical_rows(terms)
        lexical_done = perf_counter()
        semantic, status = self.semantic.search(query, MAX_CANDIDATES, started + RETRIEVAL_BUDGET_SECONDS)

        # Reciprocal rank fusion avoids comparing raw BM25 and cosine scores.
        scores = {}
        rows = {}
        for ranking in (lexical, semantic):
            for rank, row in enumerate(ranking, start=1):
                doc_id = row[0]
                scores[doc_id] = scores.get(doc_id, 0) + 1 / (60 + rank)
                rows[doc_id] = row
        ordered = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
        context = "\n\n".join(f"[{rows[doc_id][1]}]\n{rows[doc_id][2]}" for doc_id in ordered)
        context = context[:min(MAX_CONTEXT_CHARS, limit * (CHUNK_SIZE + 50))]
        log.info("lexical_ms=%.1f semantic_ms=%.1f semantic_status=%s lexical_hits=%d "
                 "semantic_hits=%d context_chars=%d",
                 (lexical_done - started) * 1000, (perf_counter() - lexical_done) * 1000,
                 status, len(lexical), len(semantic), len(context))
        return context

    def _lexical_rows(self, terms):
        expression = " OR ".join(f'"{term}"*' for term in terms)
        deadline = perf_counter() + INTERACTIVE_SQLITE_BUDGET_SECONDS
        with self.db_mgr.connection(timeout=INTERACTIVE_SQLITE_BUDGET_SECONDS) as conn:
            conn.set_progress_handler(lambda: int(perf_counter() >= deadline), 1000)
            return conn.execute("""
                SELECT d.id, d.source, d.content FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ?
                ORDER BY bm25(documents_fts), d.id LIMIT ?
            """, (expression, MAX_CANDIDATES)).fetchall()

    def get_summary_context(self, source):
        with self.db_mgr.connection() as conn:
            conn.execute("BEGIN")
            count, length = conn.execute("""
                SELECT count(*), coalesce(sum(length(content)), 0)
                FROM documents WHERE source = ?
            """, (source,)).fetchone()
            if not count:
                return ""
            if length + count * 2 <= SUMMARY_BUDGET:
                rows = conn.execute(
                    "SELECT content FROM documents WHERE source = ? ORDER BY id", (source,)
                )
                return "\n\n".join(content for content, in rows)
            slots = min(count, (SUMMARY_BUDGET - 100) // (CHUNK_SIZE + 2))
            selected = {round(i * (count - 1) / (slots - 1)) for i in range(slots)} if slots > 1 else {0}
            rows = conn.execute(
                "SELECT content FROM documents WHERE source = ? ORDER BY id", (source,)
            )
            excerpt = "\n\n".join(
                content for index, (content,) in enumerate(rows) if index in selected
            )
        return "Вибіркові фрагменти з різних частин документа.\n\n" + excerpt[:SUMMARY_BUDGET - 70]

    def search_keyword(self, keyword):
        with self.db_mgr.connection() as conn:
            rows = conn.execute("""
                SELECT source, content FROM documents
                WHERE instr(casefold(content), ?) > 0 ORDER BY id LIMIT 3
            """, (keyword.casefold(),)).fetchall()
        if not rows:
            return f"Термін «{keyword}» у конспектах не знайдено."
        return "\n\n".join(f"[{source}] {content}" for source, content in rows)


class FileIndexerThread(QThread):
    result = Signal(str, int, str)

    def __init__(self, rag_engine, filepath):
        super().__init__()
        self.rag_engine = rag_engine
        self.filepath = filepath

    def run(self):
        filename = Path(self.filepath).name
        try:
            count = self.rag_engine.index_file_sync(self.filepath)
        except Exception:
            self.result.emit(filename, 0, "Перевірте формат, розмір та наявність тексту.")
        else:
            self.result.emit(filename, count, "")

