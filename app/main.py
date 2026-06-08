from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .accounts_store import AccountsStore
from .account_keepalive import AccountKeepaliveService
from .concat_service import ConcatService
from .config import get_paths
from .jobs import JobConfig, JobManager
from .notebooklm_health import check_account_health
from .report_explain_service import ReportExplainService
from .utils.audio_concat import concat_audio, concat_audio_with_transitions
from .utils.audio_duration import get_audio_duration
from .utils.silence_detect import detect_silence_segments, segments_to_payload
from .utils.waveform import compute_waveform_peaks
from .utils.document_parse import extract_text_from_bytes
from .login_sessions import LoginSessionManager

paths = get_paths()
paths.data_dir.mkdir(parents=True, exist_ok=True)
paths.accounts_dir.mkdir(parents=True, exist_ok=True)
paths.jobs_dir.mkdir(parents=True, exist_ok=True)

accounts_store = AccountsStore(paths)
job_manager = JobManager(paths, accounts_store)
login_manager = LoginSessionManager(paths.data_dir / "login_sessions")
account_keepalive = AccountKeepaliveService(accounts_store)
prompts_path = paths.data_dir / "prompts.json"
concat_service = ConcatService(paths)
report_explain_service = ReportExplainService(paths)
default_prompts_path = paths.base_dir / "assets" / "prompts.default.json"
default_report_explain_prompt_path = paths.base_dir / "报告解说提示词.txt"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Do not run periodic cookie keepalive on startup. Auth recovery is handled
    # on demand from the account-owned browser profile saved during login.
    try:
        yield
    finally:
        await account_keepalive.stop()


app = FastAPI(title="Podcast Studio (NotebookLM)", lifespan=lifespan)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
concat_static_dir = static_dir / "concat"
report_explain_static_dir = static_dir / "report_explain"

SHANGHAI_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat()


def _read_prompts() -> dict[str, Any]:
    if not prompts_path.exists():
        if default_prompts_path.exists():
            try:
                prompts_path.write_text(
                    default_prompts_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
            except Exception:
                pass
        else:
            return {}
    try:
        return json.loads(prompts_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_prompts(data: dict[str, Any]) -> None:
    prompts_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _sanitize_filename(name: str) -> str:
    name = Path(name).name
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    return safe or "transition_audio"


def _form_to_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


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
    profile_id: str | None = Form(default=None),
) -> dict[str, Any]:
    raw = await storage_state.read()
    if len(raw) < 100:
        raise HTTPException(status_code=400, detail="storage_state.json seems too small")
    clean_profile_id = (profile_id or "").strip() or None
    if clean_profile_id:
        from .utils.browser_profiles import parse_profile_id

        try:
            parse_profile_id(clean_profile_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not clean_profile_id.lower().startswith("firefox:"):
            raise HTTPException(status_code=400, detail="Only Firefox Profile can be bound for automatic recovery")
    account = accounts_store.add(
        name=name.strip(),
        storage_state_bytes=raw,
        created_at_iso=_now_iso(),
        profile_id=clean_profile_id,
    )
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

    result = await check_account_health(account)
    if result.get("ok"):
        return result
    raise HTTPException(status_code=400, detail=result)


@app.post("/api/accounts/{account_id}/health")
async def check_account(account_id: str) -> dict[str, Any]:
    account = accounts_store.get(account_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return await check_account_health(account)


@app.get("/api/accounts/health")
async def check_accounts() -> list[dict[str, Any]]:
    # Run sequentially to avoid turning a health check into a rate-limit trigger.
    results: list[dict[str, Any]] = []
    for account in accounts_store.list():
        results.append(await check_account_health(account))
    return results


@app.get("/api/accounts/keepalive")
async def get_accounts_keepalive() -> dict[str, Any]:
    return account_keepalive.status()


@app.post("/api/accounts/keepalive/run")
async def run_accounts_keepalive() -> dict[str, Any]:
    results = await account_keepalive.run_once()
    return {"ok": all(r.ok for r in results), "results": [r.to_dict() for r in results]}


@app.post("/api/accounts/recover/run")
async def run_accounts_recovery() -> dict[str, Any]:
    results = await account_keepalive.run_once()
    return {"ok": all(r.ok for r in results), "results": [r.to_dict() for r in results]}


@app.get("/api/accounts/keepalive/run")
async def run_accounts_keepalive_hint() -> dict[str, Any]:
    return {
        "ok": False,
        "message": "Periodic keepalive is disabled. This endpoint requires POST for one-shot recovery. New browser-login accounts recover from their saved browser profile.",
        "status_url": "/api/accounts/keepalive",
        "powershell": "Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/accounts/recover/run",
    }


class StartLoginRequest(BaseModel):
    name: str
    browser: str | None = None  # chromium | edge | chrome
    profile_id: str | None = None  # browser login only supports Edge/Chrome profile reuse


class ImportFromProfileRequest(BaseModel):
    name: str
    profile_id: str


class AccountProfileRequest(BaseModel):
    profile_id: str | None = None


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


@app.post("/api/transitions/upload")
async def upload_transition_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="file is missing")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="file is empty")

    transitions_dir = paths.data_dir / "transitions"
    transitions_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitize_filename(file.filename)
    if not safe_name.lower().endswith((".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac")):
        # keep original extension if any, otherwise default to .wav
        ext = Path(file.filename).suffix
        safe_name = safe_name + (ext if ext else ".wav")

    dest = transitions_dir / safe_name
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        for i in range(2, 1000):
            candidate = transitions_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                break

    dest.write_bytes(raw)
    rel = dest.relative_to(paths.base_dir)
    return {"ok": True, "path": str(rel)}


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
        profile_id=req.profile_id if req.profile_id.lower().startswith("firefox:") else None,
    )
    return {
        **account.__dict__,
        "imported": {"cookie_count": exported.cookie_count, "ts": exported.ts_iso},
    }


@app.post("/api/accounts/login/{session_id}/finish")
async def finish_login(session_id: str, force: bool = False) -> dict[str, Any]:
    try:
        name, raw, profile_id, browser, profile_mode, user_data_dir = await login_manager.finish(
            session_id, force=force
        )
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

    try:
        browser_profile_source = Path(user_data_dir) if profile_mode == "temp" and user_data_dir else None
        account = accounts_store.add(
            name=name.strip(),
            storage_state_bytes=raw,
            created_at_iso=_now_iso(),
            profile_id=profile_id if profile_id and profile_id.lower().startswith("firefox:") else None,
            browser_profile_source=browser_profile_source,
            browser=browser,
        )
        return account.__dict__
    finally:
        await login_manager.cleanup_session(session_id)


@app.post("/api/accounts/{account_id}/profile")
async def bind_account_profile(account_id: str, req: AccountProfileRequest) -> dict[str, Any]:
    profile_id = (req.profile_id or "").strip() or None
    if profile_id:
        from .utils.browser_profiles import parse_profile_id

        try:
            parse_profile_id(profile_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not profile_id.lower().startswith("firefox:"):
            raise HTTPException(status_code=400, detail="Only Firefox Profile can be bound for automatic recovery")

    account = accounts_store.set_profile_id(account_id, profile_id)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
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
        ext = Path(report_file.filename or "").suffix.lower() if report_file else ""
        try:
            if ext in {".txt", ".md", ".text", ".pdf", ".docx"}:
                report_text = extract_text_from_bytes(report_bytes, ext)
            else:
                report_text = report_bytes.decode("utf-8", errors="replace")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e) or type(e).__name__)

    report_text = report_text.strip()
    if len(report_text) < 200:
        raise HTTPException(status_code=400, detail="Report too short (min 200 chars)")

    job = await job_manager.create_and_start_job(config_obj, report_text)
    return job.to_public_dict()


@app.post("/api/parse-file")
async def parse_report_file(file: UploadFile = File(...)) -> dict[str, Any]:
    name = str(file.filename or "")
    ext = Path(name).suffix.lower()
    if not ext:
        raise HTTPException(status_code=400, detail="file has no extension")
    if ext == ".doc":
        raise HTTPException(status_code=400, detail="doc format not supported; please convert to docx or pdf")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="file is empty")

    try:
        text = extract_text_from_bytes(data, ext)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e) or type(e).__name__)

    return {
        "ok": True,
        "filename": name,
        "ext": ext,
        "chars": len(text),
        "text": text,
    }


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


@app.get("/api/jobs/{job_id}/waveform")
async def get_job_waveform(
    job_id: str,
    file: str,
    points: int = 1200,
    min_silence_s: float | None = None,
    threshold_db: float | None = None,
) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    name = Path(file or "").name
    if not name:
        raise HTTPException(status_code=400, detail="file is empty")

    base = job.outputs_dir.resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(status_code=400, detail="invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")

    points = int(points or 1200)
    if points < 200:
        points = 200
    if points > 5000:
        points = 5000

    cfg = getattr(job, "config", None)
    min_silence = (
        float(min_silence_s)
        if min_silence_s is not None
        else float(getattr(cfg, "silence_min_duration_s", 5.0))
    )
    threshold = (
        float(threshold_db)
        if threshold_db is not None
        else float(getattr(cfg, "silence_threshold_db", -50.0))
    )

    cache_dir = base / ".waveforms"
    cache_dir.mkdir(exist_ok=True)
    cache_key = _sanitize_filename(f"{name}_{points}_{min_silence}_{threshold}")
    cache_path = cache_dir / f"{cache_key}.json"
    audio_mtime = path.stat().st_mtime

    if cache_path.exists() and cache_path.stat().st_mtime >= audio_mtime:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _compute() -> dict[str, Any]:
        peaks, duration = compute_waveform_peaks(path, points=points, sample_rate=2000)
        silence_segments: list[dict[str, float | str]] = []
        silence_error: str | None = None
        try:
            segments = detect_silence_segments(
                path, min_duration_s=min_silence, threshold_db=threshold
            )
            silence_segments = segments_to_payload(segments)
        except Exception as e:
            silence_error = str(e) or type(e).__name__
        payload: dict[str, Any] = {
            "ok": True,
            "file": name,
            "points": points,
            "peaks": peaks,
            "duration_seconds": round(float(duration), 3),
            "silence_segments": silence_segments,
            "min_silence_duration_s": min_silence,
            "threshold_db": threshold,
        }
        if silence_error:
            payload["silence_error"] = silence_error
        return payload

    data = await asyncio.to_thread(_compute)
    try:
        cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return data


@app.get("/concat/api/jobs/{job_id}/waveform")
async def get_job_waveform_concat(
    job_id: str,
    file: str,
    points: int = 1200,
    min_silence_s: float | None = None,
    threshold_db: float | None = None,
) -> dict[str, Any]:
    return await get_job_waveform(
        job_id=job_id,
        file=file,
        points=points,
        min_silence_s=min_silence_s,
        threshold_db=threshold_db,
    )


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


@app.post("/api/jobs/{job_id}/stop-and-stitch")
async def stop_and_stitch(job_id: str, req: StopAndStitchRequest) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.state not in {"running", "queued", "waiting_selection"}:
        raise HTTPException(status_code=400, detail="job is not running")
    if not bool(getattr(job.config, "split_enabled", False)):
        raise HTTPException(status_code=400, detail="job is not split_enabled")

    segments = int(getattr(job.config, "split_segments", 3) or 3)
    raw_candidates = getattr(job.config, "split_candidates_per_part", []) or []
    enabled_parts = set()
    for i in range(max(1, segments)):
        try:
            n = int(raw_candidates[i]) if i < len(raw_candidates) else 1
        except Exception:
            n = 1
        if n > 0:
            enabled_parts.add(i + 1)
    if not enabled_parts:
        raise HTTPException(status_code=400, detail="all parts are disabled (candidates_per_part are 0)")

    accepted_parts: set[int] = set()
    for ev in reversed(job.event_log):
        if not isinstance(ev, dict):
            continue
        if str(ev.get("type") or "") != "part_accepted":
            continue
        try:
            part_idx = int(ev.get("part") or 0)
        except Exception:
            part_idx = 0
        if part_idx in enabled_parts:
            accepted_parts.add(part_idx)
            if accepted_parts == enabled_parts:
                break
    missing = sorted(enabled_parts - accepted_parts)
    if missing:
        raise HTTPException(status_code=409, detail=f"missing accepted parts: {missing}")

    job.request_stop(req.mode)
    await job.publish(
        {
            "type": "split_stop_requested",
            "ts": _now_iso(),
            "job_id": job.id,
            "mode": job.stop_mode,
            "parts": sorted(enabled_parts),
        }
    )
    return {"ok": True, "mode": job.stop_mode}


class StitchRequest(BaseModel):
    episode: int = 1
    parts: dict[str, str] = Field(default_factory=dict)


class StopAndStitchRequest(BaseModel):
    mode: str | None = None  # auto | manual


class ConcatImportRequest(BaseModel):
    job_id: str
    file: str
    repeat: int | None = None
    quality: int | None = None
    output_name: str | None = None


class ConcatImportOutputRequest(BaseModel):
    file: str
    repeat: int | None = None
    quality: int | None = None
    output_name: str | None = None


class ConcatStitchPartsRequest(BaseModel):
    job_id: str
    parts: list[str]
    output_name: str | None = None
    output_format: str | None = "m4a"
    transition_enabled: bool = False
    transition_fade_seconds: float = 3.0
    transition_files: list[str] = Field(default_factory=list)
    transition_repeats: list[int] = Field(default_factory=list)
    transition_durations: list[float] = Field(default_factory=list)


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


# =============================================================================
# Report Explain Tool
# =============================================================================


@app.get("/report-explain", response_class=HTMLResponse)
@app.get("/report-explain/", response_class=HTMLResponse)
async def report_explain_index() -> str:
    path = report_explain_static_dir / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report explain ui not found")
    return path.read_text(encoding="utf-8")


@app.get("/report-explain/result/{job_id}", response_class=HTMLResponse)
async def report_explain_result_page(job_id: str) -> str:
    if not report_explain_service.get_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    path = report_explain_static_dir / "result.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report explain result ui not found")
    return path.read_text(encoding="utf-8")


@app.get("/report-explain/app.js")
async def report_explain_app_js() -> FileResponse:
    path = report_explain_static_dir / "app.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report explain app.js not found")
    return FileResponse(path, media_type="application/javascript")


@app.get("/report-explain/result.js")
async def report_explain_result_js() -> FileResponse:
    path = report_explain_static_dir / "result.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report explain result.js not found")
    return FileResponse(path, media_type="application/javascript")


@app.get("/report-explain/style.css")
async def report_explain_style_css() -> FileResponse:
    path = report_explain_static_dir / "style.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="report explain style.css not found")
    return FileResponse(path, media_type="text/css")


@app.get("/report-explain/api/default-prompt")
async def report_explain_default_prompt() -> dict[str, Any]:
    if not default_report_explain_prompt_path.exists():
        raise HTTPException(status_code=404, detail="default prompt file not found")
    text = default_report_explain_prompt_path.read_text(encoding="utf-8")
    return {
        "ok": True,
        "promptText": text,
        "promptPath": str(default_report_explain_prompt_path),
        "promptChars": len(text),
    }


@app.get("/report-explain/api/jobs")
async def report_explain_list_jobs(limit: int = 12) -> dict[str, Any]:
    items = [job.to_summary_dict() for job in report_explain_service.list_jobs(limit=limit)]
    return {"ok": True, "items": items}


@app.post("/report-explain/api/jobs/{job_id}/rerun")
async def report_explain_rerun_job(
    job_id: str,
    prompt_text: str = Form(""),
    output_name: str = Form(""),
    export_pdf: str = Form(""),
) -> dict[str, Any]:
    source_job = report_explain_service.get_job(job_id)
    if not source_job:
        raise HTTPException(status_code=404, detail="job not found")

    prompt_value = str(prompt_text or "").strip()
    if prompt_value and len(prompt_value) < 50:
        raise HTTPException(status_code=400, detail="prompt is too short")
    export_pdf_value = _form_to_bool(export_pdf, source_job.export_pdf)

    try:
        new_job = report_explain_service.rerun_job(
            job_id,
            prompt_text=prompt_value or None,
            output_name=output_name,
            export_pdf=export_pdf_value,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e) or "job not found") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e) or type(e).__name__) from e
    return new_job.to_public_dict()


@app.post("/report-explain/api/jobs")
async def report_explain_create_job(
    report_file: UploadFile = File(...),
    prompt_text: str = Form(""),
    output_name: str = Form(""),
    export_pdf: str = Form("true"),
) -> dict[str, Any]:
    if not report_file.filename:
        raise HTTPException(status_code=400, detail="missing upload file: report_file")

    ext = Path(report_file.filename).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="only PDF files are supported")

    data = await report_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded PDF is empty")

    try:
        report_text = extract_text_from_bytes(data, ext)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e) or type(e).__name__) from e

    report_text = report_text.strip()
    if len(report_text) < 200:
        raise HTTPException(status_code=400, detail="extracted PDF text is too short (under 200 chars)")

    prompt_value = str(prompt_text or "").strip()
    if not prompt_value:
        if not default_report_explain_prompt_path.exists():
            raise HTTPException(status_code=400, detail="prompt is empty and default prompt file is missing")
        prompt_value = default_report_explain_prompt_path.read_text(encoding="utf-8").strip()
    if len(prompt_value) < 50:
        raise HTTPException(status_code=400, detail="prompt is too short")
    export_pdf_value = _form_to_bool(export_pdf, True)

    job = report_explain_service.create_job(
        source_filename=report_file.filename,
        source_file_bytes=data,
        source_text=report_text,
        prompt_text=prompt_value,
        output_name=output_name,
        export_pdf=export_pdf_value,
    )
    return job.to_public_dict()


@app.get("/report-explain/api/jobs/{job_id}")
async def report_explain_job_status(job_id: str) -> dict[str, Any]:
    job = report_explain_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_public_dict()


@app.get("/report-explain/api/jobs/{job_id}/detail")
async def report_explain_job_detail(job_id: str) -> dict[str, Any]:
    data = report_explain_service.get_job_detail(job_id)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return data


@app.get("/report-explain/api/jobs/{job_id}/logs")
async def report_explain_job_logs(
    job_id: str,
    max_chars: int = 24000,
    event_limit: int = 60,
) -> dict[str, Any]:
    data = report_explain_service.get_job_logs(job_id, max_chars=max_chars, event_limit=event_limit)
    if not data:
        raise HTTPException(status_code=404, detail="job not found")
    return data


@app.get("/report-explain/download/{filename}")
async def report_explain_download(filename: str) -> FileResponse:
    safe = Path(filename).name
    path = report_explain_service.output_dir / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media_type, filename=safe)


@app.get("/report-explain/preview/{filename}")
async def report_explain_preview(filename: str) -> FileResponse:
    safe = Path(filename).name
    path = report_explain_service.output_dir / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
    return FileResponse(path, media_type=media_type)


@app.post("/report-explain/api/open-output")
async def report_explain_open_output(file: str | None = None) -> dict[str, Any]:
    try:
        if file:
            safe = Path(file).name
            path = report_explain_service.output_dir / safe
            if path.exists():
                subprocess.Popen(["explorer.exe", f"/select,{path}"], shell=False)
                return {"ok": True}
        subprocess.Popen(["explorer.exe", str(report_explain_service.output_dir)], shell=False)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# =============================================================================
# Concat Tool (早间新闻拼接工作台)
# =============================================================================


@app.get("/concat", response_class=HTMLResponse)
@app.get("/concat/", response_class=HTMLResponse)
async def concat_index() -> str:
    path = concat_static_dir / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="concat ui not found")
    return path.read_text(encoding="utf-8")


@app.get("/concat/app.js")
async def concat_app_js() -> FileResponse:
    path = concat_static_dir / "app.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="concat app.js not found")
    return FileResponse(path, media_type="application/javascript")


@app.get("/concat/style.css")
async def concat_style_css() -> FileResponse:
    path = concat_static_dir / "style.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="concat style.css not found")
    return FileResponse(path, media_type="text/css")


@app.post("/concat/api/jobs")
async def concat_create_job(
    mainAudio: list[UploadFile] = File(...),
    repeat: str = Form("3"),
    quality: str = Form("5"),
    outputName: str = Form(""),
) -> dict[str, Any]:
    files = [f for f in (mainAudio or []) if f and f.filename]
    if not files:
        raise HTTPException(status_code=400, detail="未找到上传文件 mainAudio（可上传 1~10 个，按顺序合并）。")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="mainAudio 最多上传 10 个文件。")

    try:
        repeat_n = int(repeat)
    except Exception:
        repeat_n = 3
    if repeat_n < 1 or repeat_n > 20:
        raise HTTPException(status_code=400, detail="repeat 需在 1~20 之间。")

    try:
        quality_n = int(quality)
    except Exception:
        quality_n = 5
    if quality_n < 0 or quality_n > 9:
        raise HTTPException(status_code=400, detail="quality 需在 0~9 之间（数值越小质量越高）。")

    # create job dir early
    job_id = uuid.uuid4().hex
    job_dir = concat_service.jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    upload_paths: list[Path] = []
    for idx, f in enumerate(files, start=1):
        ext = Path(f.filename or "").suffix or ".audio"
        dest = job_dir / f"main_{idx}{ext}"
        with dest.open("wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        upload_paths.append(dest)

    loop = asyncio.get_running_loop()
    job = concat_service.create_job(
        upload_paths=upload_paths,
        repeat=repeat_n,
        quality=quality_n,
        output_name=outputName or "",
        loop=loop,
        job_id=job_id,
        job_dir=job_dir,
    )

    return {
        "ok": True,
        "jobId": job.id,
        "eventsUrl": f"/concat/api/jobs/{job.id}/events",
        "statusUrl": f"/concat/api/jobs/{job.id}",
        "outputFile": job.output_file,
        "outputPath": str(job.output_path),
        "downloadUrl": f"/concat/download/{job.output_file}",
    }


@app.get("/concat/api/jobs/{job_id}")
async def concat_job_status(job_id: str) -> dict[str, Any]:
    job = concat_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "ok": True,
        "jobId": job.id,
        "stage": job.stage,
        "message": job.message,
        "progress": job.progress,
        "done": job.done,
        "error": job.error,
        "outputFile": job.output_file,
        "outputPath": str(job.output_path),
        "downloadUrl": f"/concat/download/{job.output_file}",
        "durationSeconds": job.output_duration_seconds,
        "latestTxtPath": str(concat_service.latest_txt_path),
    }


@app.get("/concat/api/jobs/{job_id}/events")
async def concat_job_events(job_id: str) -> StreamingResponse:
    job = concat_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_gen():
        while True:
            ev = await job.events.get()
            if ev.get("type") == "__close__":
                break
            payload = json.dumps(ev.get("data") or {}, ensure_ascii=False)
            yield f"event: {ev.get('type')}\n"
            yield f"data: {payload}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/concat/download/{filename}")
async def concat_download(filename: str) -> FileResponse:
    safe = Path(filename).name
    path = concat_service.output_dir / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="audio/mpeg", filename=safe)


@app.get("/concat/api/fixed")
async def concat_fixed_list() -> dict[str, Any]:
    return {"ok": True, "items": concat_service.fixed_items()}


@app.get("/concat/fixed/{kind}")
async def concat_fixed_file(kind: str) -> FileResponse:
    path = {
        "intro": concat_service.intro_path,
        "outro": concat_service.outro_path,
        "tail": concat_service.tail_path,
    }.get(kind)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, media_type="audio/mpeg", filename=path.name)


@app.post("/concat/api/fixed/{kind}")
async def concat_fixed_upload(kind: str, file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="未找到上传文件 file。")
    tmp = concat_service.jobs_dir / f"{uuid.uuid4().hex}.upload.mp3"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    try:
        item = concat_service.replace_fixed(kind, tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e)) from e
    tmp.unlink(missing_ok=True)
    return {"ok": True, "item": item}


@app.post("/concat/api/open-output")
async def concat_open_output(file: str | None = None) -> dict[str, Any]:
    try:
        if file:
            safe = Path(file).name
            path = concat_service.output_dir / safe
            if path.exists():
                subprocess.Popen(["explorer.exe", f"/select,{path}"], shell=False)
                return {"ok": True}
        subprocess.Popen(["explorer.exe", str(concat_service.output_dir)], shell=False)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/concat/api/info")
async def concat_info() -> dict[str, Any]:
    return {
        "ok": True,
        "assetsDir": str(concat_service.assets_dir),
        "outputDir": str(concat_service.output_dir),
        "ffmpeg": concat_service.ffmpeg,
        "ffprobe": concat_service.ffprobe,
        "jobs": len(concat_service.jobs),
    }


@app.post("/concat/api/import")
async def concat_import(req: ConcatImportRequest) -> dict[str, Any]:
    job = job_manager.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    name = Path(req.file or "").name
    if not name:
        raise HTTPException(status_code=400, detail="file is empty")
    src = (job.outputs_dir / name).resolve()
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    repeat = int(req.repeat or 3)
    if repeat < 1 or repeat > 20:
        repeat = 3
    quality = int(req.quality or 5)
    if quality < 0 or quality > 9:
        quality = 5

    loop = asyncio.get_running_loop()
    concat_job = concat_service.create_job(
        upload_paths=[src],
        repeat=repeat,
        quality=quality,
        output_name=req.output_name or src.stem,
        loop=loop,
    )

    return {
        "ok": True,
        "jobId": concat_job.id,
        "outputFile": concat_job.output_file,
        "outputPath": str(concat_job.output_path),
        "eventsUrl": f"/concat/api/jobs/{concat_job.id}/events",
        "downloadUrl": f"/concat/download/{concat_job.output_file}",
    }


@app.post("/concat/api/import-output")
async def concat_import_output(req: ConcatImportOutputRequest) -> dict[str, Any]:
    name = Path(req.file or "").name
    if not name:
        raise HTTPException(status_code=400, detail="file is empty")
    src = (concat_service.output_dir / name).resolve()
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    repeat = int(req.repeat or 3)
    if repeat < 1 or repeat > 20:
        repeat = 3
    quality = int(req.quality or 5)
    if quality < 0 or quality > 9:
        quality = 5

    loop = asyncio.get_running_loop()
    concat_job = concat_service.create_job(
        upload_paths=[src],
        repeat=repeat,
        quality=quality,
        output_name=req.output_name or src.stem,
        loop=loop,
    )

    return {
        "ok": True,
        "jobId": concat_job.id,
        "outputFile": concat_job.output_file,
        "outputPath": str(concat_job.output_path),
        "eventsUrl": f"/concat/api/jobs/{concat_job.id}/events",
        "downloadUrl": f"/concat/download/{concat_job.output_file}",
    }


@app.get("/concat/api/stitch-parts/manual")
async def concat_stitch_parts_manual(job_id: str) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    base = job.outputs_dir.resolve()
    out: list[dict[str, Any]] = []

    for path in sorted(base.glob("manual_part*_*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        m = re.match(r"^manual_part(?P<part>\d+)_", path.name)
        if not m:
            continue
        try:
            part = int(m.group("part"))
        except Exception:
            continue
        if part <= 0:
            continue
        try:
            duration = get_audio_duration(path)
            duration_seconds = int(round(duration.seconds))
        except Exception:
            duration_seconds = 0
        out.append(
            {
                "part": part,
                "file": path.name,
                "durationSeconds": duration_seconds,
                "lastWriteUnixMs": int(path.stat().st_mtime * 1000),
                "downloadUrl": f"/download/{job.id}/{path.name}",
            }
        )

    return {"ok": True, "jobId": job.id, "items": out}


@app.post("/concat/api/stitch-parts/upload")
async def concat_stitch_parts_upload(
    job_id: str = Form(...),
    part: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not file.filename:
        raise HTTPException(status_code=400, detail="未找到上传文件 file。")

    try:
        part_n = int(part)
    except Exception:
        raise HTTPException(status_code=400, detail="part must be an integer") from None
    if part_n <= 0:
        raise HTTPException(status_code=400, detail="part must be >= 1")
    segments_n = int(getattr(job.config, "split_segments", 0) or 0)
    if segments_n > 0 and part_n > segments_n:
        raise HTTPException(status_code=400, detail=f"part out of range: 1..{segments_n}")

    safe_name = _sanitize_filename(file.filename or "")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".mp3", ".m4a", ".mp4", ".wav", ".aac", ".flac", ".ogg", ".opus"}:
        raise HTTPException(status_code=400, detail="仅支持常见音频文件格式")

    stem = re.sub(r"\s+", "_", Path(safe_name).stem).strip("_") or "audio"
    if len(stem) > 48:
        stem = stem[:48]
    ts = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d_%H%M%S")
    name = f"manual_part{part_n}_{ts}_{uuid.uuid4().hex[:8]}_{stem}{suffix}"
    dest = (job.outputs_dir / name).resolve()
    base = job.outputs_dir.resolve()
    if not dest.is_relative_to(base):
        raise HTTPException(status_code=400, detail="invalid upload destination")

    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    try:
        duration = get_audio_duration(dest)
        duration_seconds = int(round(duration.seconds))
    except Exception:
        duration_seconds = 0

    return {
        "ok": True,
        "jobId": job.id,
        "part": part_n,
        "file": dest.name,
        "durationSeconds": duration_seconds,
        "downloadUrl": f"/download/{job.id}/{dest.name}",
    }


@app.post("/concat/api/stitch-parts")
async def concat_stitch_parts(req: ConcatStitchPartsRequest) -> dict[str, Any]:
    job = job_manager.get(req.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    base = job.outputs_dir.resolve()
    parts: list[Path] = []
    for raw in (req.parts or []):
        name = Path(str(raw or "")).name
        if not name:
            continue
        path = (base / name).resolve()
        if not path.is_relative_to(base):
            raise HTTPException(status_code=400, detail=f"invalid filename: {name}")
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"file not found: {name}")
        parts.append(path)

    if not parts:
        raise HTTPException(status_code=400, detail="parts is empty")

    output_format = str(req.output_format or "m4a").strip().lower()
    if output_format not in {"mp3", "mp4", "m4a"}:
        raise HTTPException(status_code=400, detail="output_format must be mp3, mp4, or m4a")

    name = _sanitize_filename(req.output_name or "")
    if not name:
        name = f"main_{job.id}_{datetime.now(SHANGHAI_TZ):%Y%m%d_%H%M%S}"
    if not name.lower().endswith(f".{output_format}"):
        name += f".{output_format}"
    output_path = concat_service.output_dir / name

    transition_enabled = bool(req.transition_enabled)
    transition_files = list(req.transition_files or [])
    transition_repeats = list(req.transition_repeats or [])
    transition_durations = list(req.transition_durations or [])
    fade_seconds = float(req.transition_fade_seconds or 0.0)

    transitions: list[Path | None] = []
    if transition_enabled:
        gaps = max(0, len(parts) - 1)
        for i in range(gaps):
            raw = str(transition_files[i] if i < len(transition_files) else "").strip()
            if not raw:
                transitions.append(None)
                continue
            p = Path(raw)
            if not p.is_absolute():
                p = (paths.base_dir / raw).resolve()
            if not p.exists():
                transitions.append(None)
            else:
                transitions.append(p)

    loop = asyncio.get_running_loop()

    def _run() -> None:
        if transition_enabled and any(isinstance(p, Path) for p in transitions):
            concat_audio_with_transitions(
                parts,
                transitions,
                output_path,
                output_format=output_format,
                fade_seconds=fade_seconds,
                transition_repeats=transition_repeats,
                transition_durations=transition_durations,
            )
        else:
            concat_audio(parts, output_path, output_format=output_format)

    await loop.run_in_executor(None, _run)
    duration = get_audio_duration(output_path)

    return {
        "ok": True,
        "outputFile": output_path.name,
        "outputPath": str(output_path),
        "downloadUrl": f"/concat/download/{output_path.name}",
        "durationSeconds": int(round(duration.seconds)),
    }
