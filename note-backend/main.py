"""
Note Tray — Python Sidecar (main entry)
=========================================
JSON-RPC 2.0 over stdin/stdout line protocol.

Lifecycle:
  1. Startup → backend.hello handshake
  2. Event loop: read stdin → dispatch → write stdout
  3. Shutdown: system.shutdown RPC or unhandled exception → event.fatal → exit
"""

import asyncio
import json
import logging
import os
import secrets
import shutil
import sys
import threading
import time
import concurrent.futures
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

from log_config import setup_logging
from models.note import (
    dump_note,
    make_note,
    merge_front_matter,
    parse_note,
)
from ocr.engine import OcrEngine
from search import SearchIndex
from storage import fs as storage

logger = logging.getLogger("sidecar")

# ── Constants ──────────────────────────────────────────────────────────────
PROTOCOL_VERSION = 1
APP_VERSION = "0.1.0"
CAPABILITIES = ["note", "search", "ocr"]
IO_POOL_SIZE = 4
CPU_POOL_SIZE = 2
REQUEST_TIMEOUT = 5  # seconds; OCR uses 60s
OCR_TIMEOUT = 60

# ── Error codes ────────────────────────────────────────────────────────────
ERR_NOTE_NOT_FOUND = -32001
ERR_NOTE_PATH_INVALID = -32002
ERR_NOTE_CONTENT_TOO_LARGE = -32003
ERR_NOTE_LOCKED = -32004
ERR_NOTE_MOVE_DEST_EXISTS = -32005
ERR_SEARCH_ERROR = -32011
ERR_OCR_NOT_READY = -32021
ERR_OCR_UNSUPPORTED_FORMAT = -32022
ERR_OCR_IMAGE_TOO_LARGE = -32023
ERR_OCR_FAILED = -32024
ERR_DISK_FULL = -32031
ERR_KB_PATH_NOT_FOUND = -32032
ERR_CANCELLED = -32800
ERR_PARSE = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INTERNAL = -32603


class RpcError(Exception):
    """Exception that maps to a JSON-RPC error response."""
    def __init__(self, code: int, message: str, retryable: bool = False, user_action: str = "", detail: dict | None = None):
        self.code = code
        self.message = message
        self.data = {
            "retryable": retryable,
            "userAction": user_action,
            "detail": detail or {},
        }
        super().__init__(message)


def make_error(code: int, message: str, retryable: bool = False, user_action: str = "", detail: dict | None = None) -> dict:
    return {
        "code": code,
        "message": message,
        "data": {
            "retryable": retryable,
            "userAction": user_action,
            "detail": detail or {},
        },
    }


# ── App ────────────────────────────────────────────────────────────────────

class App:
    """Main application container — owns threads, pools, and handlers."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.io_pool = ThreadPoolExecutor(max_workers=IO_POOL_SIZE, thread_name_prefix="io")
        self.cpu_pool = ThreadPoolExecutor(max_workers=CPU_POOL_SIZE, thread_name_prefix="cpu")
        self._pending: dict[str, asyncio.Future] = {}  # requestId → Future (仅追踪支持 cancel 的请求)
        self._shutdown_flag = False
        # Queues created in run() where the event loop exists
        self._stdin_queue: asyncio.Queue[str] | None = None
        self._stdout_queue: asyncio.Queue[str] | None = None

        # Knowledge base root – from environment
        self.kb_root: str | None = os.environ.get("NOTE_KB_ROOT")
        self._validate_kb_root()

        # Subsystem stubs (populated in run())
        self.search: SearchIndex | None = None
        self.ocr: OcrEngine | None = None

    def _validate_kb_root(self):
        if self.kb_root:
            p = Path(self.kb_root)
            if not p.exists():
                logger.warning("KB root does not exist: %s", self.kb_root)
            elif not os.access(str(p), os.R_OK | os.W_OK):
                logger.warning("KB root not readable/writable: %s", self.kb_root)

    # ── I/O threads ────────────────────────────────────────────────────────

    def _stdin_reader(self):
        """Daemon thread: readline → _stdin_queue."""
        while not self._shutdown_flag:
            try:
                line = sys.stdin.readline()
                if not line:
                    self._shutdown_flag = True
                    break
                line = line.strip()
                if not line:
                    continue
                asyncio.run_coroutine_threadsafe(
                    self._stdin_queue.put(line), self.loop
                )
            except Exception:
                logger.exception("stdin reader error")
                break

    def _stdout_writer(self):
        """Daemon thread: take from _stdout_queue → stdout write(flush=True)."""
        while not self._shutdown_flag:
            try:
                msg = asyncio.run_coroutine_threadsafe(
                    self._stdout_queue.get(), self.loop
                ).result(timeout=0.5)
                sys.stdout.write(msg + "\n")
                sys.stdout.flush()
            except (concurrent.futures.TimeoutError, asyncio.TimeoutError):
                continue
            except Exception:
                logger.exception("stdout writer error")
                break

    # ── Event helpers ──────────────────────────────────────────────────────

    def _send_event(self, method: str, params: dict | None = None):
        """Enqueue a JSON-RPC notification (no id)."""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            msg = json.dumps(payload, ensure_ascii=False)
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
        except OSError:
            self._shutdown_flag = True

    # ── Dispatch ───────────────────────────────────────────────────────────

    async def _dispatch(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Parse error: %s", raw[:200])
            self._respond(None, error=make_error(ERR_PARSE, "RPC JSON 解析失败"))
            return

        rid = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "$/cancel":
            req_id = params.get("requestId", "")
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                future.cancel()
                logger.debug("Cancelled request: %s", req_id)
            return

        handler = self._handlers.get(method)
        if handler is None:
            self._respond(rid, error=make_error(ERR_METHOD_NOT_FOUND, f"未知方法: {method}"))
            return

        # Track all requests for cancelling (remove on completion)
        if rid:
            logger.debug("dispatch: method=%s rid=%s", method, rid)
            future = asyncio.ensure_future(self._run_handler(handler, params, rid, method))
            self._pending[rid] = future
            future.add_done_callback(lambda _: self._pending.pop(rid, None))
        else:
            await self._run_handler(handler, params, None, method)

    async def _run_handler(self, handler, params, rid, method_name=""):
        # Determine timeout for this method
        timeout = self._timeouts.get(method_name, 5)  # default 5s
        try:
            coro = handler(self, params)
            if timeout is not None:
                result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                result = await coro
            self._respond(rid, result=result)
        except asyncio.TimeoutError:
            logger.warning("Handler timeout: method=%s rid=%s", method_name, rid)
            self._respond(rid, error=make_error(ERR_INTERNAL, "请求超时", retryable=True))
        except asyncio.CancelledError:
            self._respond(rid, error=make_error(ERR_CANCELLED, "操作已取消", retryable=True))
        except RpcError as e:
            self._respond(rid, error=make_error(e.code, e.message, e.data["retryable"], e.data["userAction"], e.data["detail"]))
        except PermissionError as e:
            self._respond(rid, error=make_error(ERR_NOTE_PATH_INVALID, str(e)))
        except FileNotFoundError as e:
            self._respond(rid, error=make_error(ERR_NOTE_NOT_FOUND, str(e)))
        except FileExistsError as e:
            self._respond(rid, error=make_error(ERR_NOTE_MOVE_DEST_EXISTS, str(e), user_action="先删除或重命名目标文件"))
        except RuntimeError as e:
            self._respond(rid, error=make_error(ERR_INTERNAL, str(e)))
        except Exception as e:
            logger.exception("Handler error: %s", method_name)
            self._respond(rid, error=make_error(ERR_INTERNAL, str(e)))

    def _respond(self, rid: str | None, *, result=None, error=None):
        if rid is None:
            return
        body = {"jsonrpc": "2.0", "id": rid}
        if error:
            body["error"] = error
        else:
            body["result"] = result
        # Write directly to stdout (bypass queue) to avoid any queue threading issues
        try:
            msg = json.dumps(body, ensure_ascii=False)
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
        except OSError:
            self._shutdown_flag = True

    # ── Handshake ──────────────────────────────────────────────────────────

    def _send_hello(self):
        hello = {
            "jsonrpc": "2.0",
            "method": "backend.hello",
            "params": {
                "version": APP_VERSION,
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": CAPABILITIES,
            },
        }
        sys.stdout.write(json.dumps(hello, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        logger.info("Handshake sent: protocolVersion=%d", PROTOCOL_VERSION)

    # ── Shutdown ────────────────────────────────────────────────────────────

    async def shutdown(self) -> dict:
        """Graceful shutdown — returns a result dict before exiting."""
        logger.info("Shutdown requested")
        # Let the current response flush
        await asyncio.sleep(0.2)
        self._shutdown_flag = True
        for rid, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
        self._pending.clear()
        self.io_pool.shutdown(wait=False)
        self.cpu_pool.shutdown(wait=False)
        if self.search and hasattr(self.search, "close"):
            self.search.close()
        logger.info("Shutdown complete")
        # Schedule exit after response is sent
        asyncio.get_running_loop().call_later(0.1, lambda: sys.exit(0))
        return {"ok": True}

    # ── Run ────────────────────────────────────────────────────────────────

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self._stdin_queue = asyncio.Queue()
        self._stdout_queue = asyncio.Queue()
        logger.info("App started — kb_root=%s, capabilities=%s", self.kb_root, CAPABILITIES)

        # Initialise search — database in %APPDATA%/note-tray/note.db
        app_data = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "note-tray"
        if self.kb_root:
            try:
                self.search = SearchIndex(app_data / "note.db")
                # Startup incremental sync
                self.search.migrate(storage.walk_md_files, self.kb_root)
            except Exception as e:
                logger.warning("Search init failed: %s", e)

        # Initialise OCR (starts background preload)
        self.ocr = OcrEngine(loop=self.loop, send_event=self._send_event)

        self._handlers: dict[str, Callable] = {
            "system.status": self._handle_status,
            "system.shutdown": lambda app, p: self.shutdown(),
            # Phase 2 — notes
            "note.save": self._handle_note_save,
            "note.get": self._handle_note_get,
            "note.delete": self._handle_note_delete,
            "note.delete_folder": self._handle_note_delete_folder,
            "note.move": self._handle_note_move,
            "note.list_folder": self._handle_list_folder,
            "browser.tree": self._handle_browser_tree,
            "clipboard.ingest": self._handle_clipboard_ingest,
            "attachment.list": self._handle_attachment_list,
            # Phase 3 — search
            "search.query": self._handle_search_query,
            "search.rebuild": self._handle_search_rebuild,
            # Phase 4 — OCR
            "ocr.extract": self._handle_ocr_extract,
            "ocr.store": self._handle_ocr_store,
            "clipboard.ingest_image": self._handle_clipboard_ingest_image,
        }

        self._send_hello()
        threading.Thread(target=self._stdin_reader, daemon=True, name="stdin-reader").start()
        threading.Thread(target=self._stdout_writer, daemon=True, name="stdout-writer").start()

        # Per-handler timeout overrides (in seconds, None = no timeout)
        self._timeouts: dict[str, float | None] = {
            "ocr.extract": 60,
            "search.rebuild": None,  # no timeout
            "ocr.store": 60,
        }

        while not self._shutdown_flag:
            try:
                raw = await asyncio.wait_for(self._stdin_queue.get(), timeout=0.5)
                await self._dispatch(raw)
            except asyncio.TimeoutError:
                continue
            except Exception:
                logger.exception("Dispatch loop error")

    # ── Built-in handlers ──────────────────────────────────────────────────

    async def _handle_status(self, app, params) -> dict:
        root_path = self.kb_root or ""
        disk_free = 0
        kb_exists = bool(root_path and Path(root_path).exists())
        if kb_exists:
            try:
                _, _, free = shutil.disk_usage(root_path)
                disk_free = free
            except Exception:
                pass

        return {
            "ok": kb_exists,
            "indexOk": self.search is not None and self.search.ready,
            "ocrOk": self.ocr is not None and self.ocr.state == "paddleocr",
            "ocrEngine": self.ocr.state if self.ocr else "unavailable",
            "protocolVersion": PROTOCOL_VERSION,
            "rootPath": root_path,
            "diskFreeBytes": disk_free,
            "totalNotes": self._get_total_notes(),
        }

    def _get_total_notes(self) -> int:
        """Return approximate total note count."""
        if self.search and self.search.ready:
            return self.search.total_count()
        if self.kb_root:
            kb = Path(self.kb_root)
            if kb.exists():
                count = 0
                for _ in kb.rglob("*.md"):
                    count += 1
                    if count > 9999:
                        break
                return count
        return 0

    # ── Phase 2: Note handlers ────────────────────────────────────────────

    def _kb_path(self, rel_path: str) -> Path:
        """Resolve a relative path inside the knowledge base."""
        if not self.kb_root:
            raise PermissionError("知识库根目录未配置")
        return storage.resolve_kb_path(self.kb_root, rel_path)

    def _today_str(self) -> str:
        return date.today().isoformat()

    async def _handle_note_save(self, app, params) -> dict:
        path = params["path"]
        content = params.get("content", "")
        tags = params.get("tags")

        def _save():
            fp = self._kb_path(path)
            if fp.exists():
                raw = storage.read_file(fp)
                existing, raw_yaml = parse_note(raw)
                merged = merge_front_matter(existing, new_content=content, new_tags=tags)
                final = dump_note(merged, raw_yaml=raw_yaml)
                storage.atomic_write(fp, final)
                note_id, saved_at = merged.note_id, merged.updated_at
                title = merged.title or Path(path).stem
                upsert_content = merged.content
                ocr_parts = [a.get("ocrText", "") for a in (merged.attachments or [])]
                ocr_text = "\n".join([p for p in ocr_parts if p])
            else:
                if not content.strip():
                    raise ValueError("content 为空")
                note = make_note(content, tags=tags)
                storage.atomic_write(fp, dump_note(note))
                note_id, saved_at = note.note_id, note.updated_at
                title = note.title or Path(path).stem
                upsert_content = note.content
                ocr_text = ""

            # Sync with search index
            if self.search:
                self.search.upsert(
                    doc_path=path,
                    title=title,
                    content=upsert_content,
                    ocr_text=ocr_text,
                    note_id=note_id,
                    mtime=fp.stat().st_mtime,
                )
            return note_id, saved_at

        note_id, saved_at = await self.loop.run_in_executor(self.io_pool, _save)
        return {"noteId": note_id, "savedAt": saved_at, "path": path}

    async def _handle_note_get(self, app, params) -> dict:
        path = params["path"]

        def _get():
            fp = self._kb_path(path)
            raw = storage.read_file(fp)
            note, _ = parse_note(raw)
            return note

        note = await self.loop.run_in_executor(self.io_pool, _get)
        return {"noteId": note.note_id, "path": path, "content": note.content, "frontMatter": {
            "schemaVersion": note.schemaVersion,
            "id": note.note_id,
            "title": note.title,
            "createdAt": note.created_at,
            "updatedAt": note.updated_at,
            "tags": note.tags,
            "attachments": note.attachments,
        }}

    async def _handle_note_delete(self, app, params) -> dict:
        path = params["path"]

        def _delete():
            fp = self._kb_path(path)
            raw = storage.read_file(fp)
            note, _ = parse_note(raw)
            for att in (note.attachments or []):
                img = self._kb_path(att.get("path", ""))
                if img.exists():
                    img.unlink()
            fp.unlink()
            # FTS5 sync
            if self.search:
                self.search.delete(path)
            return {}

        return await self.loop.run_in_executor(self.io_pool, _delete)

    async def _handle_note_delete_folder(self, app, params) -> dict:
        folder = params.get("folder", "")
        if not folder.strip():
            raise RpcError(ERR_PARSE, "请指定要删除的项目文件夹")

        def _delete_folder():
            root = self._kb_path(folder)
            if not root.exists() or not root.is_dir():
                raise FileNotFoundError(f"文件夹不存在: {folder}")

            # Collect all .md files for FTS5 removal
            md_files = []
            for rel, abs_path in storage.walk_md_files(root, max_depth=None):
                # Compute relative path from KB root
                full_rel = str(Path(folder) / rel) if rel else folder
                md_files.append((full_rel, abs_path))

            # Delete all .md files and their attachments
            for full_rel, abs_path in md_files:
                try:
                    raw = storage.read_file(abs_path)
                    note, _ = parse_note(raw)
                    for att in (note.attachments or []):
                        img = self._kb_path(att.get("path", ""))
                        if img.exists():
                            img.unlink()
                    abs_path.unlink()
                    if self.search:
                        self.search.delete(full_rel)
                except Exception as e:
                    logger.warning("Delete skip %s: %s", full_rel, e)

            # Delete the images directory
            img_dir = root / "images"
            if img_dir.exists():
                import shutil
                shutil.rmtree(img_dir, ignore_errors=True)

            # Remove the folder itself (if empty)
            try:
                root.rmdir()
            except OSError:
                pass  # may not be empty, that's fine

            return {"ok": True}

        return await self.loop.run_in_executor(self.io_pool, _delete_folder)

    async def _handle_note_move(self, app, params) -> dict:
        from_path = params["from"]
        to_path = params["to"]
        if from_path == to_path:
            return {}

        def _move():
            src = self._kb_path(from_path)
            dst = self._kb_path(to_path)
            storage.rename_path(src, dst)
            # FTS5 sync
            if self.search:
                self.search.update_path(from_path, to_path)
            return {}

        return await self.loop.run_in_executor(self.io_pool, _move)

    async def _handle_list_folder(self, app, params) -> dict:
        folder = params.get("folder", "")
        limit = int(params.get("limit", 20))

        def _list():
            root = self._kb_path(folder)
            if not root.is_dir():
                return {"notes": []}
            entries = []
            for rel, abs_path in storage.walk_md_files(root):
                try:
                    raw = storage.read_file(abs_path)
                    note, _ = parse_note(raw)
                    snippet = note.content[:100].replace("\n", " ")
                    title = note.title or Path(rel).stem
                    entries.append({
                        "path": rel,
                        "title": title,
                        "updatedAt": note.updated_at,
                        "snippet": snippet,
                    })
                except Exception:
                    continue
            entries.sort(key=lambda e: e["updatedAt"], reverse=True)
            return {"notes": entries[:limit]}

        return await self.loop.run_in_executor(self.io_pool, _list)

    async def _handle_browser_tree(self, app, params) -> dict:
        root_rel = params.get("root", "")

        def _tree():
            base = Path(self.kb_root) if not root_rel else self._kb_path(root_rel)

            def _build(dir_path: Path, rel: str, depth: int) -> list[dict]:
                if depth > 3:
                    return []
                try:
                    items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                except PermissionError:
                    return []
                children = []
                for child in items:
                    child_rel = f"{rel}/{child.name}" if rel else child.name
                    node: dict = {"name": child.name, "path": child_rel}
                    if child.is_dir():
                        node["type"] = "dir"
                        node["children"] = _build(child, child_rel, depth + 1)
                    else:
                        node["type"] = "file"
                    children.append(node)
                return children

            return {"tree": _build(base, root_rel, 0)}

        return await self.loop.run_in_executor(self.io_pool, _tree)

    async def _handle_clipboard_ingest(self, app, params) -> dict:
        text = params.get("text", "")
        folder = params.get("folder", "")
        tags = params.get("tags")

        if not text.strip():
            raise RpcError(ERR_PARSE, "剪贴板内容为空", user_action="输入内容后再保存")

        if not folder.strip():
            raise RpcError(ERR_PARSE, "请先选择项目文件夹", user_action="选择或新建一个项目文件夹")

        today = self._today_str()
        folder_path = f"{folder}/note-{today}.md"

        def _ingest():
            fp = self._kb_path(folder_path)
            if not fp.exists():
                note = make_note(text, tags=tags)
                storage.atomic_write(fp, dump_note(note))
                note_id = note.note_id
                upsert_content = note.content
                upsert_title = note.title or Path(folder_path).stem
            else:
                raw = storage.read_file(fp)
                existing, raw_yaml = parse_note(raw)
                now_str = datetime.now().strftime("%H:%M")
                appended = f"{existing.content}\n\n---\n\n## {now_str}\n\n{text}"
                merged = merge_front_matter(existing, new_content=appended, new_tags=tags)
                storage.atomic_write(fp, dump_note(merged, raw_yaml=raw_yaml))
                note_id = merged.note_id
                upsert_content = merged.content
                upsert_title = merged.title or Path(folder_path).stem

            # Sync with search index
            if self.search:
                try:
                    self.search.upsert(
                        doc_path=folder_path,
                        title=upsert_title,
                        content=upsert_content,
                        note_id=note_id,
                        mtime=fp.stat().st_mtime,
                    )
                except Exception as e:
                    logger.error("clipboard upsert failed: %s", e)
                    raise
            return note_id

        logger.debug("clipboard.ingest: submitting to IO pool")
        note_id = await self.loop.run_in_executor(self.io_pool, _ingest)
        logger.debug("clipboard.ingest: complete, noteId=%s", note_id)
        return {"noteId": note_id}

    async def _handle_attachment_list(self, app, params) -> dict:
        note_id = params.get("noteId", "")
        if not note_id:
            return {"attachments": []}

        def _list_att():
            # First try doc_meta for fast lookup
            if self.search and self.search.ready:
                try:
                    doc_path = self.search.get_doc_path_by_note_id(note_id)
                    if doc_path:
                        raw = storage.read_file(self._kb_path(doc_path))
                        note, _ = parse_note(raw)
                        return note.attachments or []
                except Exception:
                    pass
            # Fall back: full scan
            kb = Path(self.kb_root)
            for rel, abs_path in storage.walk_md_files(kb):
                try:
                    raw = storage.read_file(abs_path)
                    note, _ = parse_note(raw)
                    if note.note_id == note_id:
                        return note.attachments or []
                except Exception:
                    continue
            raise FileNotFoundError(f"笔记 {note_id} 不存在")

        attachments = await self.loop.run_in_executor(self.io_pool, _list_att)
        return {"attachments": attachments}

    # ── Phase 3: Search handlers ──────────────────────────────────────────

    async def _handle_search_query(self, app, params) -> dict:
        q = params.get("q", "")
        limit = int(params.get("limit", 20))
        offset = int(params.get("offset", 0))

        if not self.search:
            raise RuntimeError("搜索索引不可用")
        if not q.strip():
            return {"results": [], "totalHits": 0, "timeMs": 0}

        def _query():
            t0 = time.time()
            result = self.search.query(q, limit=limit, offset=offset)
            result["timeMs"] = int((time.time() - t0) * 1000)
            # Supplement updatedAt from doc_meta
            for r in result["results"]:
                try:
                    meta = self.search.get_meta(r["path"])
                    if meta:
                        r["noteId"] = meta["noteId"]
                        r["updatedAt"] = meta["mtime"]
                except Exception:
                    pass
            return result

        return await self.loop.run_in_executor(self.io_pool, _query)

    async def _handle_search_rebuild(self, app, params) -> dict:
        if not self.search or not self.kb_root:
            raise RuntimeError("搜索索引不可用")

        cancel_event = threading.Event()

        def _rebuild():
            t0 = time.time()
            count = self.search.rebuild(storage.walk_md_files, self.kb_root, cancel_event)
            elapsed = int((time.time() - t0) * 1000)
            # Emit event
            self._send_event("event.index.rebuilt", {"totalFiles": count, "timeMs": elapsed})
            return {}

        # Note: rebuild runs in IO pool (not CPU pool — it's I/O bound)
        future = self.loop.run_in_executor(self.io_pool, _rebuild)

        # Track for cancellation
        rebuild_rid = f"rebuild_{id(future)}"
        self._pending[rebuild_rid] = future

        try:
            return await future
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        finally:
            self._pending.pop(rebuild_rid, None)

    # ── Phase 4: OCR handlers ──────────────────────────────────────────────

    async def _handle_ocr_extract(self, app, params) -> dict:
        image_path = params.get("imagePath", "")
        if not image_path:
            raise ValueError("imagePath 为必填")

        # Validate file size (20MB limit)
        ip = Path(image_path)
        if not ip.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")
        size_mb = ip.stat().st_size / (1024 * 1024)
        if size_mb > 20:
            raise RpcError(ERR_OCR_IMAGE_TOO_LARGE, "图片超过 20MB", user_action="先压缩再重试")

        # Validate format
        ext = ip.suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            raise RpcError(ERR_OCR_UNSUPPORTED_FORMAT, "不支持的图片格式，请转 PNG/JPG")

        if not self.ocr or self.ocr.state != "paddleocr":
            if self.ocr and self.ocr.state == "loading":
                raise RpcError(ERR_OCR_NOT_READY, "OCR 引擎加载中", retryable=True)
            raise RpcError(ERR_OCR_NOT_READY, "OCR 不可用", user_action="检查 OCR 安装")

        def _extract():
            return self.ocr.extract(str(ip))

        return await self.loop.run_in_executor(self.cpu_pool, _extract)

    async def _handle_ocr_store(self, app, params) -> dict:
        image_path = params.get("imagePath", "")
        folder = params.get("folder", "")
        mode = params.get("mode", "both")

        if mode not in ("text", "image", "both"):
            raise ValueError(f"无效的 mode: {mode}")

        ip = Path(image_path)
        if not ip.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")
        if not folder.strip():
            raise RpcError(ERR_PARSE, "请先选择项目文件夹", user_action="选择或新建一个项目文件夹")

        def _store():
            # 1. Determine target note path
            today = self._today_str()
            folder_path = f"{folder}/note-{today}.md"
            fp = self._kb_path(folder_path)
            images_dir = fp.parent / "images"

            # 2. Handle image: only copy for mode in ("image", "both")
            attachment = None
            if mode in ("image", "both"):
                images_dir.mkdir(parents=True, exist_ok=True)
                ext = ip.suffix.lower() or ".png"
                img_name = f"{today}-{secrets.token_hex(4)}{ext}"
                dst = images_dir / img_name
                storage.copy_image(ip, dst)
                rel_img = f"{folder}/images/{img_name}"   # for YAML attachment (KB root relative)
                md_img = f"images/{img_name}"              # for markdown display (file relative)
            else:
                dst = None
                rel_img = ""
                md_img = ""

            # 3. Run OCR for mode in ("text", "both")
            ocr_result = {"text": "", "confidence": 0.0}
            if mode in ("text", "both") and self.ocr and self.ocr.state == "paddleocr":
                ocr_result = self.ocr.extract(str(ip))
            ocr_text = ocr_result.get("text", "")

            # 4. Build attachment entry (only for image/both)
            if mode in ("image", "both"):
                attachment = {"hash": secrets.token_hex(4), "path": rel_img, "ocrText": ocr_text}

            # 5. Load or create note
            if fp.exists():
                raw = storage.read_file(fp)
                existing, raw_yaml = parse_note(raw)
                atts = existing.attachments or []

                if mode in ("image", "both") and attachment:
                    atts.append(attachment)
                    existing.attachments = atts
                    # Add markdown image reference to content (file-relative)
                    img_md = f"\n\n![screenshot]({md_img})"
                else:
                    img_md = ""

                if mode in ("text", "both") and ocr_text:
                    now_str = datetime.now().strftime("%H:%M")
                    new_content = f"{existing.content}\n\n---\n\n## {now_str}\n\n{ocr_text}{img_md}"
                elif img_md:
                    new_content = f"{existing.content}{img_md}"
                else:
                    new_content = existing.content

                merged = merge_front_matter(existing, new_content=new_content)
                if mode in ("image", "both"):
                    merged.attachments = atts
                else:
                    # text mode: preserve existing attachments unchanged
                    merged.attachments = existing.attachments

                storage.atomic_write(fp, dump_note(merged, raw_yaml=raw_yaml))
                note_id = merged.note_id

                # Update FTS5
                if self.search:
                    ocr_parts = [a.get("ocrText", "") for a in (merged.attachments or [])]
                    all_ocr = "\n".join([p for p in ocr_parts if p])
                    self.search.upsert(
                        doc_path=folder_path,
                        title=merged.title or Path(folder_path).stem,
                        content=merged.content,
                        ocr_text=all_ocr,
                        note_id=note_id,
                        mtime=fp.stat().st_mtime,
                    )
            else:
                # New file — create content
                img_md = ""
                if mode in ("image", "both") and attachment:
                    img_md = f"\n\n![screenshot]({md_img})"
                if mode in ("text", "both") and ocr_text:
                    now_str = datetime.now().strftime("%H:%M")
                    title = Path(folder_path).stem
                    content = f"# {title}\n\n## {now_str}\n\n{ocr_text}{img_md}"
                elif img_md:
                    content = f"# {Path(folder_path).stem}\n\n{img_md}"
                else:
                    content = f"# {Path(folder_path).stem}\n"
                note = make_note(content)
                if mode in ("image", "both") and attachment:
                    note.attachments = [attachment]
                storage.atomic_write(fp, dump_note(note))
                note_id = note.note_id

                if self.search:
                    ocr_parts = [a.get("ocrText", "") for a in (note.attachments or [])]
                    all_ocr = "\n".join([p for p in ocr_parts if p])
                    self.search.upsert(
                        doc_path=folder_path,
                        title=note.title or Path(folder_path).stem,
                        content=note.content,
                        ocr_text=all_ocr,
                        note_id=note_id,
                        mtime=fp.stat().st_mtime,
                    )

            return note_id, ocr_text

        note_id, ocr_text = await self.loop.run_in_executor(self.io_pool, _store)
        return {"noteId": note_id, "ocrText": ocr_text}

    async def _handle_clipboard_ingest_image(self, app, params) -> dict:
        # Same as ocr.store — delegates internally
        return await self._handle_ocr_store(app, params)


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    setup_logging()
    logger.info("=" * 60)
    logger.info("衔泥 NestTray backend starting — version %s", APP_VERSION)

    app = App()

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt")
    except Exception:
        logger.exception("Fatal unhandled exception")
        fatal = {
            "jsonrpc": "2.0",
            "method": "event.fatal",
            "params": {"reason": "未捕获的异常，请检查日志"},
        }
        sys.stdout.write(json.dumps(fatal, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        time.sleep(0.1)
        sys.exit(1)
    finally:
        logger.info("Backend exited")


if __name__ == "__main__":
    main()
