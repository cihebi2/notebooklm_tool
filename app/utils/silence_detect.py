from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
import os
from pathlib import Path

SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>[0-9.]+)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<dur>[0-9.]+)"
)


@dataclass(frozen=True)
class SilenceSegment:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


def format_hhmmss(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _run_silencedetect(
    audio_path: Path, *, min_duration_s: float, threshold_db: float
) -> str:
    args = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio_path),
        "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_duration_s}",
        "-f",
        "null",
        "NUL" if os.name == "nt" else "/dev/null",
    ]

    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    return (proc.stderr or "") + ("\n" + proc.stdout if proc.stdout else "")


def _parse_segments(log_text: str) -> list[SilenceSegment]:
    segments: list[SilenceSegment] = []
    current_start: float | None = None

    for line in log_text.splitlines():
        m_start = SILENCE_START_RE.search(line)
        if m_start:
            current_start = float(m_start.group("start"))
            continue

        m_end = SILENCE_END_RE.search(line)
        if m_end:
            end = float(m_end.group("end"))
            if current_start is None:
                continue
            segments.append(SilenceSegment(start_s=current_start, end_s=end))
            current_start = None

    return segments


def detect_silence_segments(
    audio_path: Path, *, min_duration_s: float, threshold_db: float
) -> list[SilenceSegment]:
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))
    log_text = _run_silencedetect(
        audio_path, min_duration_s=min_duration_s, threshold_db=threshold_db
    )
    return _parse_segments(log_text)


def segments_to_payload(segments: list[SilenceSegment]) -> list[dict[str, float | str]]:
    payload: list[dict[str, float | str]] = []
    for seg in segments:
        payload.append(
            {
                "start_s": round(float(seg.start_s), 3),
                "end_s": round(float(seg.end_s), 3),
                "duration_s": round(float(seg.duration_s), 3),
                "start_hhmmss": format_hhmmss(seg.start_s),
                "end_hhmmss": format_hhmmss(seg.end_s),
            }
        )
    return payload
