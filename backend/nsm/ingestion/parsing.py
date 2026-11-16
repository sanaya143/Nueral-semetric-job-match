"""Deterministic document parsing (no LLM): PDF, DOCX, TXT/MD, and images or scanned PDF pages via OCR.

Also detects hidden text (white or tiny fonts, off-page text), which is a common
vehicle for prompt injection in CVs. Hidden text is returned separately so the
sanitizer can drop it from the evaluable text and flag the document.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedDocument:
    text: str
    mime: str
    hidden_snippets: list[str] = field(default_factory=list)
    pages: int = 1
    ocr_pages: int = 0  # pages (or images) whose text came from OCR


class UnsupportedFormat(ValueError):
    pass


def _is_near_white(rgb: int) -> bool:
    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return min(r, g, b) >= 0xF0


def _is_too_light(rgb: int) -> bool:
    """Text so pale it is unreadable on a white page (e.g. #E0E0E0). Normal grey text (#999 and darker) passes."""
    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) >= 0xD9


_MIN_ALPHA = 64  # spans more transparent than ~25% opacity are treated as hidden


def _covered_spans(page) -> list[tuple[tuple[float, float, float, float], str]]:  # noqa: ANN001
    """Text painted over afterwards by an opaque filled shape (a box drawn on top of the text)."""
    fills = [(pymupdf_rect(d["rect"]), d.get("seqno", -1)) for d in page.get_drawings()
             if d.get("fill") is not None and (d.get("fill_opacity") or 1.0) >= 0.9]
    if not fills:
        return []
    out = []
    for t in page.get_texttrace():
        bbox, seq = t.get("bbox"), t.get("seqno", -1)
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox
        for (fx0, fy0, fx1, fy1), fseq in fills:
            if fseq > seq and fx0 <= x0 + 0.5 and fy0 <= y0 + 0.5 and fx1 >= x1 - 0.5 and fy1 >= y1 - 0.5:
                out.append((tuple(bbox), "".join(chr(c[0]) for c in t.get("chars", ()))))
                break
    return out


def pymupdf_rect(r) -> tuple[float, float, float, float]:  # noqa: ANN001
    return (r.x0, r.y0, r.x1, r.y1)


def _overlaps(a: tuple, b: tuple) -> bool:
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return False
    area = max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)
    return ix * iy / area >= 0.6


def _span_hidden(span: dict, rect, covered: list) -> bool:  # noqa: ANN001
    x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
    off_page = x1 < rect.x0 or x0 > rect.x1 or y1 < rect.y0 or y0 > rect.y1
    color = int(span.get("color", 0))
    if _is_near_white(color) or _is_too_light(color) or span.get("size", 10) < 4 or off_page:
        return True
    if int(span.get("alpha", 255)) < _MIN_ALPHA:  # invisible (render mode 3) or near-transparent text
        return True
    return any(_overlaps((x0, y0, x1, y1), bb) for bb, _ in covered)


def parse_pdf(data: bytes) -> ParsedDocument:
    import pymupdf

    from nsm.ingestion.ocr import MIN_TEXT_CHARS_PER_PAGE, ocr_pdf_pages

    hidden: list[str] = []
    page_lines: list[list[str]] = []
    scanned: list[int] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
        for page in doc:
            rect = page.rect
            covered = _covered_spans(page)
            lines_out: list[str] = []
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    visible_parts: list[str] = []
                    for span in line.get("spans", []):
                        txt = span.get("text", "")
                        if not txt.strip():
                            continue
                        if _span_hidden(span, rect, covered):
                            hidden.append(txt.strip())
                        else:
                            visible_parts.append(txt)
                    if visible_parts:
                        lines_out.append("".join(visible_parts).rstrip())
            page_lines.append(lines_out)
            # A page with (almost) no real text but with a picture on it is a scan: read it with OCR.
            if len("".join(lines_out).strip()) < MIN_TEXT_CHARS_PER_PAGE and page.get_images(full=False):
                scanned.append(page.number)
        ocr_text = ocr_pdf_pages(doc, scanned)
    for n, txt in ocr_text.items():
        page_lines[n] = [ln.rstrip() for ln in txt.splitlines()]
    text = "\n\n".join("\n".join(lines) for lines in page_lines)
    return ParsedDocument(text=text.strip() + "\n", mime="application/pdf", hidden_snippets=hidden, pages=pages,
                          ocr_pages=sum(1 for t in ocr_text.values() if t.strip()))


def parse_image(filename: str, data: bytes) -> ParsedDocument:
    from nsm.ingestion.ocr import OcrUnavailable, engine_name, ocr_image

    if engine_name() == "off":
        raise UnsupportedFormat("Image CVs need OCR, which is off (see NSM_OCR_ENGINE in .env.example)")
    ext = Path(filename).suffix.lower().lstrip(".")
    ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "tif": "image/tiff", "tiff": "image/tiff"}.get(ext, f"image/{ext}")
    try:
        text = ocr_image(data, ctype)
    except OcrUnavailable as e:
        raise UnsupportedFormat(str(e)) from e
    return ParsedDocument(text=text.strip() + "\n", mime=ctype, pages=1, ocr_pages=1 if text.strip() else 0)


def parse_docx(data: bytes) -> ParsedDocument:
    import docx

    document = docx.Document(io.BytesIO(data))
    hidden: list[str] = []
    lines: list[str] = []

    def para_text(p) -> str:  # noqa: ANN001
        parts: list[str] = []
        for run in p.runs:
            color = run.font.color.rgb if run.font.color is not None and run.font.color.type is not None else None
            is_white = color is not None and _is_too_light((color[0] << 16) | (color[1] << 8) | color[2])
            tiny = run.font.size is not None and run.font.size.pt < 4
            if run.font.hidden or is_white or tiny:
                if run.text.strip():
                    hidden.append(run.text.strip())
            else:
                parts.append(run.text)
        text = "".join(parts)
        style = (p.style.name or "").lower() if p.style is not None else ""
        if "list" in style and text.strip() and not text.lstrip().startswith(("-", "•", "*")):
            text = "- " + text
        return text

    for p in document.paragraphs:
        lines.append(para_text(p))
    for table in document.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                t = " ".join(para_text(p).strip() for p in cell.paragraphs).strip()
                if t:
                    cells.append(t)
            lines.append(" | ".join(cells))
    return ParsedDocument(text="\n".join(lines).strip() + "\n",
                          mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          hidden_snippets=hidden)


def parse_text(data: bytes) -> ParsedDocument:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return ParsedDocument(text=data.decode(enc), mime="text/plain")
        except UnicodeDecodeError:
            continue
    return ParsedDocument(text=data.decode("utf-8", errors="replace"), mime="text/plain")


def parse_document(filename: str, data: bytes) -> ParsedDocument:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        return parse_pdf(data)
    if ext == ".docx":
        return parse_docx(data)
    if ext in (".txt", ".md", ".text", ""):
        return parse_text(data)
    from nsm.ingestion.ocr import IMAGE_EXT

    if ext in IMAGE_EXT:
        return parse_image(filename, data)
    raise UnsupportedFormat(f"Unsupported file type: {ext or 'unknown'} (use PDF, DOCX, TXT or an image)")
