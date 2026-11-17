"""OCR for scanned PDFs and photographed / image CVs.

Engines (setting NSM_OCR_ENGINE):
- auto (default): the local engine if it is installed, otherwise Azure if it is configured, otherwise off.
- local: RapidOCR (ONNX on CPU). Its models ship inside the Python package, so it works offline and costs $0.
- azure: Azure AI Document Intelligence, "prebuilt-read" model, over REST
  (NSM_AZURE_DOCINTEL_ENDPOINT and NSM_AZURE_DOCINTEL_KEY). Use a resource in your own region.
- off: no OCR; scanned files fail with a clear message.

OCR only reads what is visibly printed on the page, so white or hidden text is never picked up by it.
The text still goes through the same sanitizer, PII redaction and extraction as any other CV.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache

from nsm.config import get_settings

log = logging.getLogger("nsm.ocr")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
OCR_DPI = 200
MIN_TEXT_CHARS_PER_PAGE = 25  # a page with less visible text than this is treated as scanned


class OcrUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _local_engine():  # noqa: ANN202
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:  # noqa: BLE001  (not installed, or a native library is missing)
        return None
    try:
        return RapidOCR()
    except Exception as e:  # noqa: BLE001
        log.warning("local OCR engine failed to load: %s", e)
        return None


def _azure_configured() -> bool:
    s = get_settings()
    return bool(s.azure_docintel_endpoint and s.azure_docintel_key)


def engine_name() -> str:
    """The engine that will actually be used: 'local', 'azure' or 'off'."""
    want = get_settings().ocr_engine.lower()
    if want == "off":
        return "off"
    if want == "local":
        return "local" if _local_engine() is not None else "off"
    if want == "azure":
        return "azure" if _azure_configured() else "off"
    if _local_engine() is not None:
        return "local"
    return "azure" if _azure_configured() else "off"


def status() -> dict:
    """For the readiness check."""
    want = get_settings().ocr_engine.lower()
    name = engine_name()
    ok = name != "off" or want in ("off", "auto")
    return {"ok": ok, "engine": name, "requested": want}


# ---------------------------------------------------------------------------------------
# local engine
# ---------------------------------------------------------------------------------------

def _lines_from_boxes(result: list) -> str:  # noqa: ANN001
    """RapidOCR returns [box, text, score] per word group. Rebuild reading order line by line."""
    items = []
    for box, txt, score in result or []:
        if not txt or float(score) < 0.5:
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append((min(ys), max(ys), min(xs), txt))
    items.sort(key=lambda r: (r[0], r[2]))
    lines: list[list[tuple[float, str]]] = []
    bounds: list[tuple[float, float]] = []
    for top, bottom, left, txt in items:
        mid = (top + bottom) / 2
        for i, (lt, lb) in enumerate(bounds):
            if lt <= mid <= lb:
                lines[i].append((left, txt))
                break
        else:
            lines.append([(left, txt)])
            bounds.append((top, bottom))
    return "\n".join(" ".join(t for _, t in sorted(line)) for line in lines)


def _ocr_local(image_bytes: bytes) -> str:
    eng = _local_engine()
    if eng is None:
        raise OcrUnavailable("local OCR engine is not installed")
    result, _ = eng(image_bytes)
    return _lines_from_boxes(result)


# ---------------------------------------------------------------------------------------
# Azure AI Document Intelligence (prebuilt-read)
# ---------------------------------------------------------------------------------------

def _ocr_azure(data: bytes, content_type: str, client=None) -> str:  # noqa: ANN001
    import httpx

    s = get_settings()
    if not _azure_configured():
        raise OcrUnavailable("Azure Document Intelligence is not configured")
    url = (f"{s.azure_docintel_endpoint.rstrip('/')}/documentintelligence/documentModels/prebuilt-read:analyze"
           f"?api-version={s.azure_docintel_api_version}")
    headers = {"Ocp-Apim-Subscription-Key": s.azure_docintel_key, "Content-Type": content_type}
    own = client is None
    http = client or httpx.Client(timeout=30)
    try:
        r = http.post(url, content=data, headers=headers)
        if r.status_code != 202:
            raise OcrUnavailable(f"Azure OCR refused the file (HTTP {r.status_code})")
        op = r.headers["operation-location"]
        deadline = time.monotonic() + s.ocr_timeout_seconds
        while time.monotonic() < deadline:
            res = http.get(op, headers={"Ocp-Apim-Subscription-Key": s.azure_docintel_key})
            body = res.json()
            st = body.get("status")
            if st == "succeeded":
                return (body.get("analyzeResult") or {}).get("content", "")
            if st == "failed":
                raise OcrUnavailable("Azure OCR failed on this file")
            time.sleep(1.0)
        raise OcrUnavailable("Azure OCR timed out")
    finally:
        if own:
            http.close()


# ---------------------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------------------

def ocr_image(image_bytes: bytes, content_type: str = "image/png") -> str:
    name = engine_name()
    if name == "local":
        return _ocr_local(image_bytes)
    if name == "azure":
        return _ocr_azure(image_bytes, content_type)
    raise OcrUnavailable("OCR is off (set NSM_OCR_ENGINE, see .env.example)")


def ocr_pdf_pages(doc, page_numbers: list[int]) -> dict[int, str]:  # noqa: ANN001
    """OCR selected pages of an open pymupdf document. Returns {page_number: text}."""
    if not page_numbers or engine_name() == "off":
        return {}
    limit = get_settings().ocr_max_pages
    out: dict[int, str] = {}
    for n in page_numbers[:limit]:
        png = doc[n].get_pixmap(dpi=OCR_DPI).tobytes("png")
        try:
            out[n] = ocr_image(png, "image/png")
        except OcrUnavailable as e:
            log.warning("OCR failed on page %s: %s", n + 1, e)
    return out
