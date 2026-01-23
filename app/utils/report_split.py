from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SplitPart:
    index: int
    total: int
    text: str
    item_start: int | None
    item_end: int | None
    item_count: int | None


@dataclass(frozen=True)
class SplitPlan:
    parts: list[SplitPart]
    detected_items: int | None
    method: str  # "bracket_numbers" | "numbered_lines" | "paragraphs"


def _normalize(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text


def _distribute(total: int, parts: int) -> list[int]:
    if parts <= 0:
        raise ValueError("parts must be >= 1")
    if total <= 0:
        return [0] * parts
    base = total // parts
    rem = total % parts
    return [base + (1 if i < rem else 0) for i in range(parts)]


def split_report(report_text: str, parts: int, include_prefix: bool = True) -> SplitPlan:
    """Split a report into multiple parts, best-effort by item markers.

    Preferred formats:
    - [01] ... [30]  (common in morning-news style reports)
    - 1. / 1、 style numbered lines

    Falls back to splitting by paragraphs evenly.
    """
    text = _normalize(report_text)
    if not text:
        raise ValueError("report_text is empty")
    if parts < 2:
        raise ValueError("parts must be >= 2")

    # 1) [01] style markers
    bracket_re = re.compile(r"(?m)^\s*\[(\d{1,3})\]\s+")
    bracket_matches = list(bracket_re.finditer(text))
    if len(bracket_matches) >= 2:
        prefix = text[: bracket_matches[0].start()].strip()
        items: list[str] = []
        nums: list[int] = []
        for idx, m in enumerate(bracket_matches):
            start = m.start()
            end = bracket_matches[idx + 1].start() if idx + 1 < len(bracket_matches) else len(text)
            block = text[start:end].strip()
            if block:
                items.append(block)
                try:
                    nums.append(int(m.group(1)))
                except Exception:
                    nums.append(idx + 1)

        sizes = _distribute(len(items), parts)
        out: list[SplitPart] = []
        pos = 0
        for i, size in enumerate(sizes, start=1):
            if size <= 0:
                continue
            chunk_items = items[pos : pos + size]
            chunk_nums = nums[pos : pos + size]
            pos += size

            joined = "\n\n".join(chunk_items).strip()
            if include_prefix and prefix:
                joined = f"{prefix}\n\n{joined}".strip()

            out.append(
                SplitPart(
                    index=i,
                    total=parts,
                    text=joined,
                    item_start=chunk_nums[0] if chunk_nums else None,
                    item_end=chunk_nums[-1] if chunk_nums else None,
                    item_count=len(chunk_items),
                )
            )

        return SplitPlan(parts=out, detected_items=len(items), method="bracket_numbers")

    # 2) 1. / 1、 numbered lines
    numbered_re = re.compile(r"(?m)^\s*(\d{1,3})[\.、]\s+")
    numbered_matches = list(numbered_re.finditer(text))
    if len(numbered_matches) >= 2:
        prefix = text[: numbered_matches[0].start()].strip()
        items = []
        nums = []
        for idx, m in enumerate(numbered_matches):
            start = m.start()
            end = numbered_matches[idx + 1].start() if idx + 1 < len(numbered_matches) else len(text)
            block = text[start:end].strip()
            if block:
                items.append(block)
                try:
                    nums.append(int(m.group(1)))
                except Exception:
                    nums.append(idx + 1)

        sizes = _distribute(len(items), parts)
        out: list[SplitPart] = []
        pos = 0
        for i, size in enumerate(sizes, start=1):
            if size <= 0:
                continue
            chunk_items = items[pos : pos + size]
            chunk_nums = nums[pos : pos + size]
            pos += size

            joined = "\n\n".join(chunk_items).strip()
            if include_prefix and prefix:
                joined = f"{prefix}\n\n{joined}".strip()

            out.append(
                SplitPart(
                    index=i,
                    total=parts,
                    text=joined,
                    item_start=chunk_nums[0] if chunk_nums else None,
                    item_end=chunk_nums[-1] if chunk_nums else None,
                    item_count=len(chunk_items),
                )
            )

        return SplitPlan(parts=out, detected_items=len(items), method="numbered_lines")

    # 3) Fallback: split by paragraphs with approximate equal length
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        # No double-newline paragraphs, split raw by length.
        step = math.ceil(len(text) / parts)
        out = []
        for i in range(parts):
            chunk = text[i * step : (i + 1) * step].strip()
            if chunk:
                out.append(SplitPart(index=i + 1, total=parts, text=chunk, item_start=None, item_end=None, item_count=None))
        return SplitPlan(parts=out, detected_items=None, method="paragraphs")

    target_len = len(text) / parts
    out: list[SplitPart] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len, out
        if not buf:
            return
        chunk = "\n\n".join(buf).strip()
        out.append(
            SplitPart(
                index=len(out) + 1,
                total=parts,
                text=chunk,
                item_start=None,
                item_end=None,
                item_count=None,
            )
        )
        buf = []
        buf_len = 0

    for para in paragraphs:
        # Keep at least one paragraph per part
        if buf and (buf_len + len(para) > target_len) and (len(out) < parts - 1):
            flush()
        buf.append(para)
        buf_len += len(para) + 2

    flush()

    # Ensure total count matches requested parts (best-effort).
    if len(out) > parts:
        out = out[:parts]
    for idx, part in enumerate(out, start=1):
        out[idx - 1] = SplitPart(
            index=idx,
            total=parts,
            text=part.text,
            item_start=None,
            item_end=None,
            item_count=None,
        )

    return SplitPlan(parts=out, detected_items=None, method="paragraphs")

