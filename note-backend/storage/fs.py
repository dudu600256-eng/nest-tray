"""
Note Tray — File-system operations (atomic writes, path validation, read).
=======================================================================
All file I/O goes through this module.  Direct ``open()`` calls are forbidden
elsewhere in the backend.
"""

import os
import secrets
import shutil
import threading
from pathlib import Path
from typing import Callable

_ENCODING = "utf-8"

# ── Path validation ────────────────────────────────────────────────────────

KB_ERROR = PermissionError  # raised when path escapes the knowledge base


def resolve_kb_path(kb_root: str | Path, target: str) -> Path:
    """Resolve a user-provided target path relative to *kb_root*.

    Guards against ``..``-based directory traversal and symlink escapes.
    Returns an absolute ``Path`` inside the knowledge base.
    Raises ``PermissionError`` if the resolved path is outside *kb_root*.
    """
    kb = Path(kb_root).resolve()
    t = Path(kb, target).resolve()
    # Check prefix
    try:
        t.relative_to(kb)
    except ValueError:
        raise PermissionError(
            f"路径不在知识库内: {target} (resolve to {t}, kb={kb})"
        )
    return t


def assert_read_writable(path: str | Path) -> None:
    """Check that the knowledge-base root is accessible."""
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"知识库目录不存在: {p}")
    if not os.access(str(p), os.R_OK | os.W_OK):
        raise PermissionError(f"知识库目录不可读写: {p}")


# ── File locking (per-path) ────────────────────────────────────────────────

_path_locks: dict[str, threading.Lock] = {}
_path_locks_lock = threading.Lock()


def _per_path_lock(path: str) -> threading.Lock:
    """Return a Lock for the given path — one lock per path."""
    with _path_locks_lock:
        if path not in _path_locks:
            _path_locks[path] = threading.Lock()
        return _path_locks[path]


# ── Atomic write ───────────────────────────────────────────────────────────

def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically.

    1. Write to ``{path}.tmp.{random8}``
    2. ``fsync`` to guarantee durability
    3. ``os.replace`` (atomic on POSIX *and* Windows)

    The write is protected by a per-path ``threading.Lock`` to prevent
    concurrent ``read-modify-write`` races.
    """
    lock = _per_path_lock(str(path))
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp." + secrets.token_hex(4))
        try:
            with open(tmp, "w", encoding=_ENCODING, errors="replace") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            # Clean up temp file on failure
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise


# ── Read ───────────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    """Read the full text of a file (UTF-8)."""
    return path.read_text(encoding=_ENCODING)


# ── Delete ─────────────────────────────────────────────────────────────────

def delete_file(path: Path, missing_ok: bool = True) -> None:
    """Remove a single file."""
    try:
        path.unlink(missing_ok=missing_ok)
    except PermissionError:
        raise  # NOTE_LOCKED


# ── Rename / move ──────────────────────────────────────────────────────────

def rename_path(from_path: Path, to_path: Path) -> None:
    """Rename (move) a file. *to_path* must not exist."""
    if to_path.exists():
        raise FileExistsError(f"目标路径已存在: {to_path}")
    from_path.rename(to_path)


# ── Directory helpers ──────────────────────────────────────────────────────

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_image(src: Path, dst: Path) -> None:
    """Copy an image file to the attachments directory."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))


# ── mtime helpers ──────────────────────────────────────────────────────────

def get_mtime(path: Path) -> float:
    return path.stat().st_mtime


# ── Walk ───────────────────────────────────────────────────────────────────

def walk_md_files(root: Path, max_depth: int | None = 3):
    """Yield (relative_path, absolute_path) for every ``.md`` file.

    *root* — absolute path of the knowledge base root.
    *max_depth* — recursion limit (None = unlimited).
    """
    for abs_path in root.rglob("*.md"):
        rel = str(abs_path.relative_to(root))
        # Normalise separator for depth counting
        depth = rel.replace("\\", "/").count("/")
        if max_depth is not None and depth > max_depth:
            continue
        yield rel, abs_path
