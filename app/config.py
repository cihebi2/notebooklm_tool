from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path
    data_dir: Path
    accounts_dir: Path
    jobs_dir: Path


def get_paths() -> AppPaths:
    base_dir = Path(__file__).resolve().parent.parent
    data_dir = base_dir / "data"
    return AppPaths(
        base_dir=base_dir,
        data_dir=data_dir,
        accounts_dir=data_dir / "accounts",
        jobs_dir=data_dir / "jobs",
    )

