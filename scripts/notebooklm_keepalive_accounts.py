from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.account_keepalive import refresh_account_cookies
from app.accounts_store import Account, AccountsStore
from app.config import get_paths


def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def load_accounts() -> list[Account]:
    return AccountsStore(get_paths()).list()


async def refresh_account(account: Account) -> bool:
    result = await refresh_account_cookies(account)
    if result.ok:
        log(f"OK {account.id} {account.name}")
        return True
    log(f"FAIL {account.id} {account.name}: {result.message}")
    return False


async def run_round(per_account_delay_seconds: int) -> int:
    accounts = load_accounts()
    if not accounts:
        log("No accounts found under data/accounts.")
        return 0

    log(f"Keepalive round started. accounts={len(accounts)}")
    ok_count = 0
    for idx, account in enumerate(accounts):
        if await refresh_account(account):
            ok_count += 1
        if per_account_delay_seconds > 0 and idx < len(accounts) - 1:
            await asyncio.sleep(per_account_delay_seconds)
    log(f"Keepalive round finished. ok={ok_count}/{len(accounts)}")
    return ok_count


async def main() -> int:
    parser = argparse.ArgumentParser(description="Keep NotebookLM account cookies fresh.")
    parser.add_argument("--interval-seconds", type=int, default=480)
    parser.add_argument("--per-account-delay-seconds", type=int, default=20)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.interval_seconds < 300:
        raise SystemExit("--interval-seconds should be >= 300 to avoid excessive Google auth traffic")
    if args.per_account_delay_seconds < 0:
        raise SystemExit("--per-account-delay-seconds must be >= 0")

    while True:
        await run_round(args.per_account_delay_seconds)
        if args.once:
            return 0
        log(f"Sleeping {args.interval_seconds}s before next keepalive round.")
        await asyncio.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
