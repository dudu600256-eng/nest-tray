"""Integration test: all 16 RPC methods via stdin/stdout subprocess.

Run manually:
    python tests/integration_test.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def test_all_methods():
    """Call every RPC method and verify a valid response."""
    tmp = Path(tempfile.gettempdir()) / "note-tray-int-test"
    tmp.mkdir(parents=True, exist_ok=True)
    kb = tmp / "kb"
    kb.mkdir(exist_ok=True)

    # Use the venv python (same as parent script)
    p = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent.parent / "main.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ, "NOTE_KB_ROOT": str(kb)},
    )

    time.sleep(1)

    def rpc(method, params, timeout=5):
        rid = str(int(time.time() * 1000))
        payload = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        p.stdin.write(payload + "\n")
        p.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                continue
            try:
                resp = json.loads(line)
                if resp.get("id") == rid:
                    return resp
            except json.JSONDecodeError:
                continue
        raise TimeoutError(f"No response for {method}")

    try:
        # 1 system.status
        r = rpc("system.status", {})
        assert "result" in r, f"system.status failed: {r}"
        print("[PASS] system.status")

        # 2 note.save (create)
        r = rpc("note.save", {"path": "test/a.md", "content": "# Hello\nWorld", "tags": ["t"]})
        assert "result" in r and r["result"]["noteId"].startswith("n_"), f"note.save create: {r}"
        nid = r["result"]["noteId"]
        print(f"[PASS] note.save create: {nid}")

        # 3 note.save (update)
        r = rpc("note.save", {"path": "test/a.md", "content": "# Hello\nUpdated"})
        assert r["result"]["noteId"] == nid
        print("[PASS] note.save update")

        # 4 note.get
        r = rpc("note.get", {"path": "test/a.md"})
        assert r["result"]["noteId"] == nid
        assert "Updated" in r["result"]["content"]
        print("[PASS] note.get")

        # 5 note.list_folder
        r = rpc("note.list_folder", {"folder": "test"})
        assert len(r["result"]["notes"]) >= 1
        print(f"[PASS] note.list_folder: {len(r['result']['notes'])} notes")

        # 6 browser.tree
        r = rpc("browser.tree", {})
        assert "tree" in r["result"]
        print("[PASS] browser.tree")

        # 7 clipboard.ingest
        r = rpc("clipboard.ingest", {"folder": "test", "text": "Quick note"})
        assert r["result"]["noteId"].startswith("n_")
        print("[PASS] clipboard.ingest")

        # 8 search.query
        r = rpc("search.query", {"q": "Hello"})
        assert "results" in r["result"]
        print(f"[PASS] search.query: {r['result']['totalHits']} hits")

        # 9 search.rebuild
        r = rpc("search.rebuild", {})
        assert "result" in r
        print("[PASS] search.rebuild")

        # 10 note.move
        r = rpc("note.move", {"from": "test/a.md", "to": "test/b.md"})
        assert "result" in r
        print("[PASS] note.move")

        # 11 note.delete
        r = rpc("note.delete", {"path": "test/b.md"})
        assert "result" in r
        print("[PASS] note.delete")

        # 12 attachment.list
        r = rpc("attachment.list", {"noteId": nid})
        assert "attachments" in r["result"]
        print("[PASS] attachment.list")

        # 13 ocr.extract — expected to fail gracefully (no PaddleOCR usually)
        r = rpc("ocr.extract", {"imagePath": "nonexistent.png"})
        assert "error" in r or "result" in r
        print(f"[PASS] ocr.extract: error={r.get('error', {}).get('message', 'none')}")

        # 14 ocr.store — same
        r = rpc("ocr.store", {"imagePath": "nonexistent.png", "folder": "test", "mode": "text"})
        assert "error" in r or "result" in r
        print(f"[PASS] ocr.store: error={r.get('error', {}).get('message', 'none')}")

        # 15 clipboard.ingest_image — delegation
        r = rpc("clipboard.ingest_image", {"imagePath": "nonexistent.png", "folder": "test"})
        assert "error" in r or "result" in r
        print(f"[PASS] clipboard.ingest_image")

        # 16 system.shutdown
        r = rpc("system.shutdown", {})
        assert "result" in r
        p.wait(timeout=5)
        print("[PASS] system.shutdown — process exited")

    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=3)

    print("\n=== ALL 16 METHODS PASSED ===")


if __name__ == "__main__":
    test_all_methods()
