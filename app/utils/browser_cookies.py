from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .browser_profiles import get_user_data_dir, parse_profile_id


_EPOCH_1601_SECONDS = 11644473600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chrome_time_to_unix_seconds(expires_utc: int | None) -> float:
    if not expires_utc:
        return -1.0
    # Chrome stores microseconds since 1601-01-01 (UTC).
    return (float(expires_utc) / 1_000_000.0) - _EPOCH_1601_SECONDS


def _map_same_site(value: int | None) -> str | None:
    # Chrome samesite: 0=UNSPECIFIED, 1=LAX, 2=STRICT, 3=NONE
    match int(value or 0):
        case 1:
            return "Lax"
        case 2:
            return "Strict"
        case 3:
            return "None"
        case _:
            return None


def _looks_like_google_domain(domain: str) -> bool:
    d = (domain or "").strip().lstrip(".").lower()
    if not d:
        return False
    if d == "google.com" or d.endswith(".google.com"):
        return True
    if d.endswith(".googleusercontent.com") or d.endswith(".usercontent.google.com"):
        return True
    # Regional Google domains: google.co.uk, google.com.sg, google.de, ...
    return bool(re.search(r"(^|\\.)google\\.[a-z.]{2,}$", d))


def _dpapi_decrypt(encrypted: bytes) -> bytes:
    """Decrypt DPAPI-encrypted data for the current Windows user."""
    if os.name != "nt":  # pragma: no cover
        raise RuntimeError("DPAPI decrypt only supported on Windows")

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CryptUnprotectData = crypt32.CryptUnprotectData
    CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    CryptUnprotectData.restype = wintypes.BOOL

    LocalFree = kernel32.LocalFree
    LocalFree.argtypes = [wintypes.HLOCAL]
    LocalFree.restype = wintypes.HLOCAL

    if not encrypted:
        return b""

    in_buffer = ctypes.create_string_buffer(encrypted, len(encrypted))
    in_blob = DATA_BLOB(len(encrypted), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)))
    out_blob = DATA_BLOB()

    ok = CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        LocalFree(out_blob.pbData)


def _get_chromium_master_key(user_data_dir: Path) -> bytes | None:
    local_state_path = user_data_dir / "Local State"
    try:
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    encrypted_key_b64 = (
        local_state.get("os_crypt", {}).get("encrypted_key", None) if isinstance(local_state, dict) else None
    )
    if not encrypted_key_b64 or not isinstance(encrypted_key_b64, str):
        return None

    try:
        encrypted_key = base64.b64decode(encrypted_key_b64)
    except Exception:
        return None

    # Newer Chromium stores: b"DPAPI" + dpapi(encrypted_key)
    if encrypted_key.startswith(b"DPAPI"):
        encrypted_key = encrypted_key[5:]

    try:
        return _dpapi_decrypt(encrypted_key)
    except Exception:
        return None


def _decrypt_chromium_cookie(encrypted_value: bytes, master_key: bytes | None) -> bytes:
    if not encrypted_value:
        return b""

    # AES-GCM (v10/v11/v20) with master key
    if encrypted_value.startswith((b"v10", b"v11", b"v20")):
        if not master_key:
            raise RuntimeError("Missing Chromium master key (cannot decrypt v10/v11/v20 cookies)")
        nonce = encrypted_value[3:15]
        ciphertext = encrypted_value[15:]
        return AESGCM(master_key).decrypt(nonce, ciphertext, None)

    # DPAPI direct
    return _dpapi_decrypt(encrypted_value)


def _find_chromium_cookie_db(user_data_dir: Path, profile_dir: str) -> Path:
    candidates = [
        user_data_dir / profile_dir / "Network" / "Cookies",
        user_data_dir / profile_dir / "Cookies",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    raise FileNotFoundError(f"Cookies DB not found for profile: {profile_dir}")


def _find_firefox_cookie_db(profiles_dir: Path, profile_dir: str) -> Path:
    cookie_db = profiles_dir / profile_dir / "cookies.sqlite"
    if cookie_db.exists() and cookie_db.is_file():
        return cookie_db
    raise FileNotFoundError(f"Firefox cookies.sqlite not found for profile: {profile_dir}")


def _decode_db_text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value or "")


@dataclass(frozen=True)
class ExportResult:
    storage_state_bytes: bytes
    cookie_count: int
    ts_iso: str


def export_storage_state_from_profile_id(profile_id: str) -> ExportResult:
    """Export a Playwright-style storage_state.json from an existing browser profile.

    Chromium cookies may be protected by Windows App-Bound Encryption. Firefox
    stores cookie values directly in cookies.sqlite, so it is usually the more
    reliable unattended import path on Windows.
    """
    browser, profile_dir = parse_profile_id(profile_id)
    user_data_dir = get_user_data_dir(browser)
    if not user_data_dir or not user_data_dir.exists():
        raise FileNotFoundError(f"{browser} user-data-dir not found")

    if browser == "firefox":
        return _export_firefox_storage_state(user_data_dir, profile_dir)

    cookie_db = _find_chromium_cookie_db(user_data_dir, profile_dir)
    master_key = _get_chromium_master_key(user_data_dir)

    tmp_dir = Path(tempfile.mkdtemp(prefix="notebooklm_cookies_"))
    try:
        tmp_db = tmp_dir / "Cookies.db"
        try:
            shutil.copy2(cookie_db, tmp_db)
        except Exception as e:
            raise RuntimeError(
                f"Failed to copy Cookies DB (is the browser locked?). Close {browser} and retry: {e}"
            ) from e

        con = sqlite3.connect(str(tmp_db))
        con.text_factory = bytes
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT host_key, name, path, value, encrypted_value, expires_utc, is_secure, is_httponly, samesite "
                "FROM cookies"
            )
            rows = cur.fetchall()
        finally:
            con.close()

        cookies_out: list[dict[str, Any]] = []
        candidate_rows = 0
        candidate_v20 = 0
        decrypt_failed = 0
        for host_key, name, path, value, encrypted_value, expires_utc, is_secure, is_httponly, samesite in rows:
            try:
                if isinstance(host_key, (bytes, bytearray)):
                    domain = host_key.decode("utf-8", errors="replace")
                else:
                    domain = str(host_key or "")
                if not _looks_like_google_domain(domain):
                    continue
                candidate_rows += 1
                if isinstance(name, (bytes, bytearray)):
                    name = name.decode("utf-8", errors="replace")
                name = str(name or "").strip()
                if not name:
                    continue

                clear_value: bytes = b""
                if isinstance(value, (bytes, bytearray)):
                    clear_value = bytes(value)
                elif isinstance(value, str):
                    clear_value = value.encode("utf-8", errors="ignore")

                if (
                    not clear_value
                    and isinstance(encrypted_value, (bytes, bytearray, memoryview))
                ):
                    encrypted_bytes = bytes(encrypted_value)
                    if encrypted_bytes.startswith(b"v20"):
                        candidate_v20 += 1
                    try:
                        clear_value = _decrypt_chromium_cookie(encrypted_bytes, master_key)
                    except Exception:
                        decrypt_failed += 1
                        continue
                if not clear_value:
                    continue

                if isinstance(path, (bytes, bytearray)):
                    path = path.decode("utf-8", errors="replace")

                cookie_obj: dict[str, Any] = {
                    "name": name,
                    "value": clear_value.decode("utf-8", errors="replace"),
                    "domain": domain,
                    "path": str(path or "/"),
                    "expires": _chrome_time_to_unix_seconds(int(expires_utc or 0)),
                    "httpOnly": bool(is_httponly),
                    "secure": bool(is_secure),
                }
                same_site = _map_same_site(samesite)
                if same_site:
                    cookie_obj["sameSite"] = same_site
                cookies_out.append(cookie_obj)
            except Exception:
                continue

        if not cookies_out:
            if candidate_rows > 0 and candidate_v20 > 0:
                raise RuntimeError(
                    "未能从浏览器 Cookies 数据库解密 Google Cookies：检测到 v20(AppBound) 加密。"
                    "这类 Cookie 无法直接离线解密导出，请改用“浏览器登录添加”（会弹出窗口保存 storage_state.json）。"
                )
            raise RuntimeError(
                f"No Google cookies extracted from this profile (google_rows={candidate_rows}, decrypt_failed={decrypt_failed})."
            )

        names = {c.get("name") for c in cookies_out if isinstance(c, dict)}
        if "SID" not in names:
            raise RuntimeError("SID cookie not found in extracted cookies (profile not logged in?)")

        storage_state = {"cookies": cookies_out, "origins": []}
        raw = json.dumps(storage_state, ensure_ascii=False, indent=2).encode("utf-8")
        return ExportResult(storage_state_bytes=raw, cookie_count=len(cookies_out), ts_iso=_now_iso())
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _export_firefox_storage_state(profiles_dir: Path, profile_dir: str) -> ExportResult:
    cookie_db = _find_firefox_cookie_db(profiles_dir, profile_dir)
    tmp_dir = Path(tempfile.mkdtemp(prefix="notebooklm_firefox_cookies_"))
    try:
        tmp_db = tmp_dir / "cookies.sqlite"
        try:
            shutil.copy2(cookie_db, tmp_db)
        except Exception as e:
            raise RuntimeError(
                f"Failed to copy Firefox cookies.sqlite (is Firefox still writing it?): {e}"
            ) from e

        con = sqlite3.connect(str(tmp_db))
        try:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(moz_cookies)")
            columns = {str(row[1]) for row in cur.fetchall()}
            same_site_col = "sameSite" if "sameSite" in columns else None
            select_cols = "host, name, path, value, expiry, isSecure, isHttpOnly"
            if same_site_col:
                select_cols += f", {same_site_col}"
            cur.execute(f"SELECT {select_cols} FROM moz_cookies")
            rows = cur.fetchall()
        finally:
            con.close()

        cookies_out: list[dict[str, Any]] = []
        candidate_rows = 0
        for row in rows:
            if same_site_col:
                host, name, path, value, expiry, is_secure, is_http_only, same_site = row
            else:
                host, name, path, value, expiry, is_secure, is_http_only = row
                same_site = None

            domain = _decode_db_text(host)
            if not _looks_like_google_domain(domain):
                continue
            candidate_rows += 1
            name = _decode_db_text(name).strip()
            value = _decode_db_text(value)
            if not name or not value:
                continue

            cookie_obj: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": _decode_db_text(path) or "/",
                "expires": float(expiry or -1),
                "httpOnly": bool(is_http_only),
                "secure": bool(is_secure),
            }
            same_site_value = _map_same_site(same_site)
            if same_site_value:
                cookie_obj["sameSite"] = same_site_value
            cookies_out.append(cookie_obj)

        if not cookies_out:
            raise RuntimeError(f"No Google cookies extracted from this Firefox profile (google_rows={candidate_rows}).")

        names = {c.get("name") for c in cookies_out if isinstance(c, dict)}
        if "SID" not in names:
            raise RuntimeError("SID cookie not found in extracted Firefox cookies (profile not logged in?)")

        storage_state = {"cookies": cookies_out, "origins": []}
        raw = json.dumps(storage_state, ensure_ascii=False, indent=2).encode("utf-8")
        return ExportResult(storage_state_bytes=raw, cookie_count=len(cookies_out), ts_iso=_now_iso())
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
