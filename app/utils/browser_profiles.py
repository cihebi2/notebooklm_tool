from __future__ import annotations

import configparser
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BrowserProfile:
    """A detected local browser profile (Windows)."""

    id: str  # e.g. "edge:Default" / "chrome:Profile 1" / "firefox:abc.default-release"
    browser: str  # "edge" | "chrome" | "firefox"
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


def _roaming_appdata() -> Path | None:
    value = os.environ.get("APPDATA") or ""
    return Path(value) if value else None


def get_user_data_dir(browser: str) -> Path | None:
    """Return Windows user-data-dir for a browser."""
    normalized = (browser or "").strip().lower()
    if normalized in {"edge", "msedge", "chrome"}:
        base = _local_appdata()
        if not base:
            return None
        match normalized:
            case "edge" | "msedge":
                return base / "Microsoft" / "Edge" / "User Data"
            case "chrome":
                return base / "Google" / "Chrome" / "User Data"
    if normalized == "firefox":
        base = _roaming_appdata()
        return base / "Mozilla" / "Firefox" / "Profiles" if base else None
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


_CHROMIUM_PROFILE_DIR_RE = re.compile(r"^(Default|Profile \d+)$")
_FIREFOX_PROFILE_DIR_RE = re.compile(r"^[A-Za-z0-9._ -]+$")


def _is_safe_firefox_profile_dir(profile_dir: str) -> bool:
    return bool(_FIREFOX_PROFILE_DIR_RE.match(profile_dir)) and profile_dir not in {".", ".."}


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
            if isinstance(key, str) and _CHROMIUM_PROFILE_DIR_RE.match(key):
                candidates.add(key)
        try:
            for p in user_data_dir.iterdir():
                if p.is_dir() and _CHROMIUM_PROFILE_DIR_RE.match(p.name):
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

    out.extend(_list_firefox_profiles())
    return out


def _list_firefox_profiles() -> list[BrowserProfile]:
    profiles_base = get_user_data_dir("firefox")
    appdata = _roaming_appdata()
    if not profiles_base or not profiles_base.exists() or not appdata:
        return []

    out: list[BrowserProfile] = []
    seen: set[str] = set()
    profiles_ini = appdata / "Mozilla" / "Firefox" / "profiles.ini"

    if profiles_ini.exists():
        parser = configparser.ConfigParser()
        try:
            parser.read(profiles_ini, encoding="utf-8")
        except Exception:
            parser = configparser.ConfigParser()

        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            raw_path = parser.get(section, "Path", fallback="").strip()
            if not raw_path:
                continue
            profile_name = Path(raw_path.replace("\\", "/")).name
            if not _is_safe_firefox_profile_dir(profile_name):
                continue
            profile_path = profiles_base / profile_name
            if not profile_path.exists():
                continue
            display_name = parser.get(section, "Name", fallback=profile_name).strip() or profile_name
            seen.add(profile_name)
            out.append(
                BrowserProfile(
                    id=f"firefox:{profile_name}",
                    browser="firefox",
                    profile_dir=profile_name,
                    display_name=display_name,
                    user_name=None,
                )
            )

    try:
        for p in profiles_base.iterdir():
            if not p.is_dir() or p.name in seen or not _is_safe_firefox_profile_dir(p.name):
                continue
            if not (p / "cookies.sqlite").exists():
                continue
            out.append(
                BrowserProfile(
                    id=f"firefox:{p.name}",
                    browser="firefox",
                    profile_dir=p.name,
                    display_name=p.name,
                    user_name=None,
                )
            )
    except Exception:
        pass

    return out


def parse_profile_id(profile_id: str) -> tuple[str, str]:
    """Parse an API profile_id like 'edge:Default'."""
    value = (profile_id or "").strip()
    if ":" not in value:
        raise ValueError("profile_id must be like 'edge:Default', 'chrome:Profile 1', or 'firefox:default-release'")
    browser, profile_dir = value.split(":", 1)
    browser = browser.strip().lower()
    profile_dir = profile_dir.strip()
    if browser not in {"edge", "chrome", "firefox"}:
        raise ValueError("profile_id browser must be 'edge', 'chrome', or 'firefox'")
    if not profile_dir:
        raise ValueError("profile_id missing profile dir")
    if browser in {"edge", "chrome"}:
        if not _CHROMIUM_PROFILE_DIR_RE.match(profile_dir):
            raise ValueError("Unsupported Chromium profile dir (expected Default / Profile N)")
    elif not _is_safe_firefox_profile_dir(profile_dir):
        raise ValueError("Unsupported Firefox profile dir")
    return browser, profile_dir

