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
LINE_AFTER_NUMBER = re.compile(
    r"(?<!\w)(\d{1,6})(?:[-‐‑–]?(?:й|ий|ій|му|го|та|th|st|nd|rd))?\s*"
    r"(?:ряд(?:ок|к\w*|ц\w*)|строк\w*|line\b)", re.IGNORECASE,
)
LINE_BEFORE_NUMBER = re.compile(
    r"(?:ряд(?:ок|к\w*|ц\w*)|строк\w*|line\b)\s*(?:№|#|номер\s*)?(\d{1,6})",
    re.IGNORECASE,
)
FUNCTION_NAME = re.compile(r"(?:функц\w*|function)\s+([A-Za-z_][A-Za-z_0-9]*)", re.IGNORECASE)
IDENTIFIER = re.compile(r"(?<![A-Za-z_0-9])([A-Za-z_][A-Za-z_0-9]*[A-Z][A-Za-z_0-9]*)(?![A-Za-z_0-9])")


class ReferenceLookupError(LookupError):
    """An exact source reference cannot be resolved safely."""


def requested_line(question):
    for pattern in (LINE_AFTER_NUMBER, LINE_BEFORE_NUMBER):
        match = pattern.search(question)
        if match:
            return int(match.group(1))
    return None


def looks_like_code(text):
    sample = text[:12000]
    return bool(re.search(r"^\s*#\s*include\b", sample, re.MULTILINE)) or sum((
        len(re.findall(r"[;{}]", sample)) >= 5,
        bool(re.search(r"\b(?:int|void|double|float|char)\s+\w+\s*\(", sample)),
        bool(re.search(r"\b(?:if|for|while)\s*\(", sample)),
    )) >= 2


def code_terms(question):
    terms = FUNCTION_NAME.findall(question) + IDENTIFIER.findall(question)
    lowered = question.casefold()
    if any(word in lowered for word in ("пам'ят", "пам’ят", "памят", "памяти", "memory", "виділя", "выделя")):
        terms.extend(("malloc", "calloc", "realloc"))
    if any(word in lowered for word in ("цикл", "loop", "масив", "массив", "array")):
        terms.extend(("for", "while", "do"))
    return list(dict.fromkeys(term for term in terms if len(term) >= 2))[:8]


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
            if piece is not None:
                length += len(piece) + 1
                if length > MAX_TEXT_CHARS:
                    raise ValueError("Документ перевищує 2 млн символів.")
                parts.append(piece)
        text = "\n".join(parts)
        if not text.strip():
            raise ValueError("Файл не містить доступного тексту.")
        lines = None
        if path.suffix.lower() == ".txt":
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            lines = text.split("\n")
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
        self.db_mgr.replace_document(path.name, chunks, lines=lines,
                                     is_code=looks_like_code(text) if lines is not None else False)
        return len(chunks)

    @staticmethod
    def _read_text(path):
        suffix = path.suffix.lower()
        if suffix == ".txt":
            try:
                yield path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                yield path.read_text(encoding="cp1251")
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

    def _resolve_line_source(self, question, source_hint, sources):
        if not sources:
            raise ReferenceLookupError("Немає TXT-файлу з доступними номерами рядків. Імпортуйте його повторно.")
        lowered = question.casefold()
        named = []
        for source, count, is_code in sources:
            name = Path(source).stem.casefold()
            full_name = source.casefold()
            if (re.search(r"(?<!\w)" + re.escape(full_name) + r"(?!\w)", lowered)
                    or (len(name) >= 3 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lowered))):
                named.append((source, count, is_code))
        if len(named) == 1:
            return named[0]
        if len(named) > 1:
            raise ReferenceLookupError("Уточніть назву файлу: запит відповідає кільком документам.")
        for entry in sources:
            if entry[0] == source_hint:
                return entry
        code_sources = [entry for entry in sources if entry[2]]
        if len(code_sources) == 1 and any(word in lowered for word in ("код", "програм", "лаборатор", "code")):
            return code_sources[0]
        if len(sources) == 1:
            return sources[0]
        raise ReferenceLookupError("Уточніть назву TXT-файлу або виберіть його в «Конспектах».")

    def _exact_line_context(self, query, source_hint, sources, request_id):
        number = requested_line(query)
        if number is None:
            return None
        source, count, _ = self._resolve_line_source(query, source_hint, sources)
        log.info("request_id=%s rag_document_resolved=%s line=%d", request_id, source, number)
        if number < 1 or number > count:
            raise ReferenceLookupError(f"У файлі «{source}» немає рядка {number}; у ньому {count} рядків.")
        started = perf_counter()
        rows = self.db_mgr.get_line_window(source, number)
        log.info("request_id=%s rag_exact_line_ms=%.1f source=%s line=%d",
                 request_id, (perf_counter() - started) * 1000, source, number)
        if not any(line == number for line, _ in rows):
            raise ReferenceLookupError(f"Рядок {number} у файлі «{source}» недоступний.")
        def display(line, content):
            maximum = 1100 if line == number else 180
            if len(content) > maximum:
                content = content[:maximum] + "… [довгий рядок скорочено]"
            return f"{line}: {content}"
        shown = "\n".join(display(line, content) for line, content in rows)
        return f"Точні рядки з файлу «{source}» (цільовий рядок {number}):\n{shown}"

    def _code_context(self, query, source_hint, request_id):
        terms = code_terms(query)
        if not terms:
            return ""
        expression = " OR ".join('"' + term + '"' for term in terms)
        started = perf_counter()
        rows = self.db_mgr.search_code_lines(expression, source=source_hint)
        if not rows and source_hint is not None:
            rows = self.db_mgr.search_code_lines(expression)
        if not rows:
            return ""
        log.info("request_id=%s rag_code_lookup_ms=%.1f hits=%d",
                 request_id, (perf_counter() - started) * 1000, len(rows))
        excerpts = []
        for source, line, _ in rows[:3]:
            window = self.db_mgr.get_line_window(source, line, radius=2)
            excerpts.append(f"Файл «{source}», рядок {line}:\n" +
                            "\n".join(f"{number}: {content}" for number, content in window))
        return "\n\n".join(excerpts)[:1800]

    def get_source_preview(self, source, *, max_chars=2200):
        """Small, optional code reference for a selected file in an image request."""
        if not source:
            return ""
        with self.db_mgr.connection(timeout=0.05) as conn:
            metadata = conn.execute(
                "SELECT is_code FROM document_meta WHERE source = ?", (source,)
            ).fetchone()
            if not metadata or not metadata[0]:
                return ""
            rows = conn.execute("""
                SELECT line_number, content FROM document_lines
                WHERE source = ? ORDER BY line_number LIMIT 80
            """, (source,)).fetchall()
        preview = "\n".join(f"{number}: {content}" for number, content in rows)
        return f"Файл «{source}» (початок коду):\n{preview[:max_chars]}"

    def retrieve_relevant_context(self, query, *, limit=4, source_hint=None, request_id="-"):
        started = perf_counter()
        query = query[:4000].strip()
        sources = self.db_mgr.get_line_sources() if requested_line(query) is not None else None
        if sources is not None:
            return self._exact_line_context(query, source_hint, sources, request_id)
        code_context = self._code_context(query, source_hint, request_id)
        words = dict.fromkeys(WORD.findall(query.casefold()))
        terms = [word for word in words if len(word) >= 2 and word not in STOP_WORDS][:24]
        if not terms or limit <= 0:
            return code_context
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
        if code_context:
            context = code_context + ("\n\n" + context if context else "")
        context = context[:min(MAX_CONTEXT_CHARS, limit * (CHUNK_SIZE + 50))]
        log.info("request_id=%s lexical_ms=%.1f semantic_ms=%.1f semantic_status=%s lexical_hits=%d "
                 "semantic_hits=%d context_chars=%d",
                 request_id, (lexical_done - started) * 1000, (perf_counter() - lexical_done) * 1000,
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

