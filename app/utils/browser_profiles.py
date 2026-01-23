from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrowserProfile:
    """A detected local browser profile (Windows)."""

    id: str  # e.g. "edge:Default" / "chrome:Profile 1"
    browser: str  # "edge" | "chrome"
    profile_dir: str  # "Default" / "Profile 1" / ...
    display_name: str
    user_name: str | None  # often email

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "browser": self.browser,
            "profile_dir": self.profile_dir,
            "display_name": self.display_name,
            "user_name": self.user_name,
        }


def _local_appdata() -> Path | None:
    value = os.environ.get("LOCALAPPDATA") or ""
    return Path(value) if value else None


def get_user_data_dir(browser: str) -> Path | None:
    """Return Windows user-data-dir for a browser."""
    base = _local_appdata()
    if not base:
        return None
    match (browser or "").strip().lower():
        case "edge" | "msedge":
            return base / "Microsoft" / "Edge" / "User Data"
        case "chrome":
            return base / "Google" / "Chrome" / "User Data"
        case _:
            return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


_PROFILE_DIR_RE = re.compile(r"^(Default|Profile \d+)$")


def _sort_profile_dir(name: str) -> tuple[int, int]:
    if name == "Default":
        return (0, 0)
    m = re.match(r"^Profile (\d+)$", name)
    if m:
        try:
            return (1, int(m.group(1)))
        except Exception:
            return (1, 9999)
    return (9, 9999)


def list_browser_profiles() -> list[BrowserProfile]:
    """Best-effort list of local Edge/Chrome profiles on Windows."""

    out: list[BrowserProfile] = []

    for browser in ("edge", "chrome"):
        user_data_dir = get_user_data_dir(browser)
        if not user_data_dir or not user_data_dir.exists():
            continue

        info_cache: dict[str, Any] = {}
        local_state = _read_json(user_data_dir / "Local State")
        if isinstance(local_state, dict):
            profile = local_state.get("profile")
            if isinstance(profile, dict):
                cache = profile.get("info_cache")
                if isinstance(cache, dict):
                    info_cache = cache

        # Prefer Local State keys, fallback to directory scan.
        candidates: set[str] = set()
        for key in info_cache.keys():
            if isinstance(key, str) and _PROFILE_DIR_RE.match(key):
                candidates.add(key)
        try:
            for p in user_data_dir.iterdir():
                if p.is_dir() and _PROFILE_DIR_RE.match(p.name):
                    candidates.add(p.name)
        except Exception:
            pass

        for profile_dir in sorted(candidates, key=_sort_profile_dir):
            if not (user_data_dir / profile_dir).exists():
                continue
            info = info_cache.get(profile_dir)
            display_name = profile_dir
            user_name: str | None = None
            if isinstance(info, dict):
                display_name = str(info.get("name") or display_name)
                raw_user = info.get("user_name")
                if raw_user:
                    user_name = str(raw_user)

            out.append(
                BrowserProfile(
                    id=f"{browser}:{profile_dir}",
                    browser=browser,
                    profile_dir=profile_dir,
                    display_name=display_name,
                    user_name=user_name,
                )
            )

    return out


def parse_profile_id(profile_id: str) -> tuple[str, str]:
    """Parse an API profile_id like 'edge:Default'."""
    value = (profile_id or "").strip()
    if ":" not in value:
        raise ValueError("profile_id must be like 'edge:Default' or 'chrome:Profile 1'")
    browser, profile_dir = value.split(":", 1)
    browser = browser.strip().lower()
    profile_dir = profile_dir.strip()
    if browser not in {"edge", "chrome"}:
        raise ValueError("profile_id browser must be 'edge' or 'chrome'")
    if not profile_dir:
        raise ValueError("profile_id missing profile dir")
    if not _PROFILE_DIR_RE.match(profile_dir):
        raise ValueError("Unsupported profile dir (expected Default / Profile N)")
    return browser, profile_dir

