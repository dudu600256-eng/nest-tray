#!/usr/bin/env python
"""Smoke test — runs main.py as a subprocess and exercises all core handlers."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

def main():
    tmp = Path(tempfile.gettempdir()) / f"note-tray-smoke-{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    kb = tmp / "kb"
    kb.mkdir(exist_ok=True)

    p = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "main.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "NOTE_KB_ROOT": str(kb)},
    )
    time.sleep(2.0)

    def req(method, params, rid=None):
        rid = rid or str(int(time.time() * 1000) % 10**6)
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        p.stdin.write(body + "\n")
        p.stdin.flush()
        deadline = time.time() + 8
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                continue
            try:
                resp = json.loads(line)
                if resp.get("id") == rid:
                    return resp
                # Could be a stray response — ignore
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

    print("=== SMOKE TEST ===")

    # 1. handshake + status
    r = req("system.status", {})
    check("handshake -> status.ok", r.get("result", {}).get("ok") is True)

    # 2. note.save create
    r = req("note.save", {"path": "test/a.md", "content": "# A\nHello", "tags": ["t"]})
    nid = r.get("result", {}).get("noteId", "")
    check("note.save create", nid.startswith("n_"))

    # 3. note.save update
    r = req("note.save", {"path": "test/a.md", "content": "# A\nWorld"})
    check("note.save update", r.get("result", {}).get("noteId") == nid)

    # 4. note.get
    r = req("note.get", {"path": "test/a.md"})
    check("note.get", "World" in r.get("result", {}).get("content", ""))

    # 5. clipboard.ingest
    r = req("clipboard.ingest", {"folder": "test", "text": "Quick memo"})
    check("clipboard.ingest", r.get("result", {}).get("noteId", "").startswith("n_"))

    # 6. search.query
    r = req("search.query", {"q": "World"})
    check("search.query", r.get("result", {}).get("totalHits", 0) >= 1)

    # 7. note.list_folder
    r = req("note.list_folder", {"folder": "test"})
    check("note.list_folder", len(r.get("result", {}).get("notes", [])) >= 1)

    # 8. browser.tree
    r = req("browser.tree", {})
    check("browser.tree", "test" in json.dumps(r.get("result", {})))

    # 9. note.move
    r = req("note.move", {"from": "test/a.md", "to": "test/b.md"})
    check("note.move", "result" in r)

    # 10. search after move (FTS5 path updated)
    r = req("search.query", {"q": "World"})
    check("search after move", r.get("result", {}).get("totalHits", 0) >= 1)

    # 11. note.delete
    r = req("note.delete", {"path": "test/b.md"})
    check("note.delete", "result" in r)

    # 12. search after delete
    r = req("search.query", {"q": "World"})
    check("search after delete", r.get("result", {}).get("totalHits", 0) == 0)

    # 13. shutdown with response
    r = req("system.shutdown", {}, rid="zzz99")
    check("shutdown returns ok", r.get("result", {}).get("ok") is True)

    p.wait(timeout=5)
    print(f"\n=== {passed}/{passed + failed} PASSED ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
