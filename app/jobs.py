from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .accounts_store import AccountsStore
from .config import AppPaths
from .runner import run_job


class AccountPlan(BaseModel):
    account_id: str
    max_attempts: int = Field(ge=1, le=200, default=20)


class JobConfig(BaseModel):
    accounts: list[AccountPlan] = Field(min_length=1)
    target_successes: int = Field(ge=1, le=20, default=1)
    target_mode: str = Field(default="accepted")  # accepted | downloaded

    min_duration_minutes: float = Field(ge=1, le=240, default=40.0)
    split_enabled: bool = False
    split_parallel: bool = True
    split_segments: int = Field(ge=2, le=10, default=3)
    split_min_duration_minutes: float = Field(ge=1, le=120, default=15.0)
    split_task_timeout_minutes: float = Field(ge=5, le=240, default=40.0)
    split_output_format: str = Field(default="m4a")  # mp3 | mp4 | m4a
    split_keep_parts: bool = True
    split_manual_stitch: bool = False
    split_candidates_per_part: list[int] = Field(default_factory=list)
    split_part_instructions: list[str] = Field(default_factory=list)
    stitch_transition_enabled: bool = False
    stitch_transition_fade_seconds: float = Field(ge=0, le=10, default=3.0)
    stitch_transition_files: list[str] = Field(default_factory=list)
    stitch_transition_repeats: list[int] = Field(default_factory=list)
    stitch_transition_durations: list[float] = Field(default_factory=list)

    language: str = Field(default="zh")
    audio_length: str = Field(default="long")  # short|default|long
    audio_format: str = Field(default="deep_dive")  # deep_dive|brief|critique|debate
    instructions: str = Field(default="")

    per_account_concurrency: int = Field(ge=1, le=6, default=2)
    accounts_concurrency: int = Field(ge=1, le=20, default=4)

    keep_short_files: bool = False
    delete_short_artifacts: bool = True
    delete_cancelled_artifacts: bool = True
    silence_check_enabled: bool = True
    silence_min_duration_s: float = Field(ge=1, le=120, default=5.0)
    silence_threshold_db: float = Field(ge=-80, le=-10, default=-50.0)

    @field_validator("target_mode")
    @classmethod
    def _validate_target_mode(cls, value: str) -> str:
        v = str(value or "").strip().lower()
        if v in {"accepted", "accept", "success", "successes"}:
            return "accepted"
        if v in {"downloaded", "download", "generated", "outputs"}:
            return "downloaded"
        raise ValueError("target_mode must be one of: accepted, downloaded")

    @field_validator("split_part_instructions")
    @classmethod
    def _validate_split_part_instructions(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("split_part_instructions must be a list")
        cleaned: list[str] = []
        for item in value[:10]:
            cleaned.append(str(item or ""))
        return cleaned

    @field_validator("stitch_transition_files")
    @classmethod
    def _validate_stitch_transition_files(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("stitch_transition_files must be a list")
        cleaned: list[str] = []
        for item in value[:20]:
            cleaned.append(str(item or "").strip())
        return cleaned

    @field_validator("stitch_transition_repeats")
    @classmethod
    def _validate_stitch_transition_repeats(cls, value: list[int] | None) -> list[int]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("stitch_transition_repeats must be a list")
        cleaned: list[int] = []
        for item in value[:20]:
            try:
                n = int(item)
            except Exception:
                n = 1
            if n < 0:
                n = 0
            if n > 5:
                n = 5
            cleaned.append(n)
        return cleaned

    @field_validator("stitch_transition_durations")
    @classmethod
    def _validate_stitch_transition_durations(cls, value: list[float] | None) -> list[float]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("stitch_transition_durations must be a list")
        cleaned: list[float] = []
        for item in value[:20]:
            try:
                n = float(item)
            except Exception:
                n = 0.0
            if n < 0:
                n = 0.0
            if n > 600:
                n = 600.0
            cleaned.append(n)
        return cleaned

    @field_validator("split_candidates_per_part")
    @classmethod
    def _validate_split_candidates_per_part(cls, value: list[int] | None) -> list[int]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("split_candidates_per_part must be a list")
        cleaned: list[int] = []
        for item in value[:10]:
            try:
                n = int(item)  # may be str
            except Exception:
                n = 1
            cleaned.append(max(0, min(n, 20)))
        return cleaned


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    config: JobConfig
    report_char_count: int
    created_at_iso: str
    state: str  # queued|running|completed|failed|cancelled
    error: str | None
    outputs_dir: Path

    _cancel_event: asyncio.Event
    _task: asyncio.Task[None] | None
    _subscribers: set[asyncio.Queue[dict[str, Any]]]
    _subscriber_lock: asyncio.Lock
    _persist_lock: asyncio.Lock
    event_log: list[dict[str, Any]]
    successes: int
    downloads: int
    _stitch_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _stitch_futures: dict[int, asyncio.Future[dict[int, str]]] = field(default_factory=dict, repr=False)
    _stitch_pending: dict[int, dict[int, str]] = field(default_factory=dict, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _stop_mode: str | None = field(default=None, repr=False)

    @property
    def job_dir(self) -> Path:
        return self.outputs_dir.parent

    @property
    def report_path(self) -> Path:
        return self.job_dir / "report.txt"

    @property
    def snapshot_path(self) -> Path:
        return self.job_dir / "job.json"

    @property
    def events_path(self) -> Path:
        return self.job_dir / "events.jsonl"

    def snapshot_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at_iso,
            "state": self.state,
            "error": self.error,
            "config": self.config.model_dump(),
            "report_char_count": self.report_char_count,
            "outputs_dir": str(self.outputs_dir),
            "report_path": str(self.report_path),
            "successes": self.successes,
            "downloads": self.downloads,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at_iso,
            "state": self.state,
            "error": self.error,
            "config": self.config.model_dump(),
            "report_char_count": self.report_char_count,
            "outputs_dir": str(self.outputs_dir),
            "successes": self.successes,
            "downloads": self.downloads,
            "files": self.list_files(),
            "stop_requested": self._stop_event.is_set(),
            "stop_mode": self._stop_mode,
        }

    def list_files(self) -> list[dict[str, Any]]:
        if not self.outputs_dir.exists():
            return []

        # Best-effort enrich file list with metadata from event log so the UI can show duration/status.
        meta_by_name: dict[str, dict[str, Any]] = {}
        result_priority = {
            "accepted": 50,
            "stitch_rejected": 49,
            "stitch_completed": 45,
            "part_accepted": 40,
            "part_rejected": 35,
            "rejected": 34,
            "downloaded": 30,
            "part_downloaded": 25,
        }
        type_to_result = {
            "accepted": "accepted",
            "downloaded": "downloaded",
            "rejected": "rejected",
            "part_downloaded": "part_downloaded",
            "part_accepted": "part_accepted",
            "part_rejected": "part_rejected",
            "stitch_completed": "stitch_completed",
            "stitch_rejected": "stitch_rejected",
        }

        for ev in self.event_log:
            if not isinstance(ev, dict):
                continue
            file = ev.get("file")
            if not isinstance(file, str) or not file:
                continue
            m = meta_by_name.setdefault(file, {})

            if isinstance(ev.get("account_name"), str) and "account_name" not in m:
                m["account_name"] = ev["account_name"]
            if isinstance(ev.get("account_id"), str) and "account_id" not in m:
                m["account_id"] = ev["account_id"]

            if ev.get("part") is not None and "part" not in m:
                try:
                    m["part"] = int(ev["part"])
                except Exception:
                    pass
            if ev.get("episode") is not None and "episode" not in m:
                try:
                    m["episode"] = int(ev["episode"])
                except Exception:
                    pass

            if ev.get("duration_minutes") is not None:
                try:
                    m["duration_minutes"] = float(ev["duration_minutes"])
                except Exception:
                    pass
            duration_method = ev.get("duration_method") or ev.get("method")
            if isinstance(duration_method, str) and duration_method:
                m["duration_method"] = duration_method

            ev_type = str(ev.get("type") or "")
            if ev_type in {"silence_ok", "part_silence_ok"}:
                m["silence"] = "ok"
                if ev.get("min_silence_duration_s") is not None:
                    m["silence_min_duration_s"] = ev.get("min_silence_duration_s")
                if ev.get("threshold_db") is not None:
                    m["silence_threshold_db"] = ev.get("threshold_db")
            if ev_type in {"silence_rejected", "part_silence_rejected"}:
                m["silence"] = "fail"
                if ev.get("min_silence_duration_s") is not None:
                    m["silence_min_duration_s"] = ev.get("min_silence_duration_s")
                if ev.get("threshold_db") is not None:
                    m["silence_threshold_db"] = ev.get("threshold_db")
                if ev.get("segments_count") is not None:
                    m["silence_segments_count"] = ev.get("segments_count")

            result = type_to_result.get(str(ev.get("type") or ""))
            if result:
                prev = m.get("result")
                if not prev or result_priority.get(result, 0) >= result_priority.get(str(prev), 0):
                    m["result"] = result

        files = []
        for p in sorted(self.outputs_dir.glob("*")):
            if p.is_file():
                info: dict[str, Any] = {"name": p.name, "size": p.stat().st_size}
                info.update(meta_by_name.get(p.name, {}))
                files.append(info)
        return files

    async def wait_for_stitch_selection(self, episode: int) -> dict[int, str]:
        ep = max(1, int(episode))
        async with self._stitch_lock:
            pending = self._stitch_pending.pop(ep, None)
            if pending is not None:
                return pending
            fut = self._stitch_futures.get(ep)
            if fut is None or fut.done():
                fut = asyncio.get_running_loop().create_future()
                self._stitch_futures[ep] = fut
        return await fut

    async def submit_stitch_selection(self, episode: int, selection: dict[int, str]) -> None:
        ep = max(1, int(episode))
        async with self._stitch_lock:
            fut = self._stitch_futures.get(ep)
            if fut is not None and not fut.done():
                fut.set_result(selection)
                return
            self._stitch_pending[ep] = selection

    async def _persist_event(self, event: dict[str, Any]) -> None:
        # Best-effort local persistence: append JSONL event log + rewrite snapshot.
        # This keeps logs & job queue across page refresh and server restart.
        try:
            self.job_dir.mkdir(parents=True, exist_ok=True)
            line = json.dumps(event, ensure_ascii=False)
            snapshot_text = json.dumps(self.snapshot_dict(), ensure_ascii=False)

            def _write() -> None:
                self.events_path.parent.mkdir(parents=True, exist_ok=True)
                with self.events_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                self.snapshot_path.write_text(snapshot_text, encoding="utf-8")

            async with self._persist_lock:
                await asyncio.to_thread(_write)
        except Exception:
            # Do not crash jobs because persistence failed.
            return

    async def publish(self, event: dict[str, Any]) -> None:
        self.event_log.append(event)
        await self._persist_event(event)
        async with self._subscriber_lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        async with self._subscriber_lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._subscriber_lock:
            self._subscribers.discard(q)

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def request_stop(self, mode: str | None = None) -> None:
        mode_norm = str(mode or "auto").strip().lower()
        if mode_norm not in {"auto", "manual"}:
            mode_norm = "auto"
        self._stop_mode = mode_norm
        self._stop_event.set()

    @property
    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    @property
    def stop_mode(self) -> str:
        return (self._stop_mode or "auto").strip().lower()


class JobManager:
    def __init__(self, paths: AppPaths, accounts_store: AccountsStore):
        self._paths = paths
        self._accounts = accounts_store
        self._jobs: dict[str, Job] = {}
        self._load_jobs_from_disk()

    def _load_jobs_from_disk(self) -> None:
        base = self._paths.jobs_dir
        if not base.exists():
            return

        for job_dir in base.iterdir():
            if not job_dir.is_dir():
                continue
            snapshot_path = job_dir / "job.json"

            # New format: has snapshot + jsonl log.
            if snapshot_path.exists():
                try:
                    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                    job_id = str(data.get("id") or job_dir.name)
                    config = JobConfig.model_validate(data.get("config") or {})
                    outputs_dir = job_dir / "outputs"
                    outputs_dir.mkdir(parents=True, exist_ok=True)

                    job = Job(
                        id=job_id,
                        config=config,
                        report_char_count=int(data.get("report_char_count") or 0),
                        created_at_iso=str(data.get("created_at") or _now_iso()),
                        state=str(data.get("state") or "completed"),
                        error=data.get("error"),
                        outputs_dir=outputs_dir,
                        _cancel_event=asyncio.Event(),
                        _task=None,
                        _subscribers=set(),
                        _subscriber_lock=asyncio.Lock(),
                        _persist_lock=asyncio.Lock(),
                        event_log=[],
                        successes=int(data.get("successes") or 0),
                        downloads=int(data.get("downloads") or 0),
                    )

                    # Load persisted event log (best-effort).
                    events_path = job_dir / "events.jsonl"
                    if events_path.exists():
                        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
                        for line in lines[-10000:]:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                            except Exception:
                                continue
                            if isinstance(ev, dict):
                                job.event_log.append(ev)

                    # We can't resume running jobs across restarts; mark them as failed.
                    if job.state in {"queued", "running", "waiting_selection"}:
                        job.state = "failed"
                        job.error = job.error or "Server restarted while job was running; cannot resume."

                    self._jobs[job_id] = job
                except Exception:
                    continue
                continue

            # Legacy format (older runs): only outputs exist, no snapshot/log.
            try:
                outputs_dir = job_dir / "outputs"
                if not outputs_dir.exists():
                    continue
                outputs_dir.mkdir(parents=True, exist_ok=True)

                job_id = job_dir.name
                created_at_iso = datetime.fromtimestamp(job_dir.stat().st_mtime, tz=timezone.utc).isoformat()
                config = JobConfig.model_validate(
                    {
                        "accounts": [{"account_id": "legacy", "max_attempts": 1}],
                        "target_successes": 1,
                        "target_mode": "accepted",
                        "min_duration_minutes": 40.0,
                    }
                )

                min_minutes = float(config.min_duration_minutes)
                duration_re = re.compile(r"_([0-9]+(?:\.[0-9]+)?)min_", re.IGNORECASE)
                downloads = 0
                successes = 0
                for p in outputs_dir.glob("*"):
                    if not p.is_file():
                        continue
                    if p.suffix.lower() not in {".mp3", ".mp4", ".m4a"}:
                        continue
                    downloads += 1
                    m = duration_re.search(p.name)
                    if m:
                        try:
                            minutes = float(m.group(1))
                        except Exception:
                            minutes = 0.0
                        if minutes >= min_minutes:
                            successes += 1

                job = Job(
                    id=job_id,
                    config=config,
                    report_char_count=0,
                    created_at_iso=created_at_iso,
                    state="completed",
                    error=None,
                    outputs_dir=outputs_dir,
                    _cancel_event=asyncio.Event(),
                    _task=None,
                    _subscribers=set(),
                    _subscriber_lock=asyncio.Lock(),
                    _persist_lock=asyncio.Lock(),
                    event_log=[],
                    successes=successes,
                    downloads=downloads,
                )
                self._jobs[job_id] = job

                # Best-effort upgrade: write snapshot so legacy jobs show up in queue after restart.
                try:
                    job.snapshot_path.write_text(
                        json.dumps(job.snapshot_dict(), ensure_ascii=False), encoding="utf-8"
                    )
                except Exception:
                    pass
                try:
                    job.events_path.touch(exist_ok=True)
                except Exception:
                    pass
            except Exception:
                continue

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at_iso)

    async def create_and_start_job(self, config: JobConfig, report_text: str) -> Job:
        job_id = secrets.token_urlsafe(10).replace("-", "").replace("_", "")
        job_dir = self._paths.jobs_dir / job_id
        outputs_dir = job_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "report.txt").write_text(report_text, encoding="utf-8")

        job = Job(
            id=job_id,
            config=config,
            report_char_count=len(report_text),
            created_at_iso=_now_iso(),
            state="queued",
            error=None,
            outputs_dir=outputs_dir,
            _cancel_event=asyncio.Event(),
            _task=None,
            _subscribers=set(),
            _subscriber_lock=asyncio.Lock(),
            _persist_lock=asyncio.Lock(),
            event_log=[],
            successes=0,
            downloads=0,
        )
        self._jobs[job_id] = job
        await job.publish({"type": "job_queued", "ts": _now_iso(), "job_id": job_id})

        async def _runner():
            job.state = "running"
            await job.publish({"type": "job_started", "ts": _now_iso(), "job_id": job_id})
            try:
                await run_job(job, report_text, self._accounts)
                if job.is_cancelled:
                    job.state = "cancelled"
                    await job.publish({"type": "job_cancelled", "ts": _now_iso(), "job_id": job_id})
                else:
                    job.state = "completed"
                    await job.publish({"type": "job_completed", "ts": _now_iso(), "job_id": job_id})
            except Exception as e:
                job.state = "failed"
                job.error = str(e)
                await job.publish(
                    {"type": "job_failed", "ts": _now_iso(), "job_id": job_id, "error": str(e)}
                )

        job._task = asyncio.create_task(_runner())
        return job
