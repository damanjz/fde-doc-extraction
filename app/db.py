"""SQLite storage (stdlib): documents, extractions, review decisions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,            -- pdf | png | jpeg
  doc_type TEXT,                 -- invoice | delivery_note
  source TEXT,                   -- pdf (text layer) | scan (OCR)
  pages INTEGER,
  status TEXT NOT NULL,          -- auto_accepted | extracted (needs a person) | reviewed | error
  error TEXT,
  text TEXT,
  file_path TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extractions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  raw_reply TEXT,
  record_json TEXT NOT NULL,
  fields_json TEXT NOT NULL,
  flags_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  timings_json TEXT NOT NULL,
  model TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL REFERENCES documents(id),
  field TEXT NOT NULL,
  extracted_value TEXT,
  final_value TEXT,
  decision TEXT NOT NULL,        -- accepted | corrected
  confidence TEXT,
  reasons TEXT,
  reviewed_at TEXT NOT NULL,
  UNIQUE (document_id, field)
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Db:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # --- writes ---------------------------------------------------------------------------------

    def add_document(
        self,
        *,
        filename: str,
        sha256: str,
        kind: str,
        doc_type: str | None,
        source: str | None,
        pages: int | None,
        status: str,
        error: str | None = None,
        text: str | None = None,
        file_path: str | None = None,
    ) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO documents (filename, sha256, kind, doc_type, source, pages, status, error, text, file_path,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (filename, sha256, kind, doc_type, source, pages, status, error, text, file_path, now()),
            )
            return int(cur.lastrowid)

    def add_extraction(
        self,
        document_id: int,
        *,
        raw_reply: str,
        record: dict,
        fields: dict,
        flags: list[dict],
        warnings: list[str],
        timings: dict,
        model: str,
    ) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO extractions (document_id, raw_reply, record_json, fields_json, flags_json, warnings_json,"
                " timings_json, model, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    raw_reply,
                    json.dumps(record),
                    json.dumps(fields),
                    json.dumps(flags),
                    json.dumps(warnings),
                    json.dumps(timings),
                    model,
                    now(),
                ),
            )

    def set_status(self, document_id: int, status: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE documents SET status=? WHERE id=?", (status, document_id))

    def save_reviews(self, document_id: int, rows: list[dict]) -> None:
        with self.conn() as c:
            for r in rows:
                c.execute(
                    "INSERT INTO reviews (document_id, field, extracted_value, final_value, decision, confidence,"
                    " reasons, reviewed_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(document_id, field) DO UPDATE SET"
                    " final_value=excluded.final_value, decision=excluded.decision, reviewed_at=excluded.reviewed_at",
                    (
                        document_id,
                        r["field"],
                        r["extracted"],
                        r["final"],
                        r["decision"],
                        r["confidence"],
                        r["reasons"],
                        now(),
                    ),
                )

    # --- reads ----------------------------------------------------------------------------------

    def get_document(self, document_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return dict(row) if row else None

    def list_documents(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT d.id, d.filename, d.doc_type, d.source, d.status, d.created_at, "
                "(SELECT COUNT(*) FROM reviews r WHERE r.document_id = d.id) AS reviewed_fields "
                "FROM documents d ORDER BY d.id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_extraction(self, document_id: int) -> dict | None:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM extractions WHERE document_id=? ORDER BY id DESC LIMIT 1", (document_id,)
            ).fetchone()
        if not row:
            return None
        out = dict(row)
        for k in ("record", "fields", "flags", "warnings", "timings"):
            out[k] = json.loads(out.pop(f"{k}_json"))
        return out

    def prior_records(self, doc_type: str) -> list[dict]:
        """Extracted records already stored for this document type (for the duplicate check)."""
        with self.conn() as c:
            rows = c.execute(
                "SELECT e.record_json FROM extractions e JOIN documents d ON d.id = e.document_id "
                "WHERE d.doc_type = ? AND d.status != 'error'",
                (doc_type,),
            ).fetchall()
        return [json.loads(r["record_json"]) for r in rows]

    def get_reviews(self, document_id: int) -> dict[str, dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM reviews WHERE document_id=?", (document_id,)).fetchall()
        return {r["field"]: dict(r) for r in rows}

    def all_reviews(self) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT r.*, d.filename, d.doc_type FROM reviews r JOIN documents d ON d.id = r.document_id "
                "ORDER BY r.document_id, r.id"
            ).fetchall()
        return [dict(r) for r in rows]
