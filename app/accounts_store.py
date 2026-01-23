from __future__ import annotations

import secrets
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

    def add(self, name: str, storage_state_bytes: bytes, created_at_iso: str) -> Account:
        account_id = secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        account_dir = self._paths.accounts_dir / account_id
        account_dir.mkdir(parents=True, exist_ok=True)
        storage_path = account_dir / "storage_state.json"
        storage_path.write_bytes(storage_state_bytes)

        account = Account(
            id=account_id,
            name=name,
            storage_path=str(storage_path),
            created_at_iso=created_at_iso,
        )
        accounts = list(self._load_all().values())
        accounts.append(account)
        self._save_all(accounts)
        return account

    def delete(self, account_id: str) -> bool:
        accounts = self._load_all()
        if account_id not in accounts:
            return False
        remaining = [a for a in accounts.values() if a.id != account_id]
        self._save_all(remaining)

        # Best-effort cleanup of files
        try:
            account_dir = self._paths.accounts_dir / account_id
            storage_path = account_dir / "storage_state.json"
            if storage_path.exists():
                storage_path.unlink()
            if account_dir.exists():
                account_dir.rmdir()
        except OSError:
            pass
        return True

