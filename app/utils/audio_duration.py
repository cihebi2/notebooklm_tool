from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile


@dataclass(frozen=True)
class AudioDuration:
    seconds: float
    method: str  # "mutagen" | "ffprobe"

    @property
    def minutes(self) -> float:
        return self.seconds / 60.0


def get_audio_duration(path: Path) -> AudioDuration:
    audio = MutagenFile(path)
    if audio is not None and getattr(audio, "info", None) is not None:
        length = getattr(audio.info, "length", None)
        if isinstance(length, (int, float)) and length > 0:
            return AudioDuration(seconds=float(length), method="mutagen")

    # Fallback to ffprobe if available
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(completed.stdout)
        duration_str = data.get("format", {}).get("duration", None)
        duration = float(duration_str)
        if duration > 0:
            return AudioDuration(seconds=duration, method="ffprobe")
    except Exception:
        pass

    raise ValueError(f"Unable to detect duration for: {path}")

