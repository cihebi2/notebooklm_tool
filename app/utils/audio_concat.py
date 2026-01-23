from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConcatResult:
    output_path: Path
    method: str  # "ffmpeg_concat_filter"


def concat_audio(parts: list[Path], output_path: Path, output_format: str = "mp3") -> ConcatResult:
    """Concatenate audio parts into one file using ffmpeg (re-encode).

    This uses the `concat` filter (not the concat demuxer) to avoid Windows
    filelist encoding issues on non-ASCII paths.
    """
    if len(parts) < 2:
        raise ValueError("Need at least 2 parts to concatenate")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH (required for stitching)")

    output_format = (output_format or "").strip().lower()
    if output_format not in {"mp3", "mp4"}:
        raise ValueError("split_output_format must be mp3 or mp4")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build: [0:a][1:a]...[n:a]concat=n=N:v=0:a=1[outa]
    filter_in = "".join(f"[{i}:a]" for i in range(len(parts)))
    filter_complex = f"{filter_in}concat=n={len(parts)}:v=0:a=1[outa]"

    cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for p in parts:
        cmd.extend(["-i", str(p)])

    cmd.extend(["-filter_complex", filter_complex, "-map", "[outa]", "-vn"])
    if output_format == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k", str(output_path)])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", str(output_path)])

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg stitch failed: {err}")

    return ConcatResult(output_path=output_path, method="ffmpeg_concat_filter")

