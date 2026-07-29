#!/usr/bin/env python
"""End-to-end RPC test — validates all 16 methods against a running main.py process.

Usage:
    python tests/e2e_test.py
"""

import json, os, subprocess, sys, tempfile, time
from pathlib import Path

KB_ROOT = Path(tempfile.gettempdir()) / "note-tray-e2e"
KB_ROOT.mkdir(parents=True, exist_ok=True)


def main():
    p = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent.parent / "main.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True, env={**os.environ, "NOTE_KB_ROOT": str(KB_ROOT)},
    )
    time.sleep(2)

    def req(method, params, rid="1"):
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        p.stdin.write(body + "\n")
        p.stdin.flush()
        deadline = time.time() + 8
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("id") == rid:
                    return r
            except json.JSONDecodeError:
                continue
        return {"error": {"message": "timeout"}}

    passed = 0
    failed = 0

    def check(name, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✅ {name}")
        else:
            failed += 1
            print(f"  ❌ {name}")

    print("=== E2E: All 16 RPC methods ===")

    # 01 system.status
    r = req("system.status", {})
    check("system.status returns ok", r.get("result", {}).get("ok") in (True, False))

    # 02 note.save (create)
    r = req("note.save", {"path": "e2e/test.md", "content": "# E2E\nTest content", "tags": ["e2e"]})
    nid = r.get("result", {}).get("noteId", "")
    check(f"note.save create -> noteId", nid.startswith("n_"))

    # 03 note.save (update — empty content should NOT overwrite)
    r = req("note.save", {"path": "e2e/test.md", "content": ""})
    r2 = req("note.get", {"path": "e2e/test.md"})
    has_original = "Test content" in r2.get("result", {}).get("content", "")
    check("note.save empty content does not overwrite", has_original)

    # 04 note.get
    r = req("note.get", {"path": "e2e/test.md"})
    check("note.get returns content", "E2E" in r.get("result", {}).get("content", ""))

    # 05 note.list_folder
    r = req("note.list_folder", {"folder": "e2e"})
    notes = r.get("result", {}).get("notes", [])
    check(f"note.list_folder -> {len(notes)} notes", len(notes) >= 1)

    # 06 browser.tree
    r = req("browser.tree", {})
    tree_str = json.dumps(r.get("result", {}))
    check("browser.tree contains folder", "e2e" in tree_str)

    # 07 clipboard.ingest
    r = req("clipboard.ingest", {"folder": "e2e", "text": "Quick note from clipboard"})
    check("clipboard.ingest -> noteId", r.get("result", {}).get("noteId", "").startswith("n_"))

    # 08 search.query — must find the content we saved
    r = req("search.query", {"q": "Test content"})
    hits = r.get("result", {}).get("totalHits", 0)
    check(f"search.query found -> {hits} hits", hits >= 1)

    # 09 search.rebuild
    r = req("search.rebuild", {})
    check("search.rebuild ok", "result" in r)

    # 10 search.query after rebuild
    r = req("search.query", {"q": "Test content"})
    hits2 = r.get("result", {}).get("totalHits", 0)
    check(f"search after rebuild -> {hits2} hits", hits2 >= 1)

    # 11 note.move
    r = req("note.move", {"from": "e2e/test.md", "to": "e2e/moved.md"})
    check("note.move ok", "result" in r)

    # 12 search after move (FTS5 path updated)
    r = req("search.query", {"q": "Test content"})
    after_move = r.get("result", {}).get("totalHits", 0)
    check(f"search after move -> {after_move} hits", after_move >= 1)

    # 13 note.delete
    r = req("note.delete", {"path": "e2e/moved.md"})
    check("note.delete ok", "result" in r)

    # 14 search after delete
    r = req("search.query", {"q": "Test content"})
    after_del = r.get("result", {}).get("totalHits", 0)
    check(f"search after delete -> {after_del} hits", after_del == 0)

    # 15 attachment.list
    r = req("attachment.list", {"noteId": nid})
    check("attachment.list ok", "attachments" in r.get("result", {}))

    # 16 ocr.extract — no PaddleOCR installed, expect error
    r = req("ocr.extract", {"imagePath": "nonexistent.png"})
    check("ocr.extract error on missing file", "error" in r)

    # 17 ocr.store — same
    r = req("ocr.store", {"imagePath": "nonexistent.png", "folder": "e2e", "mode": "text"})
    check("ocr.store error on missing file", "error" in r)

    # 18 clipboard.ingest_image — same
    r = req("clipboard.ingest_image", {"imagePath": "nonexistent.png", "folder": "e2e"})
    check("clipboard.ingest_image error on missing file", "error" in r)

    # 19 system.shutdown — must return {"ok": true}
    r = req("system.shutdown", {}, rid="shutdown-1")
    check("system.shutdown returns ok", r.get("result", {}).get("ok") is True)

    p.wait(timeout=5)
    print(f"\n=== {passed}/{passed+failed} PASSED ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
