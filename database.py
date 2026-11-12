import sqlite3
from contextlib import closing, contextmanager


class DatabaseManager:
    def __init__(self, db_name="exam_assistant.db"):
        self.db_path = db_name
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
            conn.execute("CREATE INDEX IF NOT EXISTS documents_source ON documents(source)")
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

    def get_documents_list(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT DISTINCT source FROM documents ORDER BY source")
            return [source for source, in rows if source]

    def delete_document_source(self, source_name):
        with self.connection() as conn:
            conn.execute("DELETE FROM documents WHERE source = ?", (source_name,))

    def replace_document(self, source_name, chunks):
        with self.connection() as conn:
            conn.execute("DELETE FROM documents WHERE source = ?", (source_name,))
            conn.executemany(
                "INSERT INTO documents (content, source) VALUES (?, ?)",
                ((chunk, source_name) for chunk in chunks),
            )

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