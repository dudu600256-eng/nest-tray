"""
Note Tray — Note data model and front matter handling.
=======================================================
YAML front matter + Markdown body, per spec §5.

schemaVersion is the only mandatory field. All others are
optional and auto-generated when absent.
"""

import re
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

import yaml

# ── Front matter regex ─────────────────────────────────────────────────────
# Match the FIRST ---...--- block at the start of the file
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# ── Types ──────────────────────────────────────────────────────────────────

class Note:
    """Represents a single .md note with YAML front matter."""

    __slots__ = (
        "schemaVersion", "note_id", "title", "created_at", "updated_at",
        "tags", "links", "attachments", "content",
    )

    def __init__(self, content: str = ""):
        self.schemaVersion: int = 1
        self.note_id: str = ""
        self.title: str = ""
        self.created_at: str = ""
        self.updated_at: str = ""
        self.tags: list[str] = []
        self.links: list[str] = []
        self.attachments: list[dict] = []
        self.content: str = content


# ── Public API ─────────────────────────────────────────────────────────────

def parse_note(raw: str) -> tuple[Note, str]:
    """Parse a complete .md string → (Note, raw_yaml_or_empty).

    Returns the Note object and the raw YAML string (empty if no front matter).
    Safe for use with ``yaml.safe_load()`` — never ``yaml.load()``.
    """
    m = _FM_RE.match(raw)
    note = Note()
    yaml_text = ""
    body = raw

    if m:
        yaml_text = m.group(1)
        body = raw[m.end():]
        try:
            data = yaml.safe_load(yaml_text)
            if isinstance(data, dict):
                _apply_front_matter(note, data)
        except yaml.YAMLError:
            # Malformed YAML — treat as plain body
            yaml_text = ""
            body = raw

    note.content = body
    if not note.title and note.content:
        note.title = extract_title(note.content) or ""
    return note, yaml_text


def make_note(content: str, tags: list[str] | None = None) -> Note:
    """Create a new Note from scratch (for file creation)."""
    note = Note(content)
    note.note_id = _new_id()
    now = _now_iso()
    note.created_at = now
    note.updated_at = now
    if tags:
        note.tags = list(tags)
    if note.content:
        note.title = extract_title(note.content) or ""
    return note


def dump_note(note: Note, raw_yaml: str = "") -> str:
    """Serialize a Note back to .md string (YAML front matter + body).

    If *raw_yaml* is provided, unknown fields (not managed by Note) are
    spliced into the output to preserve hand-edits.
    """
    data = {
        "schemaVersion": note.schemaVersion,
    }
    if note.note_id:
        data["id"] = note.note_id
    if note.created_at:
        data["createdAt"] = note.created_at
    if note.updated_at:
        data["updatedAt"] = note.updated_at
    if note.title:
        data["title"] = note.title
    if note.tags:
        data["tags"] = note.tags
    if note.links:
        data["links"] = note.links
    if note.attachments:
        data["attachments"] = note.attachments

    yaml_text = yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()

    # Append unknown fields from the original YAML
    if raw_yaml:
        unknown = keep_unknown_fields(raw_yaml)
        if unknown:
            yaml_text += "\n" + unknown

    return f"---\n{yaml_text}\n---\n\n{note.content}"


def merge_front_matter(
    existing: Note,
    new_content: str | None = None,
    new_tags: list[str] | None = None,
) -> Note:
    """Merge new data into an existing Note, preserving unknown fields.

    Existing YAML fields are kept; only updatedAt/tags/attachments are changed.
    Unknown fields from the original raw YAML must be handled separately in
    the caller (fs.py) by splicing the YAML text — this function only manages
    the fields Note understands.
    """
    note = Note()
    note.schemaVersion = existing.schemaVersion
    note.note_id = existing.note_id
    note.created_at = existing.created_at
    note.updated_at = _now_iso()
    note.title = existing.title
    note.tags = list(new_tags) if new_tags is not None else list(existing.tags or [])
    note.links = list(existing.links) if existing.links else []
    note.attachments = list(existing.attachments) if existing.attachments else []
    # Only replace content if caller passed a non-empty string
    if new_content is not None and new_content.strip():
        note.content = new_content
    else:
        note.content = existing.content

    # Re-extract title from new content if it changed
    if new_content is not None and new_content.strip():
        extracted = extract_title(new_content)
        if extracted:
            note.title = extracted

    return note


def extract_title(content: str) -> str | None:
    """Extract the first ``# Title`` from markdown content."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return None


def strip_front_matter(raw: str) -> str:
    """Return the body after front matter (or the whole string if none)."""
    m = _FM_RE.match(raw)
    return raw[m.end():] if m else raw


# ── Internal helpers ───────────────────────────────────────────────────────

def _new_id() -> str:
    return "n_" + secrets.token_hex(4)


def _now_iso() -> str:
    """ISO 8601 with local timezone offset and millisecond precision."""
    now = datetime.now().astimezone()  # local timezone
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + now.strftime("%z")


_FIELD_MAP = {
    "schemaVersion": "schemaVersion",
    "id": "note_id",
    "title": "title",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "tags": "tags",
    "links": "links",
    "attachments": "attachments",
}

_KNOWN_FIELDS = {"schemaVersion", "id", "title", "createdAt", "updatedAt",
                  "tags", "links", "attachments"}


def _apply_front_matter(note: Note, data: dict):
    for yaml_key, attr in _FIELD_MAP.items():
        if yaml_key in data:
            setattr(note, attr, data[yaml_key])
    # Ensure schemaVersion is an int
    if not isinstance(note.schemaVersion, int):
        note.schemaVersion = int(note.schemaVersion) if note.schemaVersion else 1
    # Normalise note_id → id alias
    if "id" in data and not note.note_id:
        note.note_id = data["id"]


def keep_unknown_fields(raw_yaml: str, known_keys: set[str] | None = None) -> str:
    """Return YAML lines that are *not* known keys — for round-trip preservation.

    Note: only handles single-line scalar values. Multi-line YAML values
    (block scalars, folded scalars, nested sequences) are not preserved.
    """
    if known_keys is None:
        known_keys = _KNOWN_FIELDS
    keep = []
    for line in raw_yaml.splitlines():
        key = line.split(":")[0].strip() if ":" in line else ""
        if key and key not in known_keys:
            keep.append(line)
    return "\n".join(keep)
