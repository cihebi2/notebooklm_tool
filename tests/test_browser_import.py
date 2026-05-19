import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.login_sessions import LoginSessionManager
from app.utils.browser_cookies import export_storage_state_from_profile_id
from app.utils.browser_profiles import list_browser_profiles, parse_profile_id


class BrowserImportTests(unittest.TestCase):
    def test_firefox_profile_is_listed_from_profiles_ini(self):
        with tempfile.TemporaryDirectory() as td:
            appdata = Path(td) / "Roaming"
            profile_dir = appdata / "Mozilla" / "Firefox" / "Profiles" / "abc.default-release"
            profile_dir.mkdir(parents=True)
            profiles_ini = appdata / "Mozilla" / "Firefox" / "profiles.ini"
            profiles_ini.write_text(
                "[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/abc.default-release\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                profiles = list_browser_profiles()

            firefox = [p for p in profiles if p.browser == "firefox"]
            self.assertEqual(len(firefox), 1)
            self.assertEqual(firefox[0].id, "firefox:abc.default-release")
            self.assertEqual(firefox[0].display_name, "default-release")

    def test_firefox_cookie_import_exports_playwright_storage(self):
        with tempfile.TemporaryDirectory() as td:
            appdata = Path(td) / "Roaming"
            profile_dir = appdata / "Mozilla" / "Firefox" / "Profiles" / "abc.default-release"
            profile_dir.mkdir(parents=True)
            db = profile_dir / "cookies.sqlite"
            con = sqlite3.connect(db)
            try:
                con.execute(
                    "CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT, path TEXT, expiry INTEGER, isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER)"
                )
                con.executemany(
                    "INSERT INTO moz_cookies VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (".google.com", "SID", "sid-value", "/", 1893456000, 0, 0, 1),
                        (".google.com", "__Secure-1PSIDTS", "sidts-value", "/", 1893456000, 1, 1, 1),
                        ("notebooklm.google.com", "OSID", "osid-value", "/", 1893456000, 1, 1, 1),
                    ],
                )
                con.commit()
            finally:
                con.close()

            with patch.dict(os.environ, {"APPDATA": str(appdata)}, clear=False):
                exported = export_storage_state_from_profile_id("firefox:abc.default-release")

            storage = json.loads(exported.storage_state_bytes.decode("utf-8"))
            cookies = {(c["domain"], c["name"]): c for c in storage["cookies"]}
            self.assertEqual(exported.cookie_count, 3)
            self.assertEqual(cookies[(".google.com", "SID")]["value"], "sid-value")
            self.assertEqual(cookies[(".google.com", "__Secure-1PSIDTS")]["value"], "sidts-value")

    def test_firefox_profile_id_rejects_path_traversal(self):
        for profile_id in ("firefox:../abc.default-release", "firefox:..", "firefox:."):
            with self.subTest(profile_id=profile_id):
                with self.assertRaises(ValueError):
                    parse_profile_id(profile_id)

    def test_firefox_profile_is_rejected_for_browser_login(self):
        with tempfile.TemporaryDirectory() as td:
            manager = LoginSessionManager(Path(td) / "sessions")
            with self.assertRaisesRegex(RuntimeError, "Firefox Profile"):
                asyncio.run(manager.start("FF", profile_id="firefox:abc.default-release"))


if __name__ == "__main__":
    unittest.main()
