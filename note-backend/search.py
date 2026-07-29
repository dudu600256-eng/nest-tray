"""
Note Tray — Full-text search via SQLite FTS5 (trigram tokenizer).
==================================================================
Manages:
  - ``notes_fts`` (FTS5 virtual table for trigram search)
  - ``doc_meta`` (path → note_id + mtime mapping)

All accesses are serialised through a single ``threading.Lock``.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger("sidecar.search")

FTS5_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    doc_path UNINDEXED,
    title,
    content,
    ocr_text,
    tokenize='trigram'
)
"""

DOC_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_meta (
    doc_path TEXT PRIMARY KEY,
    note_id TEXT UNIQUE,
    file_mtime REAL,
    word_count INTEGER DEFAULT 0
)
"""

MERGE_INTERVAL = 1000  # call merge() every N writes


class SearchIndex:
    """Single SQLite FTS5 index with WAL mode + serialised access."""

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(FTS5_SCHEMA)
        self._conn.execute(DOC_META_SCHEMA)
        self._lock = threading.Lock()
        self._write_count = 0
        self.ready = True
        logger.info("SearchIndex ready: %s", self._path)

    def close(self):
        self.ready = False
        try:
            self._conn.close()
        except Exception:
            pass

    def _sanitize(self, text: str) -> str:
        """Remove surrogate characters that would crash UTF-8 encoding."""
        return text.encode("utf-8", errors="replace").decode("utf-8")

    # ── Upsert ─────────────────────────────────────────────────────────────

    def upsert(self, doc_path: str, title: str, content: str, ocr_text: str = "", note_id: str = "", mtime: float = 0.0):
        title = self._sanitize(title)
        content = self._sanitize(content)
        ocr_text = self._sanitize(ocr_text)
        with self._lock:
            self._conn.execute(
                "DELETE FROM notes_fts WHERE doc_path = ?", (doc_path,)
            )
            self._conn.execute(
                "INSERT INTO notes_fts (doc_path, title, content, ocr_text) VALUES (?, ?, ?, ?)",
                (doc_path, title, content, ocr_text),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO doc_meta (doc_path, note_id, file_mtime, word_count) VALUES (?, ?, ?, ?)",
                (doc_path, note_id, mtime, len(content.split())),
            )
            self._conn.commit()
            self._maybe_merge_locked()

    # ── Delete ─────────────────────────────────────────────────────────────

    def delete(self, doc_path: str):
        with self._lock:
            self._conn.execute("DELETE FROM notes_fts WHERE doc_path = ?", (doc_path,))
            self._conn.execute("DELETE FROM doc_meta WHERE doc_path = ?", (doc_path,))
            self._conn.commit()
            self._maybe_merge_locked()

    def update_path(self, old_path: str, new_path: str):
        """Update doc_path after a note.move operation."""
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE notes_fts SET doc_path = ? WHERE doc_path = ?",
                    (new_path, old_path),
                )
                self._conn.execute(
                    "UPDATE doc_meta SET doc_path = ? WHERE doc_path = ?",
                    (new_path, old_path),
                )
                self._conn.commit()
            except Exception as e:
                logger.error("update_path failed: %s", e)

    def total_count(self) -> int:
        """Return total indexed notes. Thread-safe."""
        with self._lock:
            try:
                cur = self._conn.execute("SELECT count(*) FROM doc_meta")
                return cur.fetchone()[0] or 0
            except Exception:
                return 0

    def get_meta(self, doc_path: str) -> dict | None:
        """Return (note_id, file_mtime) for a doc_path. Thread-safe."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT note_id, file_mtime FROM doc_meta WHERE doc_path = ?",
                    (doc_path,),
                )
                row = cur.fetchone()
                if row:
                    return {"noteId": row[0], "mtime": row[1]}
                return None
            except Exception:
                return None

    def get_doc_path_by_note_id(self, note_id: str) -> str | None:
        """Look up doc_path by note_id. Thread-safe."""
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT doc_path FROM doc_meta WHERE note_id = ?", (note_id,)
                )
                row = cur.fetchone()
                return row[0] if row else None
            except Exception:
                return None

    def _maybe_merge_locked(self):
        self._write_count += 1
        if self._write_count >= MERGE_INTERVAL:
            self._write_count = 0
            try:
                self._conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('merge')")
                logger.debug("FTS5 merge triggered")
            except Exception as e:
                logger.warning("FTS5 merge failed: %s", e)

    # ── Query ──────────────────────────────────────────────────────────────

    def query(self, q: str, limit: int = 20, offset: int = 0) -> dict:
        """Full-text search with trigram tokenizer.

        Query strategy:
        - Strip FTS5 operator characters to prevent syntax errors
        - For short queries (1-2 CJK chars), append ``*`` for prefix matching
        - Do NOT wrap in double quotes (which disable wildcards)
        """
        # Remove FTS5 operators while keeping `*` for prefix matching
        import re
        safe = re.sub(r'["()+]', ' ', q).strip()
        safe = safe.replace('-', ' ').strip()
        # Remove standalone AND/OR/NOT operators
        safe = re.sub(r'\b(AND|OR|NOT)\b', ' ', safe, flags=re.IGNORECASE).strip()
        # For short queries, add * prefix match
        if len(safe) <= 3:
            safe = safe + '*'
        if not safe:
            return {"results": [], "totalHits": 0, "timeMs": 0}

        # Try FTS5 query first
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "SELECT doc_path, title, snippet(notes_fts, 2, '<mark>', '</mark>', '...', 40) AS snippet, "
                    "bm25(notes_fts) AS score "
                    "FROM notes_fts WHERE notes_fts MATCH ? "
                    "ORDER BY bm25(notes_fts) LIMIT ? OFFSET ?",
                    (safe, limit, offset),
                )
                results = [
                    {"path": row[0], "title": row[1], "snippet": row[2], "score": round(row[3], 2)}
                    for row in cursor.fetchall()
                ]
                # Total hits
                count_cur = self._conn.execute(
                    "SELECT count(*) FROM notes_fts WHERE notes_fts MATCH ?",
                    (safe,),
                )
                total = count_cur.fetchone()[0]
            except Exception:
                # FTS5 query failed (short query, syntax issue, etc.) — fall through to LIKE
                results = []
                total = 0

        # Fallback: try LIKE on file paths for queries FTS5 couldn't match
        if total == 0 and len(q.strip()) > 0:
            like_pat = f'%{q}%'
            with self._lock:
                try:
                    cursor = self._conn.execute(
                        "SELECT doc_path, file_mtime FROM doc_meta WHERE doc_path LIKE ? LIMIT ?",
                        (like_pat, limit),
                    )
                    for row in cursor.fetchall():
                        results.append({
                            "path": row[0], "title": Path(row[0]).stem,
                            "snippet": f"...(文件名匹配 {q})...", "score": -1.0,
                        })
                    total = len(results)
                except Exception:
                    pass

        with self._lock:
            cursor = self._conn.execute(
                "SELECT doc_path, title, snippet(notes_fts, 2, '<mark>', '</mark>', '...', 40) AS snippet, "
                "bm25(notes_fts) AS score "
                "FROM notes_fts WHERE notes_fts MATCH ? "
                "ORDER BY bm25(notes_fts) LIMIT ? OFFSET ?",
                (safe, limit, offset),
            )
            results = [
                {
                    "path": row[0],
                    "title": row[1],
                    "snippet": row[2],
                    "score": round(row[3], 2),
                }
                for row in cursor.fetchall()
            ]

            # Total hits
            count_cur = self._conn.execute(
                "SELECT count(*) FROM notes_fts WHERE notes_fts MATCH ?",
                (safe,),
            )
            total = count_cur.fetchone()[0]

        return {
            "results": results,
            "totalHits": total,
            "timeMs": 0,  # caller fills this
        }

    # ── Rebuild ────────────────────────────────────────────────────────────

    def rebuild(self, walk_fn: Callable, kb_root: str, cancel_event: threading.Event | None = None,
                batch_size: int = 100):
        """Rebuild the FTS5 index from all .md files.

        Files are processed in batches of *batch_size* and committed per batch.
        ``$/cancel`` takes effect between batches (or mid-batch via upsert's
        per-file commit if batch_size=1).  Already-processed files remain in
        the index after a partial cancel.
        """
        logger.info("Rebuilding FTS5 index… (batch=%d)", batch_size)
        count = 0
        batch = []

        for rel_path, abs_path in walk_fn(Path(kb_root), max_depth=None):
            if cancel_event and cancel_event.is_set():
                if batch:
                    self._commit_batch(batch)
                logger.info("Rebuild cancelled after %d files", count)
                break
            try:
                raw = abs_path.read_text(encoding="utf-8")
                from models.note import parse_note

                note, _ = parse_note(raw)
                content = note.content
                title = note.title or Path(rel_path).stem
                ocr_parts = []
                for att in (note.attachments or []):
                    if att.get("ocrText"):
                        ocr_parts.append(att["ocrText"])
                ocr_text = "\n".join(ocr_parts)

                batch.append((rel_path, title, content, ocr_text, note.note_id, abs_path.stat().st_mtime))
                count += 1

                if len(batch) >= batch_size:
                    self._commit_batch(batch)
                    batch = []

            except Exception as e:
                logger.warning("Rebuild skip %s: %s", rel_path, e)
                continue

        # Flush remaining
        if batch:
            self._commit_batch(batch)

        logger.info("Rebuild complete: %d files indexed", count)
        return count

    def _commit_batch(self, batch):
        with self._lock:
            for rel_path, title, content, ocr_text, note_id, mtime in batch:
                title = self._sanitize(title)
                content = self._sanitize(content)
                ocr_text = self._sanitize(ocr_text)
                self._conn.execute("DELETE FROM notes_fts WHERE doc_path = ?", (rel_path,))
                self._conn.execute(
                    "INSERT INTO notes_fts (doc_path, title, content, ocr_text) VALUES (?, ?, ?, ?)",
                    (rel_path, title, content, ocr_text),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO doc_meta (doc_path, note_id, file_mtime, word_count) VALUES (?, ?, ?, ?)",
                    (rel_path, note_id, mtime, len(content.split())),
                )
            self._conn.commit()
            self._write_count += len(batch)
            if self._write_count >= MERGE_INTERVAL:
                self._write_count = 0
                try:
                    self._conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('merge')")
                except Exception as e:
                    logger.warning("FTS5 merge failed: %s", e)

    # ── Incremental sync (startup) ────────────────────────────────────────

    def migrate(self, walk_fn: Callable, kb_root: str):
        """Startup incremental sync — compare file mtime vs doc_meta.

        Files that are newer (or not in doc_meta) are re-indexed.
        Files in doc_meta but missing from disk are removed from both indexes.
        """
        logger.info("Incremental sync start…")
        known: dict[str, float] = {}
        with self._lock:
            rows = self._conn.execute("SELECT doc_path, file_mtime FROM doc_meta").fetchall()
            known = {r[0]: r[1] for r in rows}

        found: set[str] = set()
        updated = 0
        for rel_path, abs_path in walk_fn(Path(kb_root), max_depth=None):
            found.add(rel_path)
            mtime = abs_path.stat().st_mtime
            stored = known.get(rel_path)
            if stored is None or abs(mtime - stored) > 0.01:
                try:
                    raw = abs_path.read_text(encoding="utf-8")
                    from models.note import parse_note
                    note, _ = parse_note(raw)
                    content = note.content
                    title = note.title or Path(rel_path).stem
                    ocr_parts = [att.get("ocrText", "") for att in (note.attachments or [])]
                    ocr_text = "\n".join([p for p in ocr_parts if p])
                    self.upsert(rel_path, title, content, ocr_text, note.note_id, mtime)
                    updated += 1
                except Exception as e:
                    logger.warning("Sync skip %s: %s", rel_path, e)

        # Remove stale entries
        stale = set(known.keys()) - found
        for p in stale:
            self.delete(p)

        logger.info("Sync complete: %d updated, %d stale removed", updated, len(stale))
