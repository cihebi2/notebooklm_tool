from __future__ import annotations

import asyncio
import json
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import traceback
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils.browser_profiles import get_user_data_dir, parse_profile_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(name: str) -> str:
    name = (name or "").strip()
    return name[:80] if name else "Account"


def _short(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _ensure_chromium_installed() -> None:
    """Ensure Playwright's bundled Chromium is installed (idempotent)."""
    completed = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        msg = (completed.stdout or "") + "\n" + (completed.stderr or "")
        raise RuntimeError(f"playwright install chromium failed: {_short(msg.strip())}")


def _normalize_browser(value: str | None) -> str:
    v = (value or "").strip().lower()
    if not v:
        return "edge" if sys.platform.startswith("win") else "chromium"
    if v in {"edge", "msedge"}:
        return "edge"
    if v == "chrome":
        return "chrome"
    return "chromium"


def _browser_channel(browser: str) -> str | None:
    match browser:
        case "edge":
            return "msedge"
        case "chrome":
            return "chrome"
        case _:
            return None


def _friendly_launch_error(err: str) -> str | None:
    low = (err or "").lower()
    if "notimplementederror" in low and "_make_subprocess_transport" in low:
        return (
            "启动失败：当前事件循环不支持创建子进程（常见于 Windows 上使用 `uvicorn --reload`）。"
            "请用不带 `--reload` 的方式启动服务（例如双击 `run.bat`），然后重试“浏览器登录添加”。"
        )
    if (
        "user data directory is already in use" in low
        or "processsingleton" in low
        or "profile in use" in low
        or "already running" in low
    ):
        return (
            "启动失败：浏览器 Profile 可能被占用。请先关闭对应浏览器的所有窗口/进程后重试，"
            "或不要选择“复用已登录 Profile”。"
        )
    if "executable doesn't exist" in low or "could not find" in low:
        return "启动失败：未找到浏览器可执行文件。请切换“登录浏览器”选项，或改用 Playwright Chromium。"
    if "playwright" in low and ("install" in low or "download" in low):
        return "启动失败：Playwright 浏览器未安装。可在终端执行：`playwright install chromium`。"
    return None


@dataclass
class LoginSession:
    id: str
    name: str
    created_at_iso: str

    state: str  # "starting" | "waiting_login" | "error" | "cancelled"
    message: str | None = None
    error: str | None = None
    last_url: str | None = None

    browser: str = "chromium"  # chromium | edge | chrome
    profile_mode: str = "temp"  # temp | system
    profile_id: str | None = None  # e.g. edge:Default
    profile_directory: str | None = None  # Default | Profile 1 | ...

    session_dir: Path | None = None
    storage_path: Path | None = None
    user_data_dir: Path | None = None  # persistent-context user data dir

    # Internal (thread-only)
    _cmd_q: "queue.Queue[tuple[Any, ...]] | None" = None
    _thread: "threading.Thread | None" = None

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "created_at_iso": self.created_at_iso,
            "state": self.state,
            "message": self.message,
            "error": self.error,
            "last_url": self.last_url,
            "browser": self.browser,
            "profile_mode": self.profile_mode,
            "profile_id": self.profile_id,
            "profile_directory": self.profile_directory,
        }


class LoginSessionManager:
    """Manage interactive Playwright login sessions for NotebookLM cookies.

    Uses Playwright Sync API in a background thread to avoid Windows asyncio
    event-loop limitations (e.g. uvicorn --reload on Windows uses SelectorEventLoop
    which breaks Playwright async subprocesses).
    """

    def __init__(self, sessions_dir: Path):
        self._sessions_dir = sessions_dir
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, LoginSession] = {}

    async def list_public(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.to_public() for s in self._sessions.values()]

    async def get_public(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            s = self._sessions.get(session_id)
            return s.to_public() if s else None

    async def start(
        self,
        name: str,
        browser: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise RuntimeError(
                'Playwright not installed. Install with: pip install "notebooklm-py[browser]"'
            ) from e

        session_id = secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        session_dir = self._sessions_dir / session_id
        storage_path = session_dir / "storage_state.json"

        chosen_browser = _normalize_browser(browser)
        chosen_profile_id: str | None = None
        profile_mode = "temp"
        profile_directory: str | None = None
        user_data_dir = session_dir / "browser_profile"

        if profile_id:
            parsed_browser, parsed_profile_dir = parse_profile_id(profile_id)
            if parsed_browser == "firefox":
                raise RuntimeError(
                    "Firefox Profile 仅支持离线导入 Cookie；浏览器登录添加请使用 Edge、Chrome 或 Playwright Chromium。"
                )
            chosen_browser = parsed_browser
            chosen_profile_id = f"{parsed_browser}:{parsed_profile_dir}"
            profile_mode = "system"
            profile_directory = parsed_profile_dir

            system_user_data_dir = get_user_data_dir(parsed_browser)
            if not system_user_data_dir or not system_user_data_dir.exists():
                raise RuntimeError(f"{parsed_browser} user-data-dir not found")
            if not (system_user_data_dir / parsed_profile_dir).exists():
                raise RuntimeError(f"browser profile dir not found: {chosen_profile_id}")
            user_data_dir = system_user_data_dir

        cmd_q: "queue.Queue[tuple[Any, ...]]" = queue.Queue()
        session = LoginSession(
            id=session_id,
            name=_safe_name(name),
            created_at_iso=_now_iso(),
            state="starting",
            message="准备启动浏览器…",
            browser=chosen_browser,
            profile_mode=profile_mode,
            profile_id=chosen_profile_id,
            profile_directory=profile_directory,
            session_dir=session_dir,
            user_data_dir=user_data_dir,
            storage_path=storage_path,
            _cmd_q=cmd_q,
        )

        t = threading.Thread(
            target=self._open_browser_thread,
            args=(session_id,),
            name=f"login-session-{session_id}",
            daemon=True,
        )
        session._thread = t

        with self._lock:
            self._sessions[session_id] = session

        t.start()
        return session.to_public()

    def _set_session(
        self,
        session_id: str,
        *,
        state: str | None = None,
        message: str | None = None,
        error: str | None = None,
        last_url: str | None = None,
    ) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            if state is not None:
                session.state = state
            if message is not None:
                session.message = message
            if error is not None:
                session.error = error
            if last_url is not None:
                session.last_url = last_url

    def _open_browser_thread(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            cmd_q = session._cmd_q
            user_data_dir = session.user_data_dir
            storage_path = session.storage_path
            profile_mode = session.profile_mode
            profile_directory = session.profile_directory
            browser = session.browser

        if not cmd_q or not user_data_dir or not storage_path:
            self._set_session(session_id, state="error", message="启动浏览器失败。", error="internal session missing")
            return

        try:
            if browser == "chromium":
                self._set_session(session_id, message="正在准备 Playwright Chromium（首次可能需要几分钟）…")
            elif profile_mode == "system":
                self._set_session(
                    session_id,
                    message="正在启动浏览器（复用本机 Profile；如提示占用，请先关闭浏览器窗口）…",
                )
            else:
                self._set_session(session_id, message="正在启动浏览器…")

            channel = _browser_channel(browser)
            if channel is None:
                _ensure_chromium_installed()

            # Ensure dirs exist
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or not session.session_dir:
                    return
                session.session_dir.mkdir(parents=True, exist_ok=True)
                if session.profile_mode == "temp":
                    session.user_data_dir.mkdir(parents=True, exist_ok=True)

            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            context = None
            try:
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                ]
                if profile_mode == "system" and profile_directory:
                    launch_args.append(f"--profile-directory={profile_directory}")

                launch_kwargs: dict[str, Any] = {
                    "user_data_dir": str(user_data_dir),
                    "headless": False,
                    "args": launch_args,
                    "ignore_default_args": ["--enable-automation"],
                }
                if channel is not None:
                    launch_kwargs["channel"] = channel

                context = playwright.chromium.launch_persistent_context(**launch_kwargs)

                page = context.new_page()
                page.goto("https://notebooklm.google.com/", wait_until="domcontentloaded")

                self._set_session(
                    session_id,
                    state="waiting_login",
                    last_url=page.url,
                    message="浏览器已打开：完成 Google 登录并停留在 NotebookLM 首页，然后点击“完成保存”。",
                )

                # Command loop (finish/cancel)
                while True:
                    item = cmd_q.get()
                    if not item:
                        continue
                    cmd = str(item[0])
                    if cmd == "finish":
                        _, force, fut = item
                        force = bool(force)
                        fut = fut if isinstance(fut, Future) else None
                        try:
                            current_url = page.url
                            if "notebooklm.google.com" not in current_url and not force:
                                raise ValueError(
                                    json.dumps(
                                        {
                                            "code": "not_on_notebooklm",
                                            "message": "Current page is not NotebookLM. Ensure you see the NotebookLM homepage, or force-save.",
                                            "current_url": current_url,
                                        },
                                        ensure_ascii=False,
                                    )
                                )
                            context.storage_state(path=str(storage_path))
                            if fut:
                                fut.set_result(True)
                        except Exception as e:
                            if fut:
                                fut.set_exception(e)
                        finally:
                            break
                    elif cmd == "cancel":
                        _, fut = item
                        fut = fut if isinstance(fut, Future) else None
                        if fut:
                            fut.set_result(True)
                        self._set_session(session_id, state="cancelled", message="已取消。")
                        break
            finally:
                try:
                    if context:
                        context.close()
                finally:
                    playwright.stop()

        except Exception as e:
            err = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
            self._set_session(
                session_id,
                state="error",
                error=_short(err, limit=8000),
                message=_friendly_launch_error(err) or "启动浏览器失败。",
            )

    async def finish(self, session_id: str, force: bool = False) -> tuple[str, bytes]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError("session not found")
            name = session.name
            storage_path = session.storage_path
            cmd_q = session._cmd_q
            t = session._thread
            state = session.state

        if not storage_path or not cmd_q or not t:
            raise RuntimeError("session is not ready (browser not started)")
        if state != "waiting_login":
            raise RuntimeError("session is not ready (waiting for browser)")

        fut: Future[bool] = Future()
        cmd_q.put(("finish", bool(force), fut))
        await asyncio.to_thread(fut.result, 600.0)
        await asyncio.to_thread(t.join, 10.0)

        raw = storage_path.read_bytes() if storage_path.exists() else b""
        if len(raw) < 100:
            raise RuntimeError("storage_state.json seems too small; login likely not completed")

        # Minimal cookie validation (SID present)
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
            cookies = parsed.get("cookies", [])
            names = {c.get("name") for c in cookies if isinstance(c, dict)}
            if "SID" not in names:
                raise RuntimeError("SID cookie not found; login likely not completed")
        except Exception as e:
            raise RuntimeError(f"invalid storage_state.json: {e}") from e

        await self._cleanup(session_id)
        return name, raw

    async def cancel(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            cmd_q = session._cmd_q
            t = session._thread
            session.state = "cancelled"
            session.message = "已取消。"

        if cmd_q and t:
            fut: Future[bool] = Future()
            cmd_q.put(("cancel", fut))
            try:
                await asyncio.to_thread(fut.result, 10.0)
            except Exception:
                pass
            await asyncio.to_thread(t.join, 10.0)

        await self._cleanup(session_id)
        return True

    async def _cleanup(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if not session or not session.session_dir:
            return
        try:
            shutil.rmtree(session.session_dir, ignore_errors=True)
        except Exception:
            pass
