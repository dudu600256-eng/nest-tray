"""
Note Tray — Logging Configuration
===================================
Rotating file handler to %APPDATA%/note-tray/logs/sidecar.log,
plus stderr output for Tauri sidecar consumption.
"""

import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

APP_DATA = os.environ.get("APPDATA", os.path.expanduser("~"))
LOG_DIR = Path(APP_DATA) / "note-tray" / "logs"
LOG_FILE = LOG_DIR / "sidecar.log"
LOG_LEVEL = logging.DEBUG if os.environ.get("NOTE_DEBUG") else logging.INFO
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5
LOG_RETENTION_DAYS = 30


def _cleanup_old_logs():
    """Remove rotated log files older than LOG_RETENTION_DAYS."""
    if not LOG_DIR.exists():
        return
    now = time.time()
    cutoff = now - LOG_RETENTION_DAYS * 86400
    for p in LOG_DIR.glob("sidecar.*.log"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs()

    logger = logging.getLogger("sidecar")
    logger.setLevel(LOG_LEVEL)
    logger.handlers.clear()

    # Rotating file handler
    fh = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(LOG_LEVEL)

    # Stderr handler (Tauri reads this from the sidecar stderr pipe)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(LOG_LEVEL)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] sidecar.%(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fh.setFormatter(formatter)
    sh.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(sh)

    logger.info("Logging initialized: %s (level=%s)", LOG_FILE, logging.getLevelName(LOG_LEVEL))
