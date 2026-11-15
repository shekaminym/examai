import logging
import os
from packaging.requirements import InvalidRequirement, Requirement
import re
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QThread, Signal

from examai.ai.document_profile import analyze_document, terms as topic_terms, topic_score
from examai.ai.semantic_search import SemanticSearch


WORD = re.compile(r"[^\W_]+", re.UNICODE)
CHUNK_SIZE = 750
OVERLAP = 120
SUMMARY_BUDGET = 14000
INTERACTIVE_SQLITE_BUDGET_SECONDS = 0.05
RETRIEVAL_BUDGET_SECONDS = 0.100
MAX_CONTEXT_CHARS = 3200
MAX_CANDIDATES = 16
DOCUMENT_REFERENCE = re.compile(r"\b(?:конспект\w*|лекц\w*|файл\w*|код\w*|документ\w*|pdf|notes?|file|code)\b", re.I)
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
STOP_WORDS = set(
    "що таке який яка яке які або для при про цей ця це як чим чому "
    "что такое какой какая какие или это как чем почему "
    "the and what how why does do is are of a an".split()
)
log = logging.getLogger("phantom.timing.rag")
LINE_WORD = r"(?:ряд(?:ок|к\w*|ц\w*)|стр(?:ок|оч|іч)\w*|lines?)\b"
RANGE_END = r"(?:\s*(?:[-–—]|to|до)\s*(\d{1,6})(?!\d))?"
LINE_AFTER_NUMBER = re.compile(
    r"(?<!\w)(\d{1,6})(?:[-‐‑–—]?(?:ий|ій|ма|му|го|та|th|st|nd|rd|й|м|а|я|ю))?"
    + RANGE_END + r"\s*" + LINE_WORD, re.IGNORECASE,
)
LINE_BEFORE_NUMBER = re.compile(
    LINE_WORD + r"\s*(?:№|#|номер\s*)?(\d{1,6})(?!\d)" + RANGE_END,
    re.IGNORECASE,
)
ORDINAL_LINES = (
    (1, r"перш\w*|перв\w*|first"),
    (2, r"друг\w*|втор\w*|second"),
    (3, r"трет\w*|third"),
    (4, r"четверт\w*|fourth"),
    (5, r"п['’]?ят\w*|пят\w*|fifth"),
    (6, r"шост\w*|шест\w*|sixth"),
    (7, r"сьом\w*|седьм\w*|seventh"),
    (8, r"восьм\w*|eighth"),
    (9, r"дев['’]?ят\w*|девят\w*|ninth"),
    (10, r"десят\w*|tenth"),
    (100, r"сот\w*|hundredth"),
)
ORDINAL_LINE_PATTERNS = tuple(
    (number, re.compile(
        r"(?<!\w)(?:" + ordinal + r")\b\s+" + LINE_WORD +
        r"|" + LINE_WORD + r"\s+(?:" + ordinal + r")\b", re.IGNORECASE,
    )) for number, ordinal in ORDINAL_LINES
)
FUNCTION_NAME = re.compile(r"(?:функц\w*|function)\s+([A-Za-z_][A-Za-z_0-9]*)", re.IGNORECASE)
FILE_REFERENCE = re.compile(r"(?<![\w.])[\w.-]+\.(?:txt|pdf|docx)(?![\w.-])")
IDENTIFIER = re.compile(r"(?<![A-Za-z_0-9])([A-Za-z_][A-Za-z_0-9]*[A-Z][A-Za-z_0-9]*)(?![A-Za-z_0-9])")


class ReferenceLookupError(LookupError):
    """An exact source reference cannot be resolved safely."""


def requested_lines(question):
    for pattern in (LINE_BEFORE_NUMBER, LINE_AFTER_NUMBER):
        match = pattern.search(question)
        if match:
            first = int(match[1])
            return first, int(match[2]) if match[2] else first
    for number, pattern in ORDINAL_LINE_PATTERNS:
        if pattern.search(question):
            return number, number
    return None


def requested_line(question):
    selection = requested_lines(question)
    return selection[0] if selection else None


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


def describe_requirements(text, truncated=False):
    """Classify pip syntax as document data. Never open includes or run pip."""
    text = re.sub(r"\\[ \t]*\n", " ", text)
    lines = text.splitlines()
    if truncated:
        lines = lines[:-1]
    result = []
    for raw in lines:
        line = re.split(r"(?:^|\s+)#", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        if re.match(r"^(?:-r(?=\s|[^-])|--requirement(?:=|\s))", line):
            kind = "Вкладений список (не прочитано)"
        elif re.match(r"^(?:-c(?=\s|[^-])|--constraint(?:=|\s))", line):
            kind = "Обмеження (не прочитано)"
        else:
            try:
                Requirement(line)
            except InvalidRequirement:
                kind = "Інший запис"
            else:
                kind = "Залежність"
        result.append(f"{kind}: {line}")
    if truncated:
        result.append("[Список скорочено]")
    return "\n".join(result)


class RAGEngine:
    def __init__(self, db_mgr, *, semantic=None):
        self.db_mgr = db_mgr
        self.semantic = semantic if semantic is not None else SemanticSearch(db_mgr)

    def index_file_sync(self, filepath, *, return_source=False):
        path = Path(filepath)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Файл перевищує 25 МіБ.")
        parts = []
        length = 0
        is_pdf = path.suffix.lower() == ".pdf"
        page_count = 0
        for page_count, piece in enumerate(self._read_text(path), 1):
            if piece is not None:
                if is_pdf and piece.strip():
                    piece = f"[Сторінка {page_count}]\n{piece}"
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
            lines = text.removesuffix("\n").split("\n")
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
        source = self.db_mgr.replace_document(
            path.name, chunks, lines=lines,
            is_code=looks_like_code(text) if lines is not None else False,
            origin=os.path.normcase(str(path.resolve())),
            profile=analyze_document(path.name, text, pages=page_count if is_pdf else 0),
        )
        return (len(chunks), source) if return_source else len(chunks)

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
        names = FILE_REFERENCE.findall(question.casefold())
        if names:
            explicit = [entry for entry in sources if Path(entry[0]).name.casefold() in names]
            if not explicit:
                raise ReferenceLookupError(f"Рядки файлу «{names[0]}» недоступні. Перевірте імпорт.")
            qualified = [entry for entry in explicit if "/" in entry[0] and entry[0].casefold() in question.casefold()]
            if len(qualified) == 1:
                return qualified[0]
            if len(explicit) == 1:
                return explicit[0]
            selected = next((entry for entry in explicit if entry[0] == source_hint), None)
            if selected:
                return selected
            raise ReferenceLookupError("Є кілька файлів з цією назвою. Виберіть потрібний у «Конспектах».")
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
        code_sources = [entry for entry in sources if entry[2]]
        hinted = next((entry for entry in sources if entry[0] == source_hint), None)
        code_requested = any(word in lowered for word in ("код", "програм", "лаборатор", "code"))
        if code_requested and len(code_sources) == 1 and (hinted is None or not hinted[2]):
            return code_sources[0]
        if hinted:
            return hinted
        if len(sources) == 1:
            return sources[0]
        raise ReferenceLookupError("Уточніть назву TXT-файлу або виберіть його в «Конспектах».")

    def _exact_line_context(self, query, source_hint, sources, request_id):
        selection = requested_lines(query)
        if selection is None:
            return None
        number, last = selection
        source, count, _ = self._resolve_line_source(query, source_hint, sources)
        log.info("request_id=%s rag_document_resolved=%s line=%d", request_id, source, number)
        if last < number or last - number >= 24:
            raise ReferenceLookupError("Вкажіть до 24 рядків у порядку зростання.")
        if last != number and not (1 <= number <= last <= count):
            raise ReferenceLookupError(f"Діапазон {number}–{last} виходить за межі файлу «{source}» ({count} рядків).")
        if number < 1 or number > count:
            raise ReferenceLookupError(f"У файлі «{source}» немає рядка {number}; у ньому {count} рядків.")
        started = perf_counter()
        rows = (self.db_mgr.get_line_window(source, number) if last == number
                else self.db_mgr.get_lines(source, number, last))
        log.info("request_id=%s rag_exact_line_ms=%.1f source=%s line=%d",
                 request_id, (perf_counter() - started) * 1000, source, number)
        if sum(number <= line <= last for line, _ in rows) != last - number + 1:
            raise ReferenceLookupError(f"Рядок {number} у файлі «{source}» недоступний.")
        def display(line, content):
            maximum = (1100 if line == number else 180) if number == last else min(1100, 2600 // (last - number + 1))
            if len(content) > maximum:
                content = content[:maximum] + "… [довгий рядок скорочено]"
            return f"{line}: {content}"
        shown = "\n".join(display(line, content) for line, content in rows)
        target = f"цільовий рядок {number}" if number == last else f"цільові рядки {number}–{last}"
        return f"Точні рядки з файлу «{source}» ({target}):\n{shown}"

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

    def _named_context(self, query, source_hint, request_id):
        # Full names take precedence over BM25/semantic search, including in FAST.
        matches = FILE_REFERENCE.findall(query.casefold())
        matches = list(dict.fromkeys(matches))
        if not matches:
            return None
        sources = self.db_mgr.get_documents_list(timeout=INTERACTIVE_SQLITE_BUDGET_SECONDS)
        contexts = []
        budget = max(256, (MAX_CONTEXT_CHARS - 200) // len(matches))
        for name in matches:
            candidates = [source for source in sources if Path(source).name.casefold() == name]
            selected = next((source for source in candidates if "/" in source and
                             source.casefold() in query.casefold()), None)
            if not candidates:
                raise ReferenceLookupError(f"Файл «{name}» не імпортовано.")
            if not selected:
                if len(candidates) == 1:
                    selected = candidates[0]
                elif source_hint in candidates:
                    selected = source_hint
                else:
                    raise ReferenceLookupError(f"Є кілька файлів «{name}». Виберіть потрібний у «Конспектах».")
            topical = topic_terms(re.sub(re.escape(name), "", query, flags=re.I))[:12]
            relevant = ([row[2] for row in self._lexical_rows(topical, source=selected)[:2]]
                        if topical and name != "requirements.txt" else [])
            if relevant:
                text = "\n\n".join(relevant)[:budget]
                truncated, exact = False, False
            else:
                text, truncated, exact = self.db_mgr.named_document_text(selected, budget)
            log.info("request_id=%s rag_document_resolved=%s exact_lines=%s truncated=%s",
                     request_id, selected, exact, truncated)
            if not text.strip():
                raise ReferenceLookupError(f"Текст файлу «{selected}» недоступний. Імпортуйте його повторно.")
            if name == "requirements.txt" and exact:
                text = describe_requirements(text, truncated)
            qualifier = " (фрагмент)" if truncated or not exact else ""
            contexts.append(f"Файл «{selected}»{qualifier} — довідкові дані:\n{text[:budget]}")
        return "\n\n".join(contexts)[:MAX_CONTEXT_CHARS]

    def retrieve_relevant_context(self, query, *, limit=4, source_hint=None, request_id="-"):
        started = perf_counter()
        query = query[:4000].strip()
        sources = self.db_mgr.get_line_sources() if requested_line(query) is not None else None
        if sources is not None:
            return self._exact_line_context(query, source_hint, sources, request_id)
        named_context = self._named_context(query, source_hint, request_id)
        if named_context is not None:
            return named_context
        code_context = self._code_context(query, source_hint, request_id)
        terms = list(dict.fromkeys(topic_terms(query)))[:24]
        if limit <= 0:
            return code_context
        lexical = self._lexical_rows(terms) if terms else []
        lexical_done = perf_counter()
        semantic, status = self.semantic.search(query, MAX_CANDIDATES, started + RETRIEVAL_BUDGET_SECONDS) if terms else ([], "not_needed")

        profiles = self.db_mgr.get_document_profiles(timeout=INTERACTIVE_SQLITE_BUDGET_SECONDS)
        scores = {source: topic_score(query, profile) for source, profile in profiles.items()}
        for source in {row[1] for row in lexical}:
            scores[source] = scores.get(source, 0) + 3
        for source in {row[1] for row in semantic}:
            scores[source] = scores.get(source, 0) + 2
        fusion_scores = {}
        fusion_rows = {}
        for ranking in (lexical, semantic):
            for rank, row in enumerate(ranking, start=1):
                fusion_scores[row[0]] = fusion_scores.get(row[0], 0) + 1 / (60 + rank)
                fusion_rows[row[0]] = row
        best_fusion = {}
        for doc_id, score in fusion_scores.items():
            source = fusion_rows[doc_id][1]
            best_fusion[source] = max(score, best_fusion.get(source, 0))
        if source_hint in profiles:
            scores[source_hint] += 1
        mentioned = bool(DOCUMENT_REFERENCE.search(query))
        if not terms and mentioned and source_hint not in profiles:
            if len(profiles) == 1:
                source_hint = next(iter(profiles))
            elif len(profiles) > 1:
                raise ReferenceLookupError("Виберіть потрібний файл у «Конспектах».")
        selected = max(scores, key=lambda source: (scores[source], best_fusion.get(source, 0))) if scores else None
        if selected and scores[selected] <= 1 and not mentioned:
            selected = None
        if selected and scores[selected] <= 1 and mentioned and source_hint in profiles:
            selected = source_hint

        rows = []
        if selected:
            ranked_ids = sorted((doc_id for doc_id, row in fusion_rows.items()
                                 if row[1] == selected), key=fusion_scores.__getitem__, reverse=True)
            rows = [fusion_rows[doc_id] for doc_id in ranked_ids[:limit]]
        if not rows and selected and mentioned:
            preview = profiles[selected]["preview"].strip()
            if preview:
                rows = [(0, selected, preview)]
        context = "\n\n".join(f"[{source}]\n{content}"
                               for _, source, content in rows)
        if code_context and (not selected or f"Файл «{selected}»" in code_context):
            context = code_context + ("\n\n" + context if context else "")
        context = context[:min(MAX_CONTEXT_CHARS, limit * (CHUNK_SIZE + 50))]
        log.info("request_id=%s rag_source=%s source_score=%d lexical_ms=%.1f semantic_ms=%.1f "
                 "semantic_status=%s lexical_hits=%d semantic_hits=%d context_chars=%d",
                 request_id, selected, scores.get(selected, 0),
                 (lexical_done - started) * 1000, (perf_counter() - lexical_done) * 1000,
                 status, len(lexical), len(semantic), len(context))
        return context

    def _lexical_rows(self, terms, *, source=None):
        if not terms:
            return []
        expression = " OR ".join(f'"{term}"*' for term in terms)
        deadline = perf_counter() + INTERACTIVE_SQLITE_BUDGET_SECONDS
        with self.db_mgr.connection(timeout=INTERACTIVE_SQLITE_BUDGET_SECONDS) as conn:
            conn.set_progress_handler(lambda: int(perf_counter() >= deadline), 1000)
            return conn.execute("""
                SELECT d.id, d.source, d.content FROM documents_fts
                JOIN documents d ON d.id = documents_fts.rowid
                WHERE documents_fts MATCH ? AND (? IS NULL OR d.source = ?)
                ORDER BY bm25(documents_fts), d.id LIMIT ?
            """, (expression, source, source, MAX_CANDIDATES)).fetchall()

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
            count, filename = self.rag_engine.index_file_sync(self.filepath, return_source=True)
        except Exception:
            self.result.emit(filename, 0, "Перевірте формат, розмір та наявність тексту.")
        else:
            self.result.emit(filename, count, "")

