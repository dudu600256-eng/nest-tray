"""Tests for search.py — SQLite FTS5 index management."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from search import SearchIndex


@pytest.fixture
def idx():
    tmp = Path(tempfile.mktemp(suffix=".db"))
    si = SearchIndex(tmp)
    yield si
    si.close()
    tmp.unlink(missing_ok=True)
    # Clean up SQLite WAL artefacts
    for ext in ("-wal", "-shm"):
        p = tmp.with_suffix(tmp.suffix + ext)
        p.unlink(missing_ok=True)


class TestSearchIndex:
    def test_upsert_and_query(self, idx):
        idx.upsert("test/note-1.md", "Hello World", "This is some content", "", "n_001", 1000.0)
        idx.upsert("test/note-2.md", "Python Programming", "Learning Python is fun", "", "n_002", 1001.0)

        result = idx.query("Hello")
        assert result["totalHits"] == 1
        assert result["results"][0]["title"] == "Hello World"

        result = idx.query("Python")
        assert result["totalHits"] == 1

        result = idx.query("not found")
        assert result["totalHits"] == 0

    def test_delete(self, idx):
        idx.upsert("a.md", "Title", "Content about X", "", "n_1", 1.0)
        idx.delete("a.md")
        result = idx.query("X")
        assert result["totalHits"] == 0

    def test_update_path(self, idx):
        idx.upsert("old/path.md", "T", "Content", "", "n_1", 1.0)
        idx.update_path("old/path.md", "new/path.md")
        result = idx.query("Content")
        assert result["results"][0]["path"] == "new/path.md"

    def test_query_sanitization(self, idx):
        """FTS5 operators should be disabled via quoting."""
        idx.upsert("test.md", "Test", "Java AND Python both exist", "", "n_1", 1.0)
        # Without quoting "AND" would be an operator
        result = idx.query("Java AND Python")
        # Should still find it because the phrase "Java AND Python" is matched as a whole
        assert result["totalHits"] >= 1

    def test_trigram_chinese(self, idx):
        idx.upsert("cn.md", "中文", "线程池配置问题排查", "", "n_cn", 1.0)
        result = idx.query("线程池")
        # trigram tokenizer matches "线程池" as a single trigram
        assert result["totalHits"] >= 1, f"expected match for 线程池, got {result}"
        assert len(result["results"]) >= 1

    def test_ocr_text(self, idx):
        idx.upsert("note.md", "Note", "Body text", "Exception in thread main", "n_1", 1.0)
        result = idx.query("Exception")
        assert result["totalHits"] == 1
        result2 = idx.query("Body")
        assert result2["totalHits"] == 1
