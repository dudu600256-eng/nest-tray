"""Tests for storage/fs.py — atomic writes, path validation, file operations."""

import os
import secrets
import tempfile
from pathlib import Path

import pytest

from storage.fs import (
    assert_read_writable,
    atomic_write,
    copy_image,
    delete_file,
    read_file,
    rename_path,
    resolve_kb_path,
    walk_md_files,
)


@pytest.fixture
def kb():
    tmp = Path(tempfile.mktemp())
    tmp.mkdir(parents=True, exist_ok=True)
    yield tmp
    # Cleanup
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


class TestResolvePath:
    def test_normal_path(self, kb):
        resolved = resolve_kb_path(kb, "test/note.md")
        assert resolved.name == "note.md"
        assert "test" in str(resolved)
        assert not resolved.exists()  # just resolved, not created

    def test_reject_dotdot(self, kb):
        with pytest.raises(PermissionError):
            resolve_kb_path(kb, "../secret.txt")

    def test_reject_absolute(self, kb):
        with pytest.raises(PermissionError):
            resolve_kb_path(kb, "/etc/passwd")

    @pytest.mark.skipif(os.name == "nt", reason="symlinks require admin on Windows")
    def test_reject_outside_symlink(self, kb):
        outside = Path(tempfile.mktemp())
        outside.touch()
        link = kb / "link"
        try:
            os.symlink(str(outside), str(link))
            with pytest.raises(PermissionError):
                resolve_kb_path(kb, "link")
        except (OSError, NotImplementedError):
            pass  # symlinks not supported on all platforms
        finally:
            outside.unlink(missing_ok=True)


class TestAtomicWrite:
    def test_create_file(self, kb):
        fp = kb / "hello.md"
        atomic_write(fp, "# Hello")
        assert fp.exists()
        assert fp.read_text(encoding="utf-8") == "# Hello"

    def test_overwrite(self, kb):
        fp = kb / "overwrite.md"
        atomic_write(fp, "version 1")
        atomic_write(fp, "version 2")
        assert fp.read_text(encoding="utf-8") == "version 2"

    def test_creates_parent_dirs(self, kb):
        fp = kb / "a" / "b" / "deep.md"
        atomic_write(fp, "deep")
        assert fp.exists()

    def test_utf8(self, kb):
        fp = kb / "cn.md"
        content = "# 中文笔记\n测试内容"
        atomic_write(fp, content)
        assert fp.read_text(encoding="utf-8") == content


class TestReadDeleteMove:
    def test_read_file(self, kb):
        fp = kb / "readme.md"
        atomic_write(fp, "content")
        assert read_file(fp) == "content"

    def test_delete_file(self, kb):
        fp = kb / "todelete.md"
        atomic_write(fp, "bye")
        delete_file(fp)
        assert not fp.exists()

    def test_rename_path(self, kb):
        src = kb / "src.md"
        dst = kb / "dst.md"
        atomic_write(src, "move me")
        rename_path(src, dst)
        assert not src.exists()
        assert dst.read_text(encoding="utf-8") == "move me"

    def test_rename_target_exists(self, kb):
        src = kb / "a.md"
        dst = kb / "b.md"
        atomic_write(src, "a")
        atomic_write(dst, "b")
        with pytest.raises(FileExistsError):
            rename_path(src, dst)


class TestWalk:
    def test_walk_md_files(self, kb):
        (kb / "a.md").write_text("a", encoding="utf-8")
        (kb / "sub").mkdir()
        (kb / "sub" / "b.md").write_text("b", encoding="utf-8")
        (kb / "sub" / "c.txt").write_text("c", encoding="utf-8")

        files = list(walk_md_files(kb))
        assert len(files) == 2
        paths = [str(f[0]).replace("\\", "/") for f in files]  # normalise separator
        assert "a.md" in paths
        assert "sub/b.md" in paths
