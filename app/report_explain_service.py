from __future__ import annotations

import json
import shutil
import subprocess
import threading
import uuid
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import AppPaths
from .utils.report_pdf import markdown_to_pdf

SHANGHAI_TZ = timezone(timedelta(hours=8))
REPORT_EXPLAIN_MODEL = "gpt-5.4"
REPORT_EXPLAIN_REASONING_EFFORT = "xhigh"
SOURCE_PREVIEW_LIMIT = 12000
MARKDOWN_PREVIEW_LIMIT = 3000
LOG_TAIL_LIMIT = 6000
EVENT_DETAIL_LIMIT = 4000
OUTPUT_BASENAME_LIMIT = 96

STATUS_LABELS = {
    "queued": "任务创建",
    "running": "Codex 写作",
    "rendering": "PDF 排版",
    "succeeded": "结果就绪",
    "failed": "执行失败",
}

STATUS_PROGRESS = {
    "queued": 12,
    "running": 58,
    "rendering": 88,
    "succeeded": 100,
    "failed": 100,
}


def _now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat()


def _sanitize_filename(name: str) -> str:
    safe = Path(name or "").name.strip()
    for ch in '\\/:*?"<>|':
        safe = safe.replace(ch, "_")
    return safe or "report_explain"


def _shorten_basename(name: str, limit: int = OUTPUT_BASENAME_LIMIT) -> str:
    clean = Path(_sanitize_filename(name)).stem.strip() or "report_explain"
    if len(clean) <= limit:
        return clean
    digest = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:8]
    keep = max(24, limit - len(digest) - 1)
    return f"{clean[:keep]}-{digest}"


def _read_text(path: Path, *, max_chars: int | None = None, strip: bool = False) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if strip:
        text = text.strip()
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars]
    return text


def _tail_text(path: Path, max_chars: int = LOG_TAIL_LIMIT) -> str:
    text = _read_text(path)
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _preview_text(path: Path, max_chars: int = MARKDOWN_PREVIEW_LIMIT) -> str:
    return _read_text(path, max_chars=max_chars, strip=True)


def _looks_like_final_markdown(text: str) -> bool:
    return text.strip().startswith("#")


def _parse_iso_or_default(value: str | None, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _duration_seconds(created_at: str, completed_at: str | None) -> int | None:
    start = _parse_datetime(created_at)
    end = _parse_datetime(completed_at)
    if not start or not end:
        return None
    seconds = int((end - start).total_seconds())
    return max(seconds, 0)


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status or "等待中")


def _status_progress(status: str) -> int:
    return STATUS_PROGRESS.get(status, 0)


@dataclass
class ReportExplainJob:
    id: str
    created_at: str
    updated_at: str
    status: str
    message: str
    source_filename: str
    output_basename: str
    prompt_chars: int
    source_chars: int
    job_dir: Path
    source_file_path: Path
    source_text_path: Path
    prompt_path: Path
    meta_path: Path
    event_log_path: Path
    codex_markdown_path: Path
    output_markdown_path: Path
    output_pdf_path: Path
    log_path: Path
    export_pdf: bool = True
    rerun_from_job_id: str | None = None
    error: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        markdown_ready = self.output_markdown_path.exists()
        pdf_ready = self.export_pdf and self.output_pdf_path.exists()
        rerun_ready = self.source_file_path.exists() and self.source_text_path.exists() and self.prompt_path.exists()
        return {
            "ok": True,
            "jobId": self.id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "durationSeconds": _duration_seconds(self.created_at, self.completed_at),
            "status": self.status,
            "stageLabel": _status_label(self.status),
            "progressPercent": _status_progress(self.status),
            "message": self.message,
            "error": self.error,
            "exitCode": self.exit_code,
            "sourceFilename": self.source_filename,
            "outputBaseName": self.output_basename,
            "exportPdf": self.export_pdf,
            "outputMode": "markdown+pdf" if self.export_pdf else "markdown",
            "rerunReady": rerun_ready,
            "rerunFromJobId": self.rerun_from_job_id,
            "promptChars": self.prompt_chars,
            "sourceChars": self.source_chars,
            "logTail": _tail_text(self.log_path),
            "markdownPreview": _preview_text(self.output_markdown_path),
            "markdownReady": markdown_ready,
            "pdfReady": pdf_ready,
            "markdownFile": self.output_markdown_path.name if markdown_ready else None,
            "pdfFile": self.output_pdf_path.name if pdf_ready else None,
            "detailUrl": f"/report-explain/api/jobs/{self.id}/detail",
            "logsUrl": f"/report-explain/api/jobs/{self.id}/logs",
            "resultPageUrl": f"/report-explain/result/{self.id}",
            "previewPdfUrl": f"/report-explain/preview/{self.output_pdf_path.name}" if pdf_ready else None,
            "downloadMarkdownUrl": (
                f"/report-explain/download/{self.output_markdown_path.name}" if markdown_ready else None
            ),
            "downloadPdfUrl": f"/report-explain/download/{self.output_pdf_path.name}" if pdf_ready else None,
        }

    def to_summary_dict(self) -> dict[str, Any]:
        rerun_ready = self.source_file_path.exists() and self.source_text_path.exists() and self.prompt_path.exists()
        return {
            "jobId": self.id,
            "status": self.status,
            "stageLabel": _status_label(self.status),
            "progressPercent": _status_progress(self.status),
            "message": self.message,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "completedAt": self.completed_at,
            "durationSeconds": _duration_seconds(self.created_at, self.completed_at),
            "sourceFilename": self.source_filename,
            "outputBaseName": self.output_basename,
            "exportPdf": self.export_pdf,
            "outputMode": "markdown+pdf" if self.export_pdf else "markdown",
            "rerunReady": rerun_ready,
            "rerunFromJobId": self.rerun_from_job_id,
            "markdownReady": self.output_markdown_path.exists(),
            "pdfReady": self.export_pdf and self.output_pdf_path.exists(),
            "resultPageUrl": f"/report-explain/result/{self.id}",
        }


class ReportExplainService:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.jobs_dir = paths.data_dir / "report_explain_jobs"
        self.output_dir = paths.data_dir / "report_explain_output"
        self.codex_executable = shutil.which("codex") or shutil.which("codex.cmd") or "codex"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, ReportExplainJob] = {}
        self._lock = threading.Lock()

    def get_job(self, job_id: str) -> ReportExplainJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            return job
        loaded = self._load_job_from_disk(job_id)
        if not loaded:
            return None
        with self._lock:
            self._jobs.setdefault(job_id, loaded)
            return self._jobs[job_id]

    def list_jobs(self, limit: int = 20) -> list[ReportExplainJob]:
        safe_limit = max(1, min(int(limit or 20), 100))
        jobs: list[ReportExplainJob] = []
        for path in self.jobs_dir.iterdir():
            if not path.is_dir():
                continue
            if not (path / "job_meta.json").exists():
                continue
            job = self.get_job(path.name)
            if job:
                jobs.append(job)
        jobs.sort(key=lambda item: _parse_datetime(item.updated_at) or datetime.min.replace(tzinfo=SHANGHAI_TZ), reverse=True)
        return jobs[:safe_limit]

    def get_job_detail(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        source_text = _read_text(job.source_text_path)
        prompt_text = _read_text(job.prompt_path)
        markdown_text = _read_text(job.output_markdown_path, strip=True)
        data = job.to_public_dict()
        data.update(
            {
                "sourcePreview": source_text[:SOURCE_PREVIEW_LIMIT],
                "sourcePreviewTruncated": len(source_text) > SOURCE_PREVIEW_LIMIT,
                "promptText": prompt_text,
                "promptReady": bool(prompt_text),
                "markdownText": markdown_text,
                "markdownChars": len(markdown_text),
                "events": self._read_events(job, limit=12),
            }
        )
        return data

    def get_job_logs(self, job_id: str, *, max_chars: int = 24000, event_limit: int = 60) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if not job:
            return None
        log_text = _tail_text(job.log_path, max_chars=max_chars)
        return {
            "ok": True,
            "jobId": job.id,
            "updatedAt": job.updated_at,
            "status": job.status,
            "stageLabel": _status_label(job.status),
            "logText": log_text,
            "logSize": job.log_path.stat().st_size if job.log_path.exists() else 0,
            "events": self._read_events(job, limit=event_limit),
        }

    def _persist_job_meta(self, job: ReportExplainJob) -> None:
        job.meta_path.write_text(
            json.dumps(
                {
                    "job_id": job.id,
                    "source_filename": job.source_filename,
                    "source_chars": job.source_chars,
                    "prompt_chars": job.prompt_chars,
                    "model": REPORT_EXPLAIN_MODEL,
                    "reasoning_effort": REPORT_EXPLAIN_REASONING_EFFORT,
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "completed_at": job.completed_at,
                    "status": job.status,
                    "message": job.message,
                    "error": job.error,
                    "exit_code": job.exit_code,
                    "output_basename": job.output_basename,
                    "export_pdf": job.export_pdf,
                    "rerun_from_job_id": job.rerun_from_job_id,
                    "output_markdown": str(job.output_markdown_path),
                    "output_pdf": str(job.output_pdf_path),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _load_job_from_disk(self, job_id: str) -> ReportExplainJob | None:
        job_dir = self.jobs_dir / job_id
        meta_path = job_dir / "job_meta.json"
        if not job_dir.exists() or not meta_path.exists():
            return None

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        source_filename = str(meta.get("source_filename") or "report.pdf")
        source_file_path = job_dir / _sanitize_filename(source_filename)
        if not source_file_path.exists():
            source_candidates = sorted(job_dir.glob("*.pdf"))
            if source_candidates:
                source_file_path = source_candidates[0]
                source_filename = source_file_path.name

        source_text_path = job_dir / "report_source.txt"
        prompt_path = job_dir / "user_prompt.txt"
        event_log_path = job_dir / "job_events.jsonl"
        codex_markdown_path = job_dir / "codex_result.md"
        log_path = job_dir / "codex_exec.log"
        output_markdown_path = Path(str(meta.get("output_markdown") or (self.output_dir / f"{job_id}.md"))).resolve()
        output_pdf_path = Path(str(meta.get("output_pdf") or (self.output_dir / f"{job_id}.pdf"))).resolve()

        created_at = _parse_iso_or_default(
            meta.get("created_at"),
            datetime.fromtimestamp(meta_path.stat().st_mtime, tz=SHANGHAI_TZ).isoformat(),
        )
        updated_at = _parse_iso_or_default(meta.get("updated_at"), created_at)
        completed_at = str(meta.get("completed_at") or "").strip() or None
        status = str(meta.get("status") or "").strip()
        error = str(meta.get("error") or "").strip() or None
        output_basename = str(meta.get("output_basename") or output_markdown_path.stem).strip() or output_markdown_path.stem
        export_pdf = _to_bool(meta.get("export_pdf"), True)
        rerun_from_job_id = str(meta.get("rerun_from_job_id") or "").strip() or None

        exit_code_raw = meta.get("exit_code")
        try:
            exit_code = int(exit_code_raw) if exit_code_raw is not None else None
        except Exception:
            exit_code = None

        if not status:
            if output_pdf_path.exists():
                status = "succeeded"
            elif error or "[error]" in _tail_text(log_path, 12000):
                status = "failed"
            elif codex_markdown_path.exists():
                status = "rendering"
            elif log_path.exists():
                status = "running"
            else:
                status = "queued"

        if not completed_at and status in {"succeeded", "failed"}:
            finished_target = output_pdf_path if output_pdf_path.exists() else meta_path
            completed_at = datetime.fromtimestamp(finished_target.stat().st_mtime, tz=SHANGHAI_TZ).isoformat()

        message = str(meta.get("message") or "").strip()
        if not message:
                message = {
                    "queued": "任务已创建，等待启动 Codex。",
                    "running": "正在调用 Codex Exec（GPT-5.4 xhigh）生成解说稿。",
                    "rendering": "Markdown 已生成，正在排版 PDF。",
                    "succeeded": "报告解说 PDF 已生成。",
                    "failed": "任务执行失败。",
            }.get(status, "任务处理中。")

        return ReportExplainJob(
            id=job_id,
            created_at=created_at,
            updated_at=updated_at,
            status=status,
            message=message,
            source_filename=source_filename,
            output_basename=output_basename,
            prompt_chars=int(meta.get("prompt_chars") or 0),
            source_chars=int(meta.get("source_chars") or 0),
            job_dir=job_dir,
            source_file_path=source_file_path,
            source_text_path=source_text_path,
            prompt_path=prompt_path,
            meta_path=meta_path,
            event_log_path=event_log_path,
            codex_markdown_path=codex_markdown_path,
            output_markdown_path=output_markdown_path,
            output_pdf_path=output_pdf_path,
            log_path=log_path,
            export_pdf=export_pdf,
            rerun_from_job_id=rerun_from_job_id,
            error=error,
            completed_at=completed_at,
            exit_code=exit_code,
        )

    def create_job(
        self,
        *,
        source_filename: str,
        source_file_bytes: bytes,
        source_text: str,
        prompt_text: str,
        output_name: str,
        export_pdf: bool = True,
        rerun_from_job_id: str | None = None,
    ) -> ReportExplainJob:
        job_id = uuid.uuid4().hex
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        source_name = _sanitize_filename(source_filename)
        source_file_path = job_dir / source_name
        source_text_path = job_dir / "report_source.txt"
        prompt_path = job_dir / "user_prompt.txt"
        meta_path = job_dir / "job_meta.json"
        event_log_path = job_dir / "job_events.jsonl"
        codex_markdown_path = job_dir / "codex_result.md"
        log_path = job_dir / "codex_exec.log"

        source_file_path.write_bytes(source_file_bytes)
        source_text_path.write_text(source_text, encoding="utf-8")
        prompt_path.write_text(prompt_text, encoding="utf-8")
        log_path.write_text("", encoding="utf-8")
        event_log_path.write_text("", encoding="utf-8")

        base_name = _sanitize_filename(output_name.strip()) if output_name.strip() else ""
        if not base_name:
            base_name = f"{Path(source_name).stem}（报告解说）"
        base_name = _shorten_basename(Path(base_name).stem)
        unique_base_name = _shorten_basename(f"{base_name}-{job_id[:8]}")
        output_markdown_path = self.output_dir / f"{unique_base_name}.md"
        output_pdf_path = self.output_dir / f"{unique_base_name}.pdf"

        now = _now_iso()
        job = ReportExplainJob(
            id=job_id,
            created_at=now,
            updated_at=now,
            status="queued",
            message="任务已创建，等待启动 Codex。",
            source_filename=source_filename,
            output_basename=unique_base_name,
            prompt_chars=len(prompt_text),
            source_chars=len(source_text),
            job_dir=job_dir,
            source_file_path=source_file_path,
            source_text_path=source_text_path,
            prompt_path=prompt_path,
            meta_path=meta_path,
            event_log_path=event_log_path,
            codex_markdown_path=codex_markdown_path,
            output_markdown_path=output_markdown_path,
            output_pdf_path=output_pdf_path,
            log_path=log_path,
            export_pdf=bool(export_pdf),
            rerun_from_job_id=str(rerun_from_job_id or "").strip() or None,
        )
        with self._lock:
            self._jobs[job.id] = job
        self._persist_job_meta(job)
        self._append_event(
            job,
            level="info",
            title="任务已创建",
            status="queued",
            detail=f"源文件：{source_filename}\n目标输出：{unique_base_name}",
        )

        thread = threading.Thread(target=self._run_job, args=(job.id,), daemon=True)
        thread.start()
        return job

    def rerun_job(
        self,
        source_job_id: str,
        *,
        prompt_text: str | None = None,
        output_name: str = "",
        export_pdf: bool | None = None,
    ) -> ReportExplainJob:
        source_job = self.get_job(source_job_id)
        if not source_job:
            raise KeyError(source_job_id)
        if not source_job.source_file_path.exists():
            raise FileNotFoundError("cached source PDF not found")

        source_file_bytes = source_job.source_file_path.read_bytes()
        source_text = _read_text(source_job.source_text_path)
        if not source_text.strip():
            raise FileNotFoundError("cached source text not found")

        resolved_prompt = str(prompt_text or "").strip()
        if not resolved_prompt:
            resolved_prompt = _read_text(source_job.prompt_path).strip()
        if not resolved_prompt:
            raise FileNotFoundError("cached prompt text not found")

        resolved_output_name = str(output_name or "").strip() or source_job.output_basename
        new_job = self.create_job(
            source_filename=source_job.source_filename,
            source_file_bytes=source_file_bytes,
            source_text=source_text,
            prompt_text=resolved_prompt,
            output_name=resolved_output_name,
            export_pdf=source_job.export_pdf if export_pdf is None else export_pdf,
            rerun_from_job_id=source_job_id,
        )
        self._append_event(
            new_job,
            level="info",
            title="基于缓存任务重新执行",
            status="queued",
            detail=f"来源任务 ID: {source_job_id}",
        )
        return new_job

    def _unique_output_path(self, base_name: str, suffix: str) -> Path:
        candidate = self.output_dir / f"{base_name}{suffix}"
        if not candidate.exists():
            return candidate
        for index in range(2, 1000):
            test = self.output_dir / f"{base_name}_{index}{suffix}"
            if not test.exists():
                return test
        raise RuntimeError("无法分配唯一输出文件名。")

    def _append_event(
        self,
        job: ReportExplainJob,
        *,
        level: str,
        title: str,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        row = {
            "timestamp": _now_iso(),
            "level": level,
            "status": status or job.status,
            "title": title,
            "detail": (detail or "").strip()[:EVENT_DETAIL_LIMIT],
        }
        with job.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    def _read_events(self, job: ReportExplainJob, *, limit: int = 40) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 40), 200))
        if not job.event_log_path.exists():
            return self._fallback_events(job)[-safe_limit:]

        rows: list[dict[str, Any]] = []
        try:
            for raw in job.event_log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                text = raw.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except Exception:
                    continue
                rows.append(
                    {
                        "timestamp": str(item.get("timestamp") or ""),
                        "level": str(item.get("level") or "info"),
                        "status": str(item.get("status") or ""),
                        "title": str(item.get("title") or ""),
                        "detail": str(item.get("detail") or ""),
                    }
                )
        except Exception:
            return self._fallback_events(job)[-safe_limit:]
        if not rows:
            return self._fallback_events(job)[-safe_limit:]
        return rows[-safe_limit:]

    def _fallback_events(self, job: ReportExplainJob) -> list[dict[str, Any]]:
        events = [
            {
                "timestamp": job.created_at,
                "level": "info",
                "status": "queued",
                "title": "任务已创建",
                "detail": f"源文件：{job.source_filename}",
            }
        ]
        if job.status != "queued":
            level = "success" if job.status == "succeeded" else "error" if job.status == "failed" else "info"
            events.append(
                {
                    "timestamp": job.completed_at or job.updated_at,
                    "level": level,
                    "status": job.status,
                    "title": job.message,
                    "detail": job.error or "",
                }
            )
        return events

    def _set_job_state(
        self,
        job_id: str,
        *,
        status: str | None = None,
        message: str | None = None,
        error: str | None = None,
        completed: bool = False,
        exit_code: int | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            previous = (job.status, job.message, job.error, job.completed_at, job.exit_code)
            if status is not None:
                job.status = status
            if message is not None:
                job.message = message
            if error is not None:
                job.error = error
            if exit_code is not None:
                job.exit_code = exit_code
            job.updated_at = _now_iso()
            if completed:
                job.completed_at = _now_iso()
            self._persist_job_meta(job)
            current = (job.status, job.message, job.error, job.completed_at, job.exit_code)

        if current == previous:
            return

        level = "error" if job.status == "failed" else "success" if job.status == "succeeded" else "info"
        detail = error if error is not None else job.error
        self._append_event(
            job,
            level=level,
            title=job.message or _status_label(job.status),
            status=job.status,
            detail=detail or "",
        )

    def _append_log(
        self,
        job: ReportExplainJob,
        text: str,
        *,
        event_level: str | None = None,
        event_title: str | None = None,
    ) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        with job.log_path.open("a", encoding="utf-8") as handle:
            if job.log_path.exists() and job.log_path.stat().st_size > 0:
                handle.write("\n")
            handle.write(cleaned)
            handle.write("\n")
        if event_level or event_title:
            self._append_event(
                job,
                level=event_level or "info",
                title=event_title or "日志更新",
                status=job.status,
                detail=cleaned,
            )

    def _build_codex_prompt(self, job: ReportExplainJob) -> str:
        return (
            "你正在执行一项本地“商业报告解说”生成任务。\n"
            "工作目录中已有以下文件：\n"
            "- report_source.txt：从用户上传 PDF 提取出的正文\n"
            "- user_prompt.txt：用户要求你严格遵守的写作提示词\n"
            "- job_meta.json：任务元信息\n\n"
            "要求：\n"
            "1. 必须先读取 report_source.txt 与 user_prompt.txt。\n"
            "2. 输出内容必须是最终成稿 Markdown，不要解释你的过程。\n"
            "3. 不要输出代码块，不要输出“下面是结果”这类前后缀。\n"
            "4. 如果原报告未披露某项信息，可明确写“报告未披露”，但不要编造。\n"
            "5. 不要创建、修改、删除工作目录中的任何文件；系统会自动保存你最后一条回复。\n"
            "6. 你的最终回复必须以 '# ' 开头，并且直接可用于后续排版为 PDF。\n\n"
            "现在开始。"
        )

    def _select_markdown_result(self, job: ReportExplainJob) -> str:
        text = _read_text(job.codex_markdown_path, strip=True)
        if _looks_like_final_markdown(text):
            return text

        candidates = sorted(
            [
                path
                for path in job.job_dir.glob("*.md")
                if path.resolve() != job.codex_markdown_path.resolve()
            ],
            key=lambda path: path.stat().st_size,
            reverse=True,
        )
        for candidate in candidates:
            candidate_text = _read_text(candidate, strip=True)
            if _looks_like_final_markdown(candidate_text):
                self._append_log(
                    job,
                    f"[fallback]\n使用工作目录中的 Markdown 文件：{candidate.name}",
                    event_level="warning",
                    event_title="使用回退 Markdown 结果",
                )
                return candidate_text

        return text

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return

        try:
            self._set_job_state(
                job_id,
                status="running",
                message="正在调用 Codex Exec（GPT-5.4 xhigh）生成解说稿。",
            )

            cmd = [
                self.codex_executable,
                "exec",
                "--full-auto",
                "--skip-git-repo-check",
                "-c",
                f'model_reasoning_effort="{REPORT_EXPLAIN_REASONING_EFFORT}"',
                "-m",
                REPORT_EXPLAIN_MODEL,
                "-C",
                str(job.job_dir),
                "-o",
                str(job.codex_markdown_path),
                "-",
            ]
            prompt = self._build_codex_prompt(job)
            self._append_log(
                job,
                f"$ {' '.join(cmd[:-1])} -",
                event_level="command",
                event_title="启动 Codex Exec",
            )

            proc = subprocess.run(
                cmd,
                input=prompt,
                cwd=str(job.job_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            stdout_text = (proc.stdout or "").strip()
            stderr_text = (proc.stderr or "").strip()
            if stdout_text:
                self._append_log(
                    job,
                    f"[stdout]\n{stdout_text}",
                    event_level="info",
                    event_title="Codex 输出更新",
                )
            if stderr_text:
                self._append_log(
                    job,
                    f"[stderr]\n{stderr_text}",
                    event_level="warning",
                    event_title="Codex 标准错误输出",
                )

            self._set_job_state(job_id, exit_code=proc.returncode)
            if proc.returncode != 0:
                raise RuntimeError(stderr_text or stdout_text or f"codex exec exited with {proc.returncode}")
            if not job.codex_markdown_path.exists():
                raise RuntimeError("Codex 未产出 Markdown 结果文件。")

            markdown_text = self._select_markdown_result(job)
            if not markdown_text:
                raise RuntimeError("Codex 产出的 Markdown 为空。")
            job.output_markdown_path.write_text(markdown_text, encoding="utf-8")

            if not job.export_pdf:
                self._append_event(
                    job,
                    level="info",
                    title="Markdown 文字排版已生成",
                    status="succeeded",
                    detail=f"Markdown 文件：{job.output_markdown_path.name}",
                )
                self._set_job_state(
                    job_id,
                    status="succeeded",
                    message="文字排版已生成。",
                    completed=True,
                )
                return

            self._set_job_state(
                job_id,
                status="rendering",
                message="Markdown 已生成，正在排版 PDF。",
            )
            self._append_event(
                job,
                level="info",
                title="开始排版 PDF",
                status="rendering",
                detail=f"Markdown 文件：{job.output_markdown_path.name}",
            )

            markdown_to_pdf(
                markdown_text,
                job.output_pdf_path,
                title=job.output_basename,
                source_filename=job.source_filename,
            )

            self._set_job_state(
                job_id,
                status="succeeded",
                message="报告解说 PDF 已生成。",
                completed=True,
            )
        except Exception as exc:
            self._append_log(
                job,
                f"[error]\n{type(exc).__name__}: {exc}",
                event_level="error",
                event_title="任务失败",
            )
            self._set_job_state(
                job_id,
                status="failed",
                message="任务执行失败。",
                error=str(exc),
                completed=True,
            )
