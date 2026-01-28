from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .accounts_store import AccountsStore
from .config import get_paths
from .jobs import JobConfig, JobManager
from .login_sessions import LoginSessionManager

paths = get_paths()
paths.data_dir.mkdir(parents=True, exist_ok=True)
paths.accounts_dir.mkdir(parents=True, exist_ok=True)
paths.jobs_dir.mkdir(parents=True, exist_ok=True)

accounts_store = AccountsStore(paths)
job_manager = JobManager(paths, accounts_store)
login_manager = LoginSessionManager(paths.data_dir / "login_sessions")
prompts_path = paths.data_dir / "prompts.json"

app = FastAPI(title="Podcast Studio (NotebookLM)")

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_prompts() -> dict[str, Any]:
    if not prompts_path.exists():
        return {}
    try:
        return json.loads(prompts_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_prompts(data: dict[str, Any]) -> None:
    prompts_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (static_dir / "index.html").read_text(encoding="utf-8")


# =============================================================================
# Accounts
# =============================================================================


@app.get("/api/accounts")
async def list_accounts() -> list[dict[str, Any]]:
    return [a.__dict__ for a in accounts_store.list()]


@app.post("/api/accounts")
async def add_account(
    name: str = Form(...),
    storage_state: UploadFile = File(...),
) -> dict[str, Any]:
    raw = await storage_state.read()
    if len(raw) < 100:
        raise HTTPException(status_code=400, detail="storage_state.json seems too small")
    account = accounts_store.add(name=name.strip(), storage_state_bytes=raw, created_at_iso=_now_iso())
    return account.__dict__


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: str) -> dict[str, Any]:
    ok = accounts_store.delete(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="account not found")
    return {"ok": True}


@app.post("/api/accounts/{account_id}/verify")
async def verify_account(account_id: str) -> dict[str, Any]:
    account = accounts_store.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")

    from notebooklm import NotebookLMClient

    try:
        async with await NotebookLMClient.from_storage(account.storage_path) as client:
            notebooks = await client.notebooks.list()
        return {"ok": True, "account_id": account_id, "notebooks": len(notebooks)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class StartLoginRequest(BaseModel):
    name: str
    browser: str | None = None  # chromium | edge | chrome
    profile_id: str | None = None  # e.g. "edge:Default" / "chrome:Profile 1"


class ImportFromProfileRequest(BaseModel):
    name: str
    profile_id: str


@app.get("/api/browser/profiles")
async def list_browser_profiles() -> list[dict[str, Any]]:
    from .utils.browser_profiles import list_browser_profiles

    return [p.to_public() for p in list_browser_profiles()]


@app.get("/api/accounts/login/sessions")
async def list_login_sessions() -> list[dict[str, Any]]:
    return await login_manager.list_public()


@app.post("/api/accounts/login/start")
async def start_login(req: StartLoginRequest) -> dict[str, Any]:
    try:
        return await login_manager.start(req.name, browser=req.browser, profile_id=req.profile_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# =============================================================================
# Prompts
# =============================================================================


class SavePromptRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=10)


@app.get("/api/prompts/fixed")
async def get_fixed_prompt() -> dict[str, Any]:
    data = _read_prompts()
    fixed = data.get("fixed") if isinstance(data.get("fixed"), dict) else {}
    return {
        "name": fixed.get("name", ""),
        "content": fixed.get("content", ""),
        "updated_at": fixed.get("updated_at"),
    }


@app.post("/api/prompts/fixed")
async def save_fixed_prompt(req: SavePromptRequest) -> dict[str, Any]:
    name = req.name.strip()
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is empty")
    fixed = {"name": name, "content": content, "updated_at": _now_iso()}
    data = _read_prompts()
    data["fixed"] = fixed
    _write_prompts(data)
    return fixed


class SaveSplitPromptRequest(BaseModel):
    parts: list[str] = Field(min_length=1, max_length=10)


@app.get("/api/prompts/split")
async def get_split_prompts() -> dict[str, Any]:
    data = _read_prompts()
    split = data.get("split") if isinstance(data.get("split"), dict) else {}
    parts = split.get("parts")
    if not isinstance(parts, list):
        parts = []
    return {"parts": parts, "updated_at": split.get("updated_at")}


@app.post("/api/prompts/split")
async def save_split_prompts(req: SaveSplitPromptRequest) -> dict[str, Any]:
    parts = [str(p or "") for p in (req.parts or [])][:10]
    split = {"parts": parts, "updated_at": _now_iso()}
    data = _read_prompts()
    data["split"] = split
    _write_prompts(data)
    return split


@app.post("/api/accounts/import/profile")
async def import_account_from_profile(req: ImportFromProfileRequest) -> dict[str, Any]:
    from .utils.browser_cookies import export_storage_state_from_profile_id

    try:
        exported = export_storage_state_from_profile_id(req.profile_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    account = accounts_store.add(
        name=req.name.strip(),
        storage_state_bytes=exported.storage_state_bytes,
        created_at_iso=_now_iso(),
    )
    return {
        **account.__dict__,
        "imported": {"cookie_count": exported.cookie_count, "ts": exported.ts_iso},
    }


@app.post("/api/accounts/login/{session_id}/finish")
async def finish_login(session_id: str, force: bool = False) -> dict[str, Any]:
    try:
        name, raw = await login_manager.finish(session_id, force=force)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        # ValueError may embed JSON with extra info (e.g. needs force)
        try:
            payload = json.loads(str(e))
            raise HTTPException(status_code=400, detail=payload) from e
        except Exception:
            raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    account = accounts_store.add(name=name.strip(), storage_state_bytes=raw, created_at_iso=_now_iso())
    return account.__dict__


@app.post("/api/accounts/login/{session_id}/cancel")
async def cancel_login(session_id: str) -> dict[str, Any]:
    ok = await login_manager.cancel(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


# =============================================================================
# Jobs
# =============================================================================


@app.post("/api/jobs")
async def create_job(
    config: str = Form(...),
    report_text: str | None = Form(None),
    report_file: UploadFile | None = File(None),
) -> dict[str, Any]:
    try:
        config_obj = JobConfig.model_validate_json(config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid config: {e}") from e

    if report_text is None and report_file is None:
        raise HTTPException(status_code=400, detail="Provide report_text or report_file")
    if report_text is None:
        report_bytes = await report_file.read() if report_file else b""
        report_text = report_bytes.decode("utf-8", errors="replace")

    report_text = report_text.strip()
    if len(report_text) < 200:
        raise HTTPException(status_code=400, detail="Report too short (min 200 chars)")

    job = await job_manager.create_and_start_job(config_obj, report_text)
    return job.to_public_dict()


@app.get("/api/jobs")
async def list_jobs() -> list[dict[str, Any]]:
    return [j.to_public_dict() for j in job_manager.list()]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_public_dict()


@app.get("/api/jobs/{job_id}/event_log")
async def get_job_event_log(job_id: str, limit: int = 2000) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    lim = max(1, min(int(limit), 10000))
    return {"job_id": job_id, "events": job.event_log[-lim:]}


@app.get("/api/jobs/{job_id}/events.jsonl")
async def download_job_events(job_id: str) -> FileResponse:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    base = job.job_dir.resolve()
    path = (base / "events.jsonl").resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=400, detail="invalid path")
    if not path.exists():
        raise HTTPException(status_code=404, detail="events not found")
    return FileResponse(path, filename=f"{job_id}_events.jsonl", media_type="text/plain")


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job.cancel()
    return {"ok": True}


class StitchRequest(BaseModel):
    episode: int = 1
    parts: dict[str, str] = Field(default_factory=dict)


@app.post("/api/jobs/{job_id}/stitch")
async def stitch_job(job_id: str, req: StitchRequest) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not bool(getattr(job.config, "split_enabled", False)):
        raise HTTPException(status_code=400, detail="job is not split_enabled")

    episode = max(1, int(req.episode or 1))
    raw = req.parts if isinstance(req.parts, dict) else {}
    if not raw:
        raise HTTPException(status_code=400, detail="parts is empty")

    selection: dict[int, str] = {}
    for k, v in raw.items():
        try:
            part_idx = int(k)
        except Exception:
            continue
        name = str(v or "").strip()
        if part_idx <= 0 or not name:
            continue
        selection[part_idx] = name

    segments = int(getattr(job.config, "split_segments", 3) or 3)
    raw_candidates = getattr(job.config, "split_candidates_per_part", []) or []
    candidates_per_part: list[int] = []
    for i in range(max(1, segments)):
        try:
            n = int(raw_candidates[i]) if i < len(raw_candidates) else 1
        except Exception:
            n = 1
        if n < 0:
            n = 1
        candidates_per_part.append(min(n, 20))
    enabled_parts = {i + 1 for i, n in enumerate(candidates_per_part) if int(n or 0) > 0}
    if not enabled_parts:
        raise HTTPException(status_code=400, detail="all parts are disabled (candidates_per_part are 0)")
    if set(selection.keys()) != enabled_parts:
        raise HTTPException(
            status_code=400,
            detail=f"must select exactly these parts: {sorted(enabled_parts)}",
        )

    base = job.outputs_dir.resolve()
    for part_idx, filename in selection.items():
        path = (base / filename).resolve()
        if not path.is_relative_to(base):
            raise HTTPException(status_code=400, detail=f"invalid filename: {filename}")
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"file not found: {filename}")

        ok = False
        for ev in reversed(job.event_log[-20000:]):
            if not isinstance(ev, dict):
                continue
            if str(ev.get("type") or "") != "part_accepted":
                continue
            if str(ev.get("file") or "") != filename:
                continue
            try:
                ev_part = int(ev.get("part") or 0)
            except Exception:
                ev_part = 0
            try:
                ev_ep = int(ev.get("episode") or 1)
            except Exception:
                ev_ep = 1
            if ev_part == part_idx and ev_ep == episode:
                ok = True
                break
        if not ok:
            raise HTTPException(status_code=400, detail=f"file not a valid candidate: {filename}")

    await job.publish(
        {
            "type": "split_stitch_selection_submitted",
            "ts": _now_iso(),
            "job_id": job.id,
            "episode": episode,
            "parts": selection,
        }
    )
    await job.submit_stitch_selection(episode, selection)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        queue = await job.subscribe()
        try:
            # First event: snapshot
            yield f"data: {json.dumps({'type': 'snapshot', 'job': job.to_public_dict()}, ensure_ascii=False)}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            await job.unsubscribe(queue)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/download/{job_id}/{filename}")
async def download_file(job_id: str, filename: str) -> FileResponse:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    base = job.outputs_dir.resolve()
    path = (base / filename).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=400, detail="invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)
