"""Tests for models/note.py — front matter parsing, merging, title extraction."""

from pathlib import Path
import pytest
import yaml

from models.note import (
    parse_note, make_note, dump_note, merge_front_matter,
    extract_title, strip_front_matter,
)


class TestParseNote:
    def test_basic_front_matter(self):
        raw = """---
schemaVersion: 1
id: n_abc123
tags: [Java]
---

# Hello
Body text
"""
        note, yaml_text = parse_note(raw)
        assert note.schemaVersion == 1
        assert note.note_id == "n_abc123"
        assert note.tags == ["Java"]
        assert note.content == "# Hello\nBody text\n"

    def test_no_front_matter(self):
        raw = "# Just content\nNo front matter here."
        note, yaml_text = parse_note(raw)
        assert note.schemaVersion == 1
        assert yaml_text == ""
        assert note.content == raw

    def test_empty_content(self):
        raw = "---\nschemaVersion: 1\n---\n"
        note, _ = parse_note(raw)
        assert note.schemaVersion == 1
        assert note.content == ""

    def test_malformed_yaml(self):
        raw = "---\n: bad yaml\n---\nBody"
        note, _ = parse_note(raw)
        assert note.content == raw  # treat as plain body on YAML error


class TestMakeNote:
    def test_new_note(self):
        note = make_note("# Title\n\nHello", tags=["test"])
        assert note.note_id.startswith("n_")
        assert note.title == "Title"
        assert note.tags == ["test"]
        assert note.created_at
        assert note.updated_at

    def test_new_note_no_tags(self):
        note = make_note("# Title")
        assert note.tags == []


class TestDumpRoundtrip:
    def test_roundtrip(self):
        raw = """---
schemaVersion: 1
id: n_xyz
title: Test
tags:
- java
- thread
---

# Test
Content here
"""
        note, _ = parse_note(raw)
        dumped = dump_note(note)
        note2, _ = parse_note(dumped)
        assert note2.note_id == "n_xyz"
        assert note2.title == "Test"
        assert note2.tags == ["java", "thread"]
        assert note2.content == "# Test\nContent here\n"

    def test_dump_new_note(self):
        note = make_note("Hello", tags=["a"])
        dumped = dump_note(note)
        assert dumped.startswith("---\n")
        assert "schemaVersion: 1" in dumped
        assert "tags:\n- a" in dumped


class TestMerge:
    def test_preserve_id_and_dates(self):
        old = make_note("# Old", tags=[])
        old_created = old.created_at
        merged = merge_front_matter(old, new_content="# New", new_tags=["updated"])
        assert merged.note_id == old.note_id
        assert merged.created_at == old_created
        # updatedAt is refreshed; may equal old.updatedAt if same millisecond
        assert merged.updated_at >= old.updated_at
        assert merged.tags == ["updated"]
        assert merged.content == "# New"

    def test_merge_no_new_tags(self):
        old = make_note("# Old", tags=["keep"])
        merged = merge_front_matter(old, new_content="# New")
        assert merged.tags == ["keep"]

    def test_merge_no_new_content(self):
        old = make_note("# Old\n", tags=["a"])
        merged = merge_front_matter(old, new_tags=["b"])
        assert merged.content == "# Old\n"
        assert merged.tags == ["b"]


class TestTitleExtraction:
    def test_extract_h1(self):
        assert extract_title("# Hello") == "Hello"
        assert extract_title("#   Spaced  ") == "Spaced"

    def test_ignore_h2(self):
        assert extract_title("## Not title") is None

    def test_no_heading(self):
        assert extract_title("Plain text") is None

    def test_first_h1_only(self):
        content = "# First\n\n## Second\n\n# Third"
        assert extract_title(content) == "First"


class TestStripFrontMatter:
    def test_strip(self):
        raw = "---\nkey: val\n---\nBody\nMore"
        assert strip_front_matter(raw) == "Body\nMore"

    def test_no_front_matter(self):
        raw = "Just body"
        assert strip_front_matter(raw) == raw
