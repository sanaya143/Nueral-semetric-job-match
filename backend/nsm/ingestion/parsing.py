"""Deterministic document parsing (no LLM): PDF, DOCX, TXT/MD.

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


class UnsupportedFormat(ValueError):
    pass


def _is_near_white(rgb: int) -> bool:
    r, g, b = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return min(r, g, b) >= 0xF0


def parse_pdf(data: bytes) -> ParsedDocument:
    import pymupdf

    hidden: list[str] = []
    lines_out: list[str] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
        for page in doc:
            rect = page.rect
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    visible_parts: list[str] = []
                    for span in line.get("spans", []):
                        txt = span.get("text", "")
                        if not txt.strip():
                            continue
                        x0, y0, x1, y1 = span.get("bbox", (0, 0, 0, 0))
                        off_page = x1 < rect.x0 or x0 > rect.x1 or y1 < rect.y0 or y0 > rect.y1
                        if _is_near_white(int(span.get("color", 0))) or span.get("size", 10) < 4 or off_page:
                            hidden.append(txt.strip())
                        else:
                            visible_parts.append(txt)
                    if visible_parts:
                        lines_out.append("".join(visible_parts).rstrip())
            lines_out.append("")
    return ParsedDocument(text="\n".join(lines_out).strip() + "\n", mime="application/pdf",
                          hidden_snippets=hidden, pages=pages)


def parse_docx(data: bytes) -> ParsedDocument:
    import docx
    from docx.shared import RGBColor

    document = docx.Document(io.BytesIO(data))
    hidden: list[str] = []
    lines: list[str] = []

    def para_text(p) -> str:  # noqa: ANN001
        parts: list[str] = []
        for run in p.runs:
            color = run.font.color.rgb if run.font.color is not None and run.font.color.type is not None else None
            is_white = color is not None and color == RGBColor(0xFF, 0xFF, 0xFF)
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
            lines.append(" | ".join(c.text.strip() for c in row.cells if c.text.strip()))
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
    raise UnsupportedFormat(f"Unsupported file type: {ext or 'unknown'} (use PDF, DOCX or TXT)")
