from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .audio_duration import get_audio_duration


@dataclass(frozen=True)
class ConcatResult:
    output_path: Path
    method: str  # "ffmpeg_concat_filter" | "ffmpeg_transcode" | "ffmpeg_concat_transition"


def concat_audio(parts: list[Path], output_path: Path, output_format: str = "mp3") -> ConcatResult:
    """Concatenate audio parts into one file using ffmpeg (re-encode).

    This uses the `concat` filter (not the concat demuxer) to avoid Windows
    filelist encoding issues on non-ASCII paths.
    """
    if len(parts) < 1:
        raise ValueError("Need at least 1 part to concatenate")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH (required for stitching)")

    output_format = (output_format or "").strip().lower()
    if output_format not in {"mp3", "mp4", "m4a"}:
        raise ValueError("split_output_format must be mp3, mp4, or m4a")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if len(parts) == 1:
        cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(parts[0]), "-vn"]
        if output_format == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k", str(output_path)])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", str(output_path)])

        completed = subprocess.run(cmd, capture_output=True, text=True)
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"ffmpeg transcode failed: {err}")
        return ConcatResult(output_path=output_path, method="ffmpeg_transcode")

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


def concat_audio_with_transitions(
    parts: list[Path],
    transitions: list[Path | None],
    output_path: Path,
    output_format: str = "mp3",
    fade_seconds: float = 1.0,
) -> ConcatResult:
    if len(parts) < 1:
        raise ValueError("Need at least 1 part to concatenate")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH (required for stitching)")

    output_format = (output_format or "").strip().lower()
    if output_format not in {"mp3", "mp4", "m4a"}:
        raise ValueError("split_output_format must be mp3, mp4, or m4a")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build sequence: part1, transition1 (optional), part2, transition2, ...
    sequence: list[tuple[Path, bool, float, float | None]] = []
    for idx, part in enumerate(parts):
        sequence.append((part, False, 0.0, None))
        if idx < len(parts) - 1:
            trans = transitions[idx] if idx < len(transitions) else None
            if trans is not None:
                duration = get_audio_duration(trans).seconds
                fade = max(0.0, float(fade_seconds))
                if duration > 0 and fade > 0:
                    fade = min(fade, duration / 2.0)
                sequence.append((trans, True, fade, duration))

    if len(sequence) == 1:
        # single input; re-encode to target format
        return concat_audio([sequence[0][0]], output_path, output_format=output_format)

    cmd: list[str] = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for p, _, _, _ in sequence:
        cmd.extend(["-i", str(p)])

    filter_parts: list[str] = []
    label_map: list[str] = []
    for idx, (_, is_transition, fade, duration) in enumerate(sequence):
        label_in = f"[{idx}:a]"
        if is_transition and fade > 0:
            dur = duration or 0.0
            out_start = max(0.0, dur - fade)
            label_out = f"[t{idx}]"
            filter_parts.append(
                f"{label_in}afade=t=in:st=0:d={fade},afade=t=out:st={out_start}:d={fade}{label_out}"
            )
            label_map.append(label_out)
        else:
            label_map.append(label_in)

    filter_complex = "".join(label_map) + f"concat=n={len(label_map)}:v=0:a=1[outa]"
    if filter_parts:
        filter_complex = ";".join(filter_parts + [filter_complex])

    cmd.extend(["-filter_complex", filter_complex, "-map", "[outa]", "-vn"])
    if output_format == "mp3":
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k", str(output_path)])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", str(output_path)])

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"ffmpeg stitch failed: {err}")

    return ConcatResult(output_path=output_path, method="ffmpeg_concat_transition")
