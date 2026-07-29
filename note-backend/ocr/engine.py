"""
Note Tray — OCR Engine (PaddleOCR)
=====================================
Lazy-loaded on a background thread at startup.

Lifecycle:
  App.__init__ → OcrEngine() → _start_preload()
    → "loading" → background thread loads PaddleOCR
    → "paddleocr" (success) or "unavailable" (failure)
    → event.ocr_ready emitted via asyncio bridge

Confidence threshold: results < 0.5 are dropped.

Note: First-run downloads ~200MB of models to the paddlex cache directory.
Subsequent startups are much faster with cached models.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Speed up model loading by skipping remote source check
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "1")
# Redirect PaddlePaddle debug output to stderr (avoids corrupting JSON-RPC stdout)
os.environ.setdefault("GLOG_logtostderr", "1")
os.environ["FLAGS_logtostderr"] = "1"
os.environ["GLOG_logtostderr"] = "1"
# Suppress oneDNN verbose output
os.environ["DNNL_VERBOSE"] = "0"

logger = logging.getLogger("sidecar.ocr")


class OcrEngine:
    """Manages PaddleOCR (ONNX) lifecycle."""

    def __init__(self, loop=None, send_event=None, lang: str = "ch"):
        self.state = "loading"  # "loading" | "paddleocr" | "unavailable"
        self._engine = None
        self._loop = loop
        self._send_event = send_event
        self._lang = lang
        self._cpu_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
        self._start_preload()

    def _start_preload(self):
        """Launch background preload thread (non-daemon for safe shutdown)."""
        t = threading.Thread(target=self._preload_worker, name="ocr-preload")
        t.start()

    def _preload_worker(self):
        """Load PaddleOCR models in background (first-run downloads models)."""
        try:
            logger.info("OCR preload: starting PaddleOCR…")
            from paddleocr import PaddleOCR
            self._engine = PaddleOCR(lang=self._lang)
            self.state = "paddleocr"
            logger.info("OCR preload: PaddleOCR ready")
            self._notify_ready()
        except ImportError:
            logger.warning("OCR preload: paddleocr not installed — OCR unavailable")
            self.state = "unavailable"
        except Exception as e:
            logger.warning("OCR preload failed: %s", e)
            self.state = "unavailable"

    def _notify_ready(self):
        """Push event.ocr_ready notification (runs in background thread, writes directly to stdout)."""
        if self._send_event:
            self._send_event("event.ocr_ready", {"ocrEngine": "paddleocr", "ready": True})

    # ── Extract ──────────────────────────────────────────────────────────

    def extract(self, image_path: str) -> dict:
        """Run OCR on an image file using PaddleOCR 3.7+ API.

        Returns:
            dict with keys: text, confidence, engine, timeMs
        Raises:
            RuntimeError if engine is not ready.
        """
        if self.state != "paddleocr":
            raise RuntimeError("OCR 引擎未就绪")

        t0 = time.time()
        try:
            raw_result = self._engine.ocr(image_path)
            elapsed = int((time.time() - t0) * 1000)

            lines = []
            best_conf = 0.0

            if raw_result is not None:
                # PaddleOCR 3.7 returns list[OCRResult] (dict-like access)
                if isinstance(raw_result, list) and len(raw_result) > 0:
                    for page_result in raw_result:
                        if page_result is None:
                            continue
                        try:
                            texts = page_result['rec_texts'] or []
                            scores = page_result['rec_scores'] or [0.0] * len(texts)
                            for text, conf in zip(texts, scores):
                                if conf >= 0.5 and text and text.strip():
                                    lines.append(text.strip())
                                    best_conf = max(best_conf, conf)
                        except (KeyError, TypeError):
                            pass
                # Legacy format: list of list of (bbox, (text, conf))
                elif isinstance(raw_result, list):
                    for page in raw_result:
                        if page is None:
                            continue
                        for item in page:
                            if not isinstance(item, (list, tuple)) or len(item) < 2:
                                continue
                            text_data = item[1]
                            if isinstance(text_data, (list, tuple)):
                                text, conf = text_data[0], text_data[1]
                            else:
                                text, conf = str(text_data), 0.0
                            if conf >= 0.5:
                                lines.append(text)
                                best_conf = max(best_conf, conf)

            full_text = "\n".join(lines)
            return {
                "text": full_text,
                "confidence": round(best_conf, 2) if lines else 0.0,
                "engine": "paddleocr",
                "timeMs": elapsed,
            }
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)
            logger.error("OCR extract failed: %s", e)
            raise RuntimeError(f"OCR 识别失败: {e}") from e
