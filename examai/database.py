import sqlite3
from pathlib import Path
from contextlib import closing, contextmanager


from examai.paths import data_root


class DatabaseManager:
    def __init__(self, db_name=None):
        self.db_path = db_name if db_name is not None else data_root() / "exam_assistant.db"
        self.init_db()

    @contextmanager
    def connection(self, *, timeout=3):
        with closing(sqlite3.connect(self.db_path, timeout=timeout)) as conn, conn:
            conn.create_function("casefold", 1, lambda value: (value or "").casefold())
            yield conn

    def init_db(self):
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, source TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT, answer TEXT,
                    model TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    line_number INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    UNIQUE(source, line_number)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_meta (
                    source TEXT PRIMARY KEY,
                    line_count INTEGER NOT NULL,
                    is_code INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""CREATE TABLE IF NOT EXISTS document_origins (
                source TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS documents_source ON documents(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS document_lines_source ON document_lines(source, line_number)")
            existing = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='documents_fts'"
            ).fetchone()
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                    content, content='documents', content_rowid='id', tokenize='unicode61'
                )
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS documents_fts_insert AFTER INSERT ON documents
                BEGIN
                    INSERT INTO documents_fts(rowid, content) VALUES (new.id, new.content);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS documents_fts_delete AFTER DELETE ON documents
                BEGIN
                    INSERT INTO documents_fts(documents_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END
            """)
            if existing is None:
                conn.execute("INSERT INTO documents_fts(documents_fts) VALUES ('rebuild')")
            line_fts_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='document_lines_fts'"
            ).fetchone()
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS document_lines_fts USING fts5(
                    content, content='document_lines', content_rowid='id', tokenize='unicode61'
                )
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS document_lines_fts_insert AFTER INSERT ON document_lines
                BEGIN
                    INSERT INTO document_lines_fts(rowid, content) VALUES (new.id, new.content);
                END
            """)
            conn.execute("""
                CREATE TRIGGER IF NOT EXISTS document_lines_fts_delete AFTER DELETE ON document_lines
                BEGIN
                    INSERT INTO document_lines_fts(document_lines_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END
            """)
            if line_fts_exists is None:
                conn.execute("INSERT INTO document_lines_fts(document_lines_fts) VALUES ('rebuild')")

    def get_documents_list(self, *, timeout=3):
        with self.connection(timeout=timeout) as conn:
            rows = conn.execute("SELECT DISTINCT source FROM documents ORDER BY source")
            return [source for source, in rows if source]

    def delete_document_source(self, source_name):
        with self.connection() as conn:
            conn.execute("DELETE FROM documents WHERE source = ?", (source_name,))
            conn.execute("DELETE FROM document_lines WHERE source = ?", (source_name,))
            conn.execute("DELETE FROM document_meta WHERE source = ?", (source_name,))
            conn.execute("DELETE FROM document_origins WHERE source = ?", (source_name,))

    def named_document_text(self, source, budget):
        with self.connection(timeout=0.05) as conn:
            rows = conn.execute("""
                SELECT substr(content, 1, ?) FROM document_lines
                WHERE source = ? ORDER BY line_number LIMIT ?
            """, (budget + 1, source, budget + 1))
            pieces = []
            used = 0
            for (line,) in rows:
                pieces.append(line[:max(0, budget - used)])
                used += len(line) + 1
                if used > budget:
                    return "\n".join(pieces), True, True
            if pieces:
                return "\n".join(pieces), False, True
            rows = conn.execute("""
                SELECT substr(content, 1, ?) FROM documents WHERE source = ? ORDER BY id LIMIT 12
            """, (budget, source))
            text = "\n\n".join(content for content, in rows)
            return text[:budget], len(text) >= budget, False

    def replace_document(self, source_name, chunks, *, lines=None, is_code=False, origin=None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if origin is not None:
                known = conn.execute("SELECT source FROM document_origins WHERE path = ?", (origin,)).fetchone()
                if known:
                    source_name = known[0]
                else:
                    taken = conn.execute(
                        "SELECT 1 FROM document_origins WHERE casefold(source) = casefold(?)", (source_name,)
                    ).fetchone()
                    if taken:
                        project = Path(origin).parent.name or "project"
                        base = f"{project}/{source_name}"
                        source_name = base
                        n = 2
                        while conn.execute("SELECT 1 FROM documents WHERE casefold(source) = casefold(?)",
                                           (source_name,)).fetchone():
                            source_name = f"{project} ({n})/{Path(base).name}"
                            n += 1
                    conn.execute("INSERT INTO document_origins VALUES (?, ?)", (source_name, origin))
            conn.execute("DELETE FROM documents WHERE source = ?", (source_name,))
            conn.execute("DELETE FROM document_lines WHERE source = ?", (source_name,))
            conn.execute("DELETE FROM document_meta WHERE source = ?", (source_name,))
            conn.executemany(
                "INSERT INTO documents (content, source) VALUES (?, ?)",
                ((chunk, source_name) for chunk in chunks),
            )
            if lines is not None:
                conn.executemany(
                    "INSERT INTO document_lines (source, line_number, content) VALUES (?, ?, ?)",
                    ((source_name, number, content) for number, content in enumerate(lines, 1)),
                )
                conn.execute(
                    "INSERT INTO document_meta (source, line_count, is_code) VALUES (?, ?, ?)",
                    (source_name, len(lines), int(is_code)),
                )

    def get_line_sources(self):
        with self.connection() as conn:
            return conn.execute(
                "SELECT source, line_count, is_code FROM document_meta ORDER BY source"
            ).fetchall()

    def get_line_window(self, source, number, radius=5):
        with self.connection() as conn:
            rows = conn.execute("""
                SELECT line_number, content FROM document_lines
                WHERE source = ? AND line_number BETWEEN ? AND ?
                ORDER BY line_number
            """, (source, max(1, number - radius), number + radius)).fetchall()
        return rows

    def search_code_lines(self, expression, *, source=None, limit=4):
        with self.connection(timeout=0.05) as conn:
            return conn.execute("""
                SELECT d.source, d.line_number, d.content FROM document_lines_fts AS f
                JOIN document_lines AS d ON d.id = f.rowid
                WHERE document_lines_fts MATCH ? AND (? IS NULL OR d.source = ?)
                ORDER BY bm25(document_lines_fts), d.id LIMIT ?
            """, (expression, source, source, limit)).fetchall()

    def save_qa(self, question, answer, model):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO history (question, answer, model) VALUES (?, ?, ?)",
                (question, answer, model),
            )

    def get_history(self, limit=10):
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT question, answer FROM history ORDER BY id DESC LIMIT ?",
                (max(0, limit),),
            )
            return [{"question": question, "answer": answer} for question, answer in rows]
