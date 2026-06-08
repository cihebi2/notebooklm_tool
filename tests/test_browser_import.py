import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.accounts_store import AccountsStore
from app.config import AppPaths
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

    def test_account_store_persists_bound_profile(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            paths = AppPaths(
                base_dir=base,
                data_dir=base / "data",
                accounts_dir=base / "data" / "accounts",
                jobs_dir=base / "data" / "jobs",
            )
            paths.accounts_dir.mkdir(parents=True)
            store = AccountsStore(paths)
            account = store.add(
                "A",
                b'{"cookies":[{"name":"SID","value":"x","domain":".google.com"}],"origins":[]}',
                "2026-01-01T00:00:00+00:00",
                profile_id="firefox:abc.default-release",
            )

            loaded = AccountsStore(paths).get(account.id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.profile_id, "firefox:abc.default-release")

            updated = store.set_profile_id(account.id, "edge:Default")
            self.assertIsNotNone(updated)
            self.assertEqual(AccountsStore(paths).get(account.id).profile_id, "edge:Default")

    def test_account_store_moves_saved_browser_profile(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            paths = AppPaths(
                base_dir=base,
                data_dir=base / "data",
                accounts_dir=base / "data" / "accounts",
                jobs_dir=base / "data" / "jobs",
            )
            paths.accounts_dir.mkdir(parents=True)
            source_profile = base / "session" / "browser_profile"
            source_profile.mkdir(parents=True)
            (source_profile / "marker.txt").write_text("ok", encoding="utf-8")

            account = AccountsStore(paths).add(
                "A",
                b'{"cookies":[{"name":"SID","value":"x","domain":".google.com"}],"origins":[]}',
                "2026-01-01T00:00:00+00:00",
                browser_profile_source=source_profile,
                browser="edge",
            )

            self.assertFalse(source_profile.exists())
            self.assertIsNotNone(account.browser_profile_path)
            saved_profile = Path(account.browser_profile_path)
            self.assertTrue((saved_profile / "marker.txt").exists())
            self.assertEqual(account.browser, "edge")


if __name__ == "__main__":
    unittest.main()
