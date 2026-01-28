from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SilenceSegment:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<start>[0-9.]+)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<dur>[0-9.]+)"
)


def _null_sink() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def _format_hhmmss(seconds: float) -> str:
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
        _null_sink(),
    ]

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "找不到 ffmpeg：请先安装 ffmpeg 并确保在 PATH 中可用。"
        ) from exc

    # ffmpeg 把日志写到 stderr；也可能混有 stdout，合并即可。
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
                # 极少数情况下可能只看到 end；忽略。
                continue
            segments.append(SilenceSegment(start_s=current_start, end_s=end))
            current_start = None

    return segments


def _iter_print_lines(
    segments: Iterable[SilenceSegment], *, threshold_db: float, min_duration_s: float
) -> Iterable[str]:
    segments = list(segments)
    yield f"Detected {len(segments)} silent segments (<= {threshold_db} dB, >= {min_duration_s} s):"
    for i, seg in enumerate(segments, start=1):
        yield (
            f"{i:>2}. {_format_hhmmss(seg.start_s)} -> {_format_hhmmss(seg.end_s)}"
            f"  (duration={seg.duration_s:.3f}s)"
        )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Detect silent segments in an audio file using ffmpeg silencedetect.",
    )
    parser.add_argument("audio", type=Path, help="Path to audio file (e.g. .mp3)")
    parser.add_argument(
        "--min-duration",
        type=float,
        default=10.0,
        help="Minimum silence duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=-50.0,
        help="Silence threshold in dB (default: -50)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output segments as JSON to stdout",
    )
    args = parser.parse_args(argv)

    if not args.audio.exists():
        print(f"文件不存在：{args.audio}", file=sys.stderr)
        return 2

    log_text = _run_silencedetect(
        args.audio, min_duration_s=args.min_duration, threshold_db=args.threshold
    )
    segments = _parse_segments(log_text)

    if args.json:
        payload = [
            {
                "start_s": seg.start_s,
                "end_s": seg.end_s,
                "duration_s": seg.duration_s,
                "start_hhmmss": _format_hhmmss(seg.start_s),
                "end_hhmmss": _format_hhmmss(seg.end_s),
            }
            for seg in segments
        ]
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    for line in _iter_print_lines(
        segments, threshold_db=args.threshold, min_duration_s=args.min_duration
    ):
        print(line)

    if not segments:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

