from __future__ import annotations

import array
import math
import shutil
import subprocess
from pathlib import Path

from .audio_duration import get_audio_duration


def compute_waveform_peaks(
    audio_path: Path, *, points: int = 1200, sample_rate: int = 2000
) -> tuple[list[float], float]:
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found in PATH")

    duration = get_audio_duration(audio_path).seconds
    if duration <= 0:
        raise ValueError("invalid duration")

    total_samples = max(1, int(duration * sample_rate))
    samples_per_bucket = max(1, math.ceil(total_samples / max(1, points)))
    peaks: list[float] = [0.0 for _ in range(points)]

    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    idx = 0
    count = 0
    current_max = 0.0

    try:
        while idx < points:
            chunk = proc.stdout.read(4096) if proc.stdout else b""
            if not chunk:
                break
            buf = array.array("h")
            buf.frombytes(chunk)
            for sample in buf:
                val = abs(sample) / 32768.0
                if val > current_max:
                    current_max = val
                count += 1
                if count >= samples_per_bucket:
                    peaks[idx] = round(current_max, 4)
                    idx += 1
                    if idx >= points:
                        break
                    count = 0
                    current_max = 0.0
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass

    if idx < points and count > 0:
        peaks[idx] = round(current_max, 4)

    return peaks, duration
