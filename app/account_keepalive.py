from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .accounts_store import Account, AccountsStore


logger = logging.getLogger(__name__)


DEFAULT_KEEPALIVE_INTERVAL_SECONDS = 480
DEFAULT_KEEPALIVE_PER_ACCOUNT_DELAY_SECONDS = 20
DEFAULT_KEEPALIVE_STARTUP_DELAY_SECONDS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AccountKeepaliveResult:
    account_id: str
    account_name: str
    ok: bool
    message: str
    checked_at: str
    used_profile_recovery: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _looks_auth_expired(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "authentication expired" in msg
        or "run 'notebooklm login'" in msg
        or "accounts.google.com" in msg
        or "redirected to:" in msg
        or "unauthorized" in msg
    )


async def _fetch_with_storage(storage_path: Path) -> None:
    from notebooklm.auth import fetch_tokens_with_domains

    await fetch_tokens_with_domains(storage_path)


def _browser_channel(browser: str | None) -> str | None:
    match (browser or "").strip().lower():
        case "edge" | "msedge":
            return "msedge"
        case "chrome":
            return "chrome"
        case _:
            return None


def _rebuild_storage_from_account_browser_profile_sync(
    *,
    browser_profile_path: Path,
    browser: str | None,
    storage_path: Path,
) -> str:
    from playwright.sync_api import sync_playwright

    if not browser_profile_path.exists():
        raise RuntimeError(f"saved browser profile missing: {browser_profile_path}")

    channel = _browser_channel(browser)
    launch_kwargs: dict[str, Any] = {
        "user_data_dir": str(browser_profile_path),
        "headless": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--password-store=basic",
        ],
        "ignore_default_args": ["--enable-automation"],
    }
    if channel is not None:
        launch_kwargs["channel"] = channel

    playwright = sync_playwright().start()
    context = None
    try:
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
        page = context.new_page()
        page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_000)
        context.storage_state(path=str(storage_path))
        return f"saved_browser_profile={browser_profile_path} browser={browser or 'chromium'} url={page.url}"
    finally:
        try:
            if context:
                context.close()
        finally:
            playwright.stop()


async def _rebuild_storage_from_account_browser_profile(account: Account, storage_path: Path) -> str:
    if not account.browser_profile_path:
        raise RuntimeError("account has no saved browser profile")
    return await asyncio.to_thread(
        _rebuild_storage_from_account_browser_profile_sync,
        browser_profile_path=Path(account.browser_profile_path),
        browser=account.browser,
        storage_path=storage_path,
    )


async def _rebuild_storage_from_profile(account: Account, storage_path: Path) -> str:
    from .utils.browser_cookies import export_storage_state_from_profile_id

    if not account.profile_id:
        raise RuntimeError("account has no bound browser profile")
    if not account.profile_id.lower().startswith("firefox:"):
        raise RuntimeError("only Firefox Profile recovery is enabled")
    exported = await asyncio.to_thread(export_storage_state_from_profile_id, account.profile_id)
    storage_path.write_bytes(exported.storage_state_bytes)
    return f"profile={account.profile_id} cookies={exported.cookie_count}"


async def refresh_account_cookies(account: Account) -> AccountKeepaliveResult:
    """Refresh NotebookLM cookies for one stored account."""

    storage_path = Path(account.storage_path)
    if not storage_path.exists():
        return AccountKeepaliveResult(
            account_id=account.id,
            account_name=account.name,
            ok=False,
            message=f"storage file missing: {storage_path}",
            checked_at=_now_iso(),
        )

    try:
        await _fetch_with_storage(storage_path)
        return AccountKeepaliveResult(
            account_id=account.id,
            account_name=account.name,
            ok=True,
            message="refreshed",
            checked_at=_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001 - keepalive must not stop the web app.
        if (account.browser_profile_path or account.profile_id) and _looks_auth_expired(exc):
            try:
                if account.browser_profile_path:
                    profile_info = await _rebuild_storage_from_account_browser_profile(account, storage_path)
                else:
                    profile_info = await _rebuild_storage_from_profile(account, storage_path)
                await _fetch_with_storage(storage_path)
                return AccountKeepaliveResult(
                    account_id=account.id,
                    account_name=account.name,
                    ok=True,
                    message=f"rebuilt_from_profile: {profile_info}",
                    checked_at=_now_iso(),
                    used_profile_recovery=True,
                )
            except Exception as recovery_exc:  # noqa: BLE001
                return AccountKeepaliveResult(
                    account_id=account.id,
                    account_name=account.name,
                    ok=False,
                    message=(
                        f"{type(exc).__name__}: {exc} | "
                        f"profile_recovery_failed: {type(recovery_exc).__name__}: {recovery_exc}"
                    ),
                    checked_at=_now_iso(),
                    used_profile_recovery=True,
                )
        return AccountKeepaliveResult(
            account_id=account.id,
            account_name=account.name,
            ok=False,
            message=f"{type(exc).__name__}: {exc}",
            checked_at=_now_iso(),
        )


class AccountKeepaliveService:
    """Background cookie keepalive for all saved NotebookLM accounts."""

    def __init__(
        self,
        accounts_store: AccountsStore,
        *,
        interval_seconds: int = DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
        per_account_delay_seconds: int = DEFAULT_KEEPALIVE_PER_ACCOUNT_DELAY_SECONDS,
        startup_delay_seconds: int = DEFAULT_KEEPALIVE_STARTUP_DELAY_SECONDS,
    ) -> None:
        self._accounts_store = accounts_store
        self._interval_seconds = max(300, int(interval_seconds))
        self._per_account_delay_seconds = max(0, int(per_account_delay_seconds))
        self._startup_delay_seconds = max(0, int(startup_delay_seconds))
        self._task: asyncio.Task[None] | None = None
        self._run_lock: asyncio.Lock | None = None
        self._rounds = 0
        self._last_started_at: str | None = None
        self._last_finished_at: str | None = None
        self._last_results: list[AccountKeepaliveResult] = []

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._run_lock = asyncio.Lock()
        self._task = asyncio.create_task(self._run_forever(), name="notebooklm-account-keepalive")
        logger.info(
            "NotebookLM account keepalive started: interval=%ss per_account_delay=%ss",
            self._interval_seconds,
            self._per_account_delay_seconds,
        )

    async def stop(self) -> None:
        task = self._task
        if not task:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None
        logger.info("NotebookLM account keepalive stopped")

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._task and not self._task.done()),
            "interval_seconds": self._interval_seconds,
            "per_account_delay_seconds": self._per_account_delay_seconds,
            "startup_delay_seconds": self._startup_delay_seconds,
            "rounds": self._rounds,
            "last_started_at": self._last_started_at,
            "last_finished_at": self._last_finished_at,
            "last_results": [r.to_dict() for r in self._last_results],
        }

    async def run_once(self) -> list[AccountKeepaliveResult]:
        if self._run_lock is None:
            self._run_lock = asyncio.Lock()

        async with self._run_lock:
            self._last_started_at = _now_iso()
            accounts = self._accounts_store.list()
            if not accounts:
                logger.info("NotebookLM account keepalive skipped: no accounts")
                self._last_results = []
                self._last_finished_at = _now_iso()
                self._rounds += 1
                return []

            logger.info("NotebookLM account keepalive round started: accounts=%s", len(accounts))
            results: list[AccountKeepaliveResult] = []
            for idx, account in enumerate(accounts):
                result = await refresh_account_cookies(account)
                results.append(result)
                if result.ok:
                    logger.info("NotebookLM keepalive OK: %s %s", account.id, account.name)
                else:
                    logger.warning(
                        "NotebookLM keepalive failed: %s %s: %s",
                        account.id,
                        account.name,
                        result.message,
                    )

                if self._per_account_delay_seconds > 0 and idx < len(accounts) - 1:
                    await asyncio.sleep(self._per_account_delay_seconds)

            ok_count = sum(1 for r in results if r.ok)
            self._last_results = results
            self._last_finished_at = _now_iso()
            self._rounds += 1
            logger.info(
                "NotebookLM account keepalive round finished: ok=%s/%s",
                ok_count,
                len(results),
            )
            return results

    async def _run_forever(self) -> None:
        try:
            if self._startup_delay_seconds > 0:
                await asyncio.sleep(self._startup_delay_seconds)
            while True:
                await self.run_once()
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("NotebookLM account keepalive crashed")
