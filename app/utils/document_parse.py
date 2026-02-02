from __future__ import annotations

import io
from typing import Iterable

from pypdf import PdfReader
from docx import Document


def _decode_text(data: bytes, encodings: Iterable[str] | None = None) -> str:
    encs = list(encodings or ["utf-8", "utf-8-sig", "gbk", "gb2312", "big5"])
    for enc in encs:
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text_from_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def extract_text_from_docx(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    parts: list[str] = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def extract_text_from_bytes(data: bytes, ext: str) -> str:
    ext = ext.lower().strip()
    if ext in {".txt", ".md", ".text"}:
        return _decode_text(data).strip()
    if ext == ".pdf":
        return extract_text_from_pdf(data)
    if ext == ".docx":
        return extract_text_from_docx(data)
    raise ValueError(f"unsupported extension: {ext}")
