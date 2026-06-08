from __future__ import annotations

import secrets
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .config import AppPaths
from .utils.fs import atomic_write_json, read_json


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    storage_path: str  # absolute path to storage_state.json
    created_at_iso: str
    profile_id: str | None = None  # optional browser profile used to rebuild cookies
    browser_profile_path: str | None = None  # account-owned browser profile from interactive login
    browser: str | None = None  # chromium | edge | chrome


class AccountsStore:
    def __init__(self, paths: AppPaths):
        self._paths = paths
        self._index_path = paths.accounts_dir / "accounts.json"

    def _load_all(self) -> dict[str, Account]:
        raw = read_json(self._index_path, default={"accounts": []})
        accounts: dict[str, Account] = {}
        for item in raw.get("accounts", []):
            try:
                account = Account(
                    id=str(item["id"]),
                    name=str(item["name"]),
                    storage_path=str(item["storage_path"]),
                    created_at_iso=str(item["created_at_iso"]),
                    profile_id=str(item["profile_id"]) if item.get("profile_id") else None,
                    browser_profile_path=(
                        str(item["browser_profile_path"]) if item.get("browser_profile_path") else None
                    ),
                    browser=str(item["browser"]) if item.get("browser") else None,
                )
                accounts[account.id] = account
            except Exception:
                continue
        return accounts

    def _save_all(self, accounts: Iterable[Account]) -> None:
        atomic_write_json(self._index_path, {"accounts": [asdict(a) for a in accounts]})

    def list(self) -> list[Account]:
        return sorted(self._load_all().values(), key=lambda a: a.created_at_iso)

    def get(self, account_id: str) -> Account | None:
        return self._load_all().get(account_id)

    def add(
        self,
        name: str,
        storage_state_bytes: bytes,
        created_at_iso: str,
        profile_id: str | None = None,
        browser_profile_source: Path | None = None,
        browser: str | None = None,
    ) -> Account:
        account_id = secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        account_dir = self._paths.accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=True)
        storage_path = account_dir / "storage_state.json"
        storage_path.write_bytes(storage_state_bytes)

        browser_profile_path: str | None = None
        if browser_profile_source and browser_profile_source.exists():
            dest = account_dir / "browser_profile"
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(browser_profile_source), str(dest))
            browser_profile_path = str(dest)

        account = Account(
            id=account_id,
            name=name,
            storage_path=str(storage_path),
            created_at_iso=created_at_iso,
            profile_id=profile_id,
            browser_profile_path=browser_profile_path,
            browser=browser if browser_profile_path else None,
        )
        accounts = list(self._load_all().values())
        accounts.append(account)
        self._save_all(accounts)
        return account

    def set_profile_id(self, account_id: str, profile_id: str | None) -> Account | None:
        accounts = self._load_all()
        account = accounts.get(account_id)
        if not account:
            return None
        updated = Account(
            id=account.id,
            name=account.name,
            storage_path=account.storage_path,
            created_at_iso=account.created_at_iso,
            profile_id=(profile_id or None),
            browser_profile_path=account.browser_profile_path,
            browser=account.browser,
        )
        accounts[account_id] = updated
        self._save_all(accounts.values())
        return updated

    def delete(self, account_id: str) -> bool:
        accounts = self._load_all()
        if account_id not in accounts:
            return False
        remaining = [a for a in accounts.values() if a.id != account_id]
        self._save_all(remaining)

        # Best-effort cleanup of files
        try:
            account_dir = self._paths.accounts_dir / account_id
            if account_dir.exists():
                shutil.rmtree(account_dir, ignore_errors=True)
        except OSError:
            pass
        return True

