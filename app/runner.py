from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from notebooklm import AudioFormat, AudioLength, NotebookLMClient, RPCError
from notebooklm.types import GenerationStatus

from .accounts_store import AccountsStore
from .utils.audio_concat import concat_audio
from .utils.audio_duration import get_audio_duration
from .utils.notebooklm_download import _extract_audio_download_url, download_audio_with_storage
from .utils.report_split import split_report

if TYPE_CHECKING:
    from .jobs import Job


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_filename(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\\s+", " ", value)
    return value[:120] if value else "output"


def _parse_audio_length(value: str) -> AudioLength:
    match value.strip().lower():
        case "short":
            return AudioLength.SHORT
        case "default":
            return AudioLength.DEFAULT
        case "long":
            return AudioLength.LONG
        case _:
            raise ValueError("audio_length must be one of: short, default, long")


def _parse_audio_format(value: str) -> AudioFormat:
    match value.strip().lower():
        case "deep_dive" | "deep-dive":
            return AudioFormat.DEEP_DIVE
        case "brief":
            return AudioFormat.BRIEF
        case "critique":
            return AudioFormat.CRITIQUE
        case "debate":
            return AudioFormat.DEBATE
        case _:
            raise ValueError("audio_format must be one of: deep_dive, brief, critique, debate")


@dataclass(frozen=True)
class AttemptResult:
    ok: bool
    duration_seconds: float | None
    file_name: str | None
    error: str | None


def _rpc_error_details(err: Exception) -> dict[str, Any]:
    if not isinstance(err, RPCError):
        return {"error": str(err), "error_type": type(err).__name__}

    out: dict[str, Any] = {"error": str(err), "error_type": type(err).__name__}
    if getattr(err, "rpc_id", None):
        out["rpc_id"] = err.rpc_id
    if getattr(err, "code", None) is not None:
        out["rpc_code"] = err.code
    found = getattr(err, "found_ids", None)
    if isinstance(found, list) and found:
        out["found_ids"] = found[:30]
    return out


def _merge_instructions(base: str | None, extra: str) -> str:
    base = (base or "").strip()
    extra = (extra or "").strip()
    if base and extra:
        return f"{base}\n\n{extra}"
    return base or extra


def _build_part_instructions(
    base: str | None,
    part_index: int,
    part_total: int,
    item_start: int | None,
    item_end: int | None,
    target_minutes: float,
) -> str:
    if item_start is not None and item_end is not None:
        scope = f"新闻 [{item_start:02d}]–[{item_end:02d}]"
    else:
        scope = f"分段 {part_index}/{part_total}"

    extra = (
        "你正在为同一份晨间新闻稿件生成“分段播客”，请严格只使用本段提供的内容。\n"
        f"本段是第 {part_index}/{part_total} 段（{scope}）。\n"
        f"目标：尽量接近 {target_minutes:.0f}–{target_minutes + 8:.0f} 分钟，至少 {target_minutes:.0f} 分钟。\n"
        "开头请清晰提示“这是第几段/共几段”，并概览本段将覆盖的主题。\n"
        "结尾做一个本段小结，并用 1 句话预告下一段（最后一段则做总收束）。\n"
    )
    return _merge_instructions(base, extra)


def _select_part_instructions(
    cfg: "JobConfig",
    base: str | None,
    part_index: int,
    part_total: int,
    item_start: int | None,
    item_end: int | None,
    target_minutes: float,
) -> str:
    custom_list = cfg.split_part_instructions or []
    if len(custom_list) >= part_index:
        candidate = str(custom_list[part_index - 1] or "").strip()
        if candidate:
            return candidate
    return _build_part_instructions(
        base=base,
        part_index=part_index,
        part_total=part_total,
        item_start=item_start,
        item_end=item_end,
        target_minutes=target_minutes,
    )


async def _wait_for_completion_resilient(
    client: NotebookLMClient,
    notebook_id: str,
    task_id: str,
    timeout: float = 3600.0,
    initial_interval: float = 2.0,
    max_interval: float = 12.0,
) -> GenerationStatus:
    """Wait for a generation task to complete, tolerating transient poll failures.

    NotebookLM 的 POLL_STUDIO 有时会出现请求异常；这里用“失败不立刻判死”的策略，
    继续等待并用 LIST_ARTIFACTS 兜底判断是否已经完成。
    """
    start_time = asyncio.get_running_loop().time()
    current_interval = initial_interval
    last_error: str | None = None

    while True:
        try:
            status = await client.artifacts.poll_status(notebook_id, task_id)
            if status.is_complete or status.is_failed:
                return status
        except Exception as e:
            last_error = str(e) or repr(e)
            try:
                list_raw = getattr(client.artifacts, "_list_raw", None)
                if callable(list_raw):
                    artifacts_data = await list_raw(notebook_id)
                    # If we can already extract a download URL, the audio is ready.
                    _extract_audio_download_url(artifacts_data, task_id)
                    return GenerationStatus(task_id=task_id, status="completed")
            except Exception as e2:
                last_error = f"{last_error} | list_fallback: {str(e2) or repr(e2)}"

        elapsed = asyncio.get_running_loop().time() - start_time
        if elapsed > timeout:
            suffix = f" last_error={last_error}" if last_error else ""
            raise TimeoutError(f"Task {task_id} timed out after {timeout}s.{suffix}")

        remaining = timeout - elapsed
        sleep_duration = min(current_interval, remaining)
        if sleep_duration > 0:
            await asyncio.sleep(sleep_duration)
        current_interval = min(current_interval * 2, max_interval)


def _is_poll_studio_timeout(err: BaseException | None) -> bool:
    if err is None:
        return False
    return "POLL_STUDIO" in str(err).upper()


async def run_job(job: Job, report_text: str, accounts_store: AccountsStore) -> None:
    cfg = job.config

    target = cfg.target_successes
    target_mode = str(getattr(cfg, "target_mode", "accepted") or "accepted").strip().lower()
    if target_mode not in {"accepted", "downloaded"}:
        target_mode = "accepted"
    min_seconds = cfg.min_duration_minutes * 60.0
    split_enabled = bool(getattr(cfg, "split_enabled", False))
    split_parallel = bool(getattr(cfg, "split_parallel", False))
    split_segments = int(getattr(cfg, "split_segments", 3))
    split_min_seconds = float(getattr(cfg, "split_min_duration_minutes", 15.0)) * 60.0
    split_output_format = str(getattr(cfg, "split_output_format", "m4a") or "m4a").strip().lower()
    split_keep_parts = bool(getattr(cfg, "split_keep_parts", False))
    split_task_timeout = max(1200.0, min(3600.0, split_min_seconds * 2.0))

    audio_length = _parse_audio_length(cfg.audio_length)
    audio_format = _parse_audio_format(cfg.audio_format)
    language = cfg.language.strip() or "en"
    instructions = (cfg.instructions or "").strip() or None
    per_account_concurrency = max(1, int(getattr(cfg, "per_account_concurrency", 1) or 1))
    delete_cancelled_artifacts = bool(getattr(cfg, "delete_cancelled_artifacts", True))

    semaphore = asyncio.Semaphore(cfg.accounts_concurrency)
    success_lock = asyncio.Lock()

    def progress_count_unlocked() -> int:
        return job.downloads if target_mode == "downloaded" else job.successes

    async def publish(event: dict[str, Any]) -> None:
        await job.publish(event)

    async def run_split_parallel_parts() -> None:
        plan = split_report(report_text, split_segments, include_prefix=True)
        await publish(
            {
                "type": "split_detected",
                "ts": _now_iso(),
                "method": plan.method,
                "detected_items": plan.detected_items,
                "segments": len(plan.parts),
                "min_part_minutes": cfg.split_min_duration_minutes,
                "parallel": True,
            }
        )

        parts_by_index = {p.index: p for p in plan.parts}
        if not parts_by_index:
            raise RuntimeError("split produced no parts")

        account_plans: list[tuple[Any, int]] = []
        for a in cfg.accounts:
            account = accounts_store.get(a.account_id)
            if not account:
                await publish(
                    {
                        "type": "account_error",
                        "ts": _now_iso(),
                        "account_id": a.account_id,
                        "error": "account not found",
                    }
                )
                continue
            account_plans.append((account, a.max_attempts))

        if not account_plans:
            raise RuntimeError("no valid accounts available")

        async def stitch_episode(*, episode_index: int, parts_paths: list[Path]) -> tuple[Path, float]:
            output_format = (
                split_output_format if split_output_format in {"mp3", "mp4", "m4a"} else "m4a"
            )
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            merged_tmp = job.outputs_dir / f"merged_ep{episode_index:02d}_{job.id}_{ts}.{output_format}"

            await publish(
                {
                    "type": "stitch_started",
                    "ts": _now_iso(),
                    "episode": episode_index,
                    "parts": [p.name for p in parts_paths],
                    "output": merged_tmp.name,
                    "output_format": output_format,
                }
            )

            result = concat_audio(parts_paths, merged_tmp, output_format=output_format)
            merged_duration = get_audio_duration(result.output_path)
            merged_minutes = merged_duration.minutes
            await publish(
                {
                    "type": "stitch_completed",
                    "ts": _now_iso(),
                    "episode": episode_index,
                    "file": result.output_path.name,
                    "duration_minutes": round(merged_minutes, 2),
                    "duration_method": merged_duration.method,
                    "method": result.method,
                }
            )

            if merged_duration.seconds < min_seconds:
                await publish(
                    {
                        "type": "stitch_rejected",
                        "ts": _now_iso(),
                        "episode": episode_index,
                        "file": result.output_path.name,
                        "duration_minutes": round(merged_minutes, 2),
                        "min_duration_minutes": cfg.min_duration_minutes,
                    }
                )
                try:
                    result.output_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise RuntimeError(
                    "stitched result too short; consider increasing split_min_duration_minutes "
                    "or lowering min_duration_minutes"
                )

            final_name = f"merged_ep{episode_index:02d}_{math.floor(merged_minutes):02d}min_{job.id}_{ts}.{output_format}"
            final_path = job.outputs_dir / final_name
            result.output_path.replace(final_path)
            return final_path, merged_minutes

        # Produce N stitched outputs (target_successes). Episodes run sequentially to keep logic simple.
        for episode_index in range(1, target + 1):
            if job.is_cancelled:
                return

            results_by_part: dict[int, Path] = {}
            exhausted_by_part: dict[int, set[str]] = {idx: set() for idx in parts_by_index}
            in_progress_by_part: dict[int, str | None] = {idx: None for idx in parts_by_index}
            state_lock = asyncio.Lock()

            done_event = asyncio.Event()
            failed_event = asyncio.Event()
            failure: dict[str, str] = {}

            part_queue: asyncio.Queue[int] = asyncio.Queue()
            for idx in sorted(parts_by_index):
                part_queue.put_nowait(idx)

            accounts_semaphore = asyncio.Semaphore(cfg.accounts_concurrency)

            async def account_worker(account: Any, max_attempts: int) -> None:
                if done_event.is_set() or failed_event.is_set() or job.is_cancelled:
                    return

                async with accounts_semaphore:
                    if done_event.is_set() or failed_event.is_set() or job.is_cancelled:
                        return

                    await publish(
                        {
                            "type": "account_started",
                            "ts": _now_iso(),
                            "account_id": account.id,
                            "account_name": account.name,
                            "max_attempts": max_attempts,
                            "episode": episode_index,
                        }
                    )

                    try:
                        async with await NotebookLMClient.from_storage(account.storage_path, timeout=90.0) as client:
                            nb_title = _sanitize_filename(
                                f"Morning Podcast - {job.id} - ep{episode_index:02d} - {account.name}"
                            )
                            nb = await client.notebooks.create(nb_title)
                            await publish(
                                {
                                    "type": "notebook_created",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "account_name": account.name,
                                    "notebook_id": nb.id,
                                    "title": nb_title,
                                    "episode": episode_index,
                                }
                            )

                            async def add_source_resilient(
                                *,
                                title: str,
                                content: str,
                                file_name: str,
                                part: int | None = None,
                            ):
                                try:
                                    src = await client.sources.add_text(
                                        nb.id,
                                        title=title,
                                        content=content,
                                        wait=True,
                                        wait_timeout=600.0,
                                    )
                                    return src, "text"
                                except RPCError as e:
                                    if getattr(e, "rpc_id", None) == "izAoDd" or "izAoDd" in str(e):
                                        await publish(
                                            {
                                                "type": "source_fallback_file",
                                                "ts": _now_iso(),
                                                "account_id": account.id,
                                                "account_name": account.name,
                                                "notebook_id": nb.id,
                                                "part": part,
                                                "episode": episode_index,
                                                **_rpc_error_details(e),
                                            }
                                        )
                                        path = job.outputs_dir / file_name
                                        path.write_text(content, encoding="utf-8")
                                        src = await client.sources.add_file(
                                            nb.id,
                                            file_path=path,
                                            wait=True,
                                            wait_timeout=600.0,
                                        )
                                        return src, "file"
                                    raise

                            while not job.is_cancelled and not done_event.is_set() and not failed_event.is_set():
                                try:
                                    part_index = await asyncio.wait_for(part_queue.get(), timeout=1.0)
                                except asyncio.TimeoutError:
                                    continue

                                claimed = False
                                try:
                                    async with state_lock:
                                        if part_index in results_by_part:
                                            continue
                                        if account.id in exhausted_by_part.get(part_index, set()):
                                            part_queue.put_nowait(part_index)
                                            continue
                                        if in_progress_by_part.get(part_index) is not None:
                                            part_queue.put_nowait(part_index)
                                            continue
                                        in_progress_by_part[part_index] = account.id
                                        claimed = True

                                    part = parts_by_index.get(part_index)
                                    if not part:
                                        raise RuntimeError(f"invalid part index: {part_index}")

                                    header = (
                                        f"【分段 {part.index}/{part.total}】"
                                        + (
                                            f"新闻 [{part.item_start:02d}]–[{part.item_end:02d}]（{part.item_count} 条）"
                                            if part.item_start is not None
                                            and part.item_end is not None
                                            and part.item_count is not None
                                            else ""
                                        )
                                    ).strip()
                                    content = f"{header}\n\n{part.text}".strip()

                                    src, source_method = await add_source_resilient(
                                        title=f"Morning Report Part {part.index}/{part.total}",
                                        content=content,
                                        file_name=(
                                            f"{_sanitize_filename(account.name)}_ep{episode_index:02d}_"
                                            f"part{part.index:02d}_{job.id}.txt"
                                        ),
                                        part=part.index,
                                    )
                                    await publish(
                                        {
                                            "type": "split_source_ready",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "account_name": account.name,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "episode": episode_index,
                                            "source_id": src.id,
                                            "source_method": source_method,
                                            "item_start": part.item_start,
                                            "item_end": part.item_end,
                                            "item_count": part.item_count,
                                        }
                                    )

                                    accepted_path: Path | None = None
                                    generate_lock = asyncio.Lock()
                                    task_id_by_attempt: dict[int, str] = {}
                                    attempt_by_task: dict[asyncio.Task, int] = {}
                                    switch_account = False
                                    no_task_id_count = 0

                                    async def run_part_attempt(attempt: int) -> dict[str, Any] | None:
                                        tmp_path: Path | None = None
                                        task_id: str | None = None
                                        nonlocal no_task_id_count

                                        if job.is_cancelled or done_event.is_set() or failed_event.is_set():
                                            return None

                                        await publish(
                                            {
                                                "type": "part_attempt_started",
                                                "ts": _now_iso(),
                                                "account_id": account.id,
                                                "account_name": account.name,
                                                "notebook_id": nb.id,
                                                "part": part.index,
                                                "attempt": attempt,
                                                "episode": episode_index,
                                                "parallel": per_account_concurrency > 1,
                                            }
                                        )

                                        try:
                                            part_instructions = _select_part_instructions(
                                                cfg=cfg,
                                                base=instructions,
                                                part_index=part.index,
                                                part_total=part.total,
                                                item_start=part.item_start,
                                                item_end=part.item_end,
                                                target_minutes=float(cfg.split_min_duration_minutes),
                                            )
                                            async with generate_lock:
                                                status = await client.artifacts.generate_audio(
                                                    nb.id,
                                                    source_ids=[src.id],
                                                    language=language,
                                                    instructions=part_instructions,
                                                    audio_format=audio_format,
                                                    audio_length=audio_length,
                                                )
                                            task_id = getattr(status, "task_id", None) or ""
                                            if not task_id:
                                                await publish(
                                                    {
                                                        "type": "part_generation_failed",
                                                        "ts": _now_iso(),
                                                        "account_id": account.id,
                                                        "account_name": account.name,
                                                        "notebook_id": nb.id,
                                                        "part": part.index,
                                                        "attempt": attempt,
                                                        "episode": episode_index,
                                                        "task_id": "",
                                                        "error": "generate_audio returned empty task id",
                                                        "error_code": "NO_TASK_ID",
                                                    }
                                                )
                                                return {"ok": False, "no_task_id": True, "attempt": attempt}
                                            no_task_id_count = 0
                                            task_id_by_attempt[attempt] = task_id

                                            await publish(
                                                {
                                                    "type": "part_generation_started",
                                                    "ts": _now_iso(),
                                                    "account_id": account.id,
                                                    "account_name": account.name,
                                                    "notebook_id": nb.id,
                                                    "part": part.index,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    "task_id": task_id,
                                                }
                                            )

                                            final = await _wait_for_completion_resilient(
                                                client,
                                                nb.id,
                                                task_id,
                                                timeout=split_task_timeout,
                                                initial_interval=2.0,
                                                max_interval=12.0,
                                            )

                                            if final.is_failed:
                                                await publish(
                                                    {
                                                        "type": "part_generation_failed",
                                                        "ts": _now_iso(),
                                                        "account_id": account.id,
                                                        "account_name": account.name,
                                                        "notebook_id": nb.id,
                                                        "part": part.index,
                                                        "attempt": attempt,
                                                        "episode": episode_index,
                                                        "task_id": task_id,
                                                        "error": final.error,
                                                        "error_code": final.error_code,
                                                    }
                                                )
                                                if final.is_rate_limited:
                                                    await asyncio.sleep(10.0)
                                                return None

                                            tmp_name = (
                                                f"{_sanitize_filename(account.name)}_ep{episode_index:02d}_"
                                                f"p{part.index:02d}_a{attempt:02d}_{task_id}.mp4"
                                            )
                                            tmp_path = job.outputs_dir / tmp_name
                                            await download_audio_with_storage(
                                                artifacts_api=client.artifacts,
                                                storage_state_path=Path(account.storage_path),
                                                notebook_id=nb.id,
                                                artifact_id=task_id,
                                                output_path=tmp_path,
                                            )

                                            duration = get_audio_duration(tmp_path)
                                            duration_minutes = duration.minutes
                                            await publish(
                                                {
                                                    "type": "part_downloaded",
                                                    "ts": _now_iso(),
                                                    "account_id": account.id,
                                                    "account_name": account.name,
                                                    "notebook_id": nb.id,
                                                    "part": part.index,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    "task_id": task_id,
                                                    "file": tmp_name,
                                                    "duration_seconds": duration.seconds,
                                                    "duration_minutes": round(duration_minutes, 2),
                                                    "duration_method": duration.method,
                                                }
                                            )

                                            if duration.seconds >= split_min_seconds:
                                                final_name = (
                                                    f"{_sanitize_filename(account.name)}_ep{episode_index:02d}_"
                                                    f"p{part.index:02d}_{math.floor(duration_minutes):02d}min_{task_id}.mp4"
                                                )
                                                final_path = job.outputs_dir / final_name
                                                tmp_path.replace(final_path)
                                                tmp_path = None
                                                return {
                                                    "ok": True,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    "task_id": task_id,
                                                    "file": final_name,
                                                    "duration_minutes": duration_minutes,
                                                    "path": final_path,
                                                }

                                            await publish(
                                                {
                                                    "type": "part_rejected",
                                                    "ts": _now_iso(),
                                                    "account_id": account.id,
                                                    "account_name": account.name,
                                                    "notebook_id": nb.id,
                                                    "part": part.index,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    "task_id": task_id,
                                                    "file": tmp_name,
                                                    "duration_minutes": round(duration_minutes, 2),
                                                    "min_duration_minutes": cfg.split_min_duration_minutes,
                                                }
                                            )

                                            if not cfg.keep_short_files:
                                                try:
                                                    tmp_path.unlink(missing_ok=True)
                                                except Exception:
                                                    pass
                                            if cfg.delete_short_artifacts:
                                                try:
                                                    await client.artifacts.delete(nb.id, task_id)
                                                except Exception:
                                                    pass
                                            return None

                                        except asyncio.CancelledError:
                                            if tmp_path:
                                                try:
                                                    tmp_path.unlink(missing_ok=True)
                                                except Exception:
                                                    pass
                                            raise
                                        except TimeoutError as e:
                                            poll_timeout = _is_poll_studio_timeout(e)
                                            err_msg = str(e)
                                            if poll_timeout:
                                                err_msg = f"{err_msg} (switch account)"
                                            await publish(
                                                {
                                                    "type": "part_attempt_error",
                                                    "ts": _now_iso(),
                                                    "account_id": account.id,
                                                    "account_name": account.name,
                                                    "notebook_id": nb.id,
                                                    "part": part.index,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    "task_id": task_id,
                                                    "error": err_msg,
                                                    "error_type": "TimeoutError",
                                                }
                                            )
                                            if task_id and delete_cancelled_artifacts:
                                                try:
                                                    await client.artifacts.delete(nb.id, task_id)
                                                except Exception:
                                                    pass
                                            await asyncio.sleep(2.0)
                                            if poll_timeout:
                                                return {
                                                    "ok": False,
                                                    "fatal_for_account": True,
                                                    "reason": "POLL_STUDIO_TIMEOUT",
                                                    "attempt": attempt,
                                                    "task_id": task_id,
                                                }
                                            return None
                                        except RPCError as e:
                                            await publish(
                                                {
                                                    "type": "part_attempt_error",
                                                    "ts": _now_iso(),
                                                    "account_id": account.id,
                                                    "account_name": account.name,
                                                    "notebook_id": nb.id,
                                                    "part": part.index,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    **_rpc_error_details(e),
                                                }
                                            )
                                            await asyncio.sleep(5.0)
                                            return None
                                        except Exception as e:
                                            await publish(
                                                {
                                                    "type": "part_attempt_error",
                                                    "ts": _now_iso(),
                                                    "account_id": account.id,
                                                    "account_name": account.name,
                                                    "notebook_id": nb.id,
                                                    "part": part.index,
                                                    "attempt": attempt,
                                                    "episode": episode_index,
                                                    "error": str(e),
                                                    "error_type": type(e).__name__,
                                                }
                                            )
                                            await asyncio.sleep(2.0)
                                            return None

                                    next_attempt = 1
                                    active: set[asyncio.Task[dict[str, Any] | None]] = set()

                                    try:
                                        while accepted_path is None and not (
                                            job.is_cancelled or done_event.is_set() or failed_event.is_set()
                                        ) and not switch_account:
                                            while (
                                                next_attempt <= max_attempts
                                                and len(active) < per_account_concurrency
                                                and not switch_account
                                            ):
                                                attempt_no = next_attempt
                                                next_attempt += 1
                                                t = asyncio.create_task(run_part_attempt(attempt_no))
                                                attempt_by_task[t] = attempt_no
                                                active.add(t)

                                            if not active:
                                                break

                                            done, _ = await asyncio.wait(
                                                active, return_when=asyncio.FIRST_COMPLETED
                                            )
                                            for t in done:
                                                active.remove(t)
                                                try:
                                                    outcome = t.result()
                                                except asyncio.CancelledError:
                                                    continue
                                                except Exception:
                                                    continue

                                                if not outcome:
                                                    continue
                                                if outcome.get("no_task_id"):
                                                    no_task_id_count += 1
                                                    if no_task_id_count >= 3:
                                                        switch_account = True
                                                        await publish(
                                                            {
                                                                "type": "part_attempt_error",
                                                                "ts": _now_iso(),
                                                                "account_id": account.id,
                                                                "account_name": account.name,
                                                                "notebook_id": nb.id,
                                                                "part": part.index,
                                                                "attempt": int(outcome.get("attempt") or 0),
                                                                "episode": episode_index,
                                                                "error": "NO_TASK_ID reached 3 times; switching account",
                                                                "error_type": "NoTaskIdSwitch",
                                                            }
                                                        )
                                                    continue
                                                if outcome.get("fatal_for_account"):
                                                    switch_account = True
                                                    break
                                                if not outcome.get("ok"):
                                                    continue

                                                accepted_path = outcome.get("path")
                                                duration_minutes = float(outcome["duration_minutes"])
                                                await publish(
                                                    {
                                                        "type": "part_accepted",
                                                        "ts": _now_iso(),
                                                        "account_id": account.id,
                                                        "account_name": account.name,
                                                        "notebook_id": nb.id,
                                                        "part": part.index,
                                                        "attempt": int(outcome["attempt"]),
                                                        "episode": episode_index,
                                                        "task_id": str(outcome["task_id"]),
                                                        "file": str(outcome["file"]),
                                                        "duration_minutes": round(duration_minutes, 2),
                                                        "min_duration_minutes": cfg.split_min_duration_minutes,
                                                    }
                                                )

                                                accepted_task_id = str(outcome.get("task_id") or "")
                                                extra_task_ids = []
                                                if delete_cancelled_artifacts:
                                                    for p in active:
                                                        a_no = attempt_by_task.get(p)
                                                        tid = (
                                                            task_id_by_attempt.get(int(a_no))
                                                            if a_no is not None
                                                            else None
                                                        )
                                                        if tid and tid != accepted_task_id:
                                                            extra_task_ids.append(tid)

                                                for p in active:
                                                    p.cancel()
                                                await asyncio.gather(*active, return_exceptions=True)
                                                active.clear()
                                                if delete_cancelled_artifacts and extra_task_ids:
                                                    deletes = [
                                                        asyncio.create_task(client.artifacts.delete(nb.id, tid))
                                                        for tid in set(extra_task_ids)
                                                    ]
                                                    await asyncio.gather(*deletes, return_exceptions=True)
                                                break
                                            if switch_account:
                                                break
                                    finally:
                                        extra_task_ids = []
                                        if delete_cancelled_artifacts:
                                            for p in active:
                                                a_no = attempt_by_task.get(p)
                                                tid = (
                                                    task_id_by_attempt.get(int(a_no))
                                                    if a_no is not None
                                                    else None
                                                )
                                                if tid:
                                                    extra_task_ids.append(tid)
                                        for p in active:
                                            p.cancel()
                                        await asyncio.gather(*active, return_exceptions=True)
                                        if delete_cancelled_artifacts and extra_task_ids:
                                            deletes = [
                                                asyncio.create_task(client.artifacts.delete(nb.id, tid))
                                                for tid in set(extra_task_ids)
                                            ]
                                            await asyncio.gather(*deletes, return_exceptions=True)

                                    async with state_lock:
                                        in_progress_by_part[part_index] = None
                                        if accepted_path is not None:
                                            results_by_part[part_index] = accepted_path
                                            if len(results_by_part) >= len(parts_by_index):
                                                done_event.set()
                                        else:
                                            exhausted_by_part[part_index].add(account.id)
                                            remaining = [
                                                a.id
                                                for a, _ in account_plans
                                                if a.id not in exhausted_by_part[part_index]
                                            ]
                                            if remaining:
                                                part_queue.put_nowait(part_index)
                                            else:
                                                failure["error"] = (
                                                    f"part {part_index} failed to reach duration threshold"
                                                )
                                                failed_event.set()

                                except Exception as e:
                                    async with state_lock:
                                        if claimed and in_progress_by_part.get(part_index) == account.id:
                                            in_progress_by_part[part_index] = None
                                            exhausted_by_part[part_index].add(account.id)
                                            remaining = [
                                                a.id
                                                for a, _ in account_plans
                                                if a.id not in exhausted_by_part[part_index]
                                            ]
                                            if remaining:
                                                part_queue.put_nowait(part_index)
                                            else:
                                                failure["error"] = (
                                                    f"part {part_index} failed: {str(e) or type(e).__name__}"
                                                )
                                                failed_event.set()
                                finally:
                                    part_queue.task_done()

                    except Exception as e:
                        await publish(
                            {
                                "type": "account_error",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "account_name": account.name,
                                "episode": episode_index,
                                **_rpc_error_details(e),
                            }
                        )
                        async with state_lock:
                            for idx in parts_by_index:
                                if idx in results_by_part:
                                    continue
                                exhausted_by_part[idx].add(account.id)
                                if in_progress_by_part.get(idx) == account.id:
                                    in_progress_by_part[idx] = None
                                    part_queue.put_nowait(idx)
                            for idx in parts_by_index:
                                if idx in results_by_part:
                                    continue
                                remaining = [
                                    a.id for a, _ in account_plans if a.id not in exhausted_by_part[idx]
                                ]
                                if not remaining:
                                    failure["error"] = "no accounts left to generate remaining parts"
                                    failed_event.set()
                                    break
                    finally:
                        await publish(
                            {
                                "type": "account_finished",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "account_name": account.name,
                                "episode": episode_index,
                            }
                        )

            tasks = [asyncio.create_task(account_worker(acc, ma)) for acc, ma in account_plans]

            try:
                while not done_event.is_set() and not failed_event.is_set() and not job.is_cancelled:
                    await asyncio.sleep(0.25)
            finally:
                try:
                    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=15.0)
                except asyncio.TimeoutError:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

            if job.is_cancelled:
                return
            if failed_event.is_set():
                err = failure.get("error") or "split parallel failed"
                await publish({"type": "split_failed", "ts": _now_iso(), "episode": episode_index, "error": err})
                raise RuntimeError(err)

            parts_paths = [results_by_part[i] for i in sorted(parts_by_index)]
            stitched_path, stitched_minutes = await stitch_episode(
                episode_index=episode_index, parts_paths=parts_paths
            )

            if not split_keep_parts:
                for p in parts_paths:
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass

            job.downloads += 1
            job.successes += 1
            await publish(
                {
                    "type": "accepted",
                    "ts": _now_iso(),
                    "attempt": 1,
                    "task_id": "merged",
                    "file": stitched_path.name,
                    "duration_minutes": round(stitched_minutes, 2),
                    "successes": job.successes,
                    "downloads": job.downloads,
                    "target": target,
                    "mode": "split_parallel",
                    "episode": episode_index,
                }
            )

    if split_enabled and split_parallel:
        await run_split_parallel_parts()
        return

    async def account_worker(account_id: str, max_attempts: int) -> None:
        async with semaphore:
            account = accounts_store.get(account_id)
            if not account:
                await publish(
                    {
                        "type": "account_error",
                        "ts": _now_iso(),
                        "account_id": account_id,
                        "error": "account not found",
                    }
                )
                return

            await publish(
                {
                    "type": "account_started",
                    "ts": _now_iso(),
                    "account_id": account.id,
                    "account_name": account.name,
                    "max_attempts": max_attempts,
                }
            )

            try:
                async with await NotebookLMClient.from_storage(account.storage_path, timeout=90.0) as client:
                    nb_title = _sanitize_filename(f"Morning Podcast • {job.id} • {account.name}")
                    nb = await client.notebooks.create(nb_title)
                    await publish(
                        {
                            "type": "notebook_created",
                            "ts": _now_iso(),
                            "account_id": account.id,
                            "notebook_id": nb.id,
                            "title": nb_title,
                        }
                    )

                    async def add_source_resilient(
                        *,
                        title: str,
                        content: str,
                        file_name: str,
                        part: int | None = None,
                    ):
                        try:
                            src = await client.sources.add_text(
                                nb.id,
                                title=title,
                                content=content,
                                wait=True,
                                wait_timeout=600.0,
                            )
                            return src, "text"
                        except RPCError as e:
                            # ADD_SOURCE (izAoDd) may return null result for large payloads or transient API issues.
                            # Fallback to file upload, which is typically more robust.
                            if getattr(e, "rpc_id", None) == "izAoDd" or "izAoDd" in str(e):
                                await publish(
                                    {
                                        "type": "source_fallback_file",
                                        "ts": _now_iso(),
                                        "account_id": account.id,
                                        "notebook_id": nb.id,
                                        "part": part,
                                        **_rpc_error_details(e),
                                    }
                                )
                                path = job.outputs_dir / file_name
                                path.write_text(content, encoding="utf-8")
                                src = await client.sources.add_file(
                                    nb.id,
                                    file_path=path,
                                    wait=True,
                                    wait_timeout=600.0,
                                )
                                return src, "file"
                            raise

                    if split_enabled:
                        plan = split_report(report_text, split_segments, include_prefix=True)
                        await publish(
                            {
                                "type": "split_detected",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "notebook_id": nb.id,
                                "method": plan.method,
                                "detected_items": plan.detected_items,
                                "segments": len(plan.parts),
                                "min_part_minutes": cfg.split_min_duration_minutes,
                            }
                        )

                        sources = []
                        for part in plan.parts:
                            header = (
                                f"【分段 {part.index}/{part.total}】"
                                + (
                                    f"新闻 [{part.item_start:02d}]–[{part.item_end:02d}]（{part.item_count} 条）"
                                    if part.item_start is not None
                                    and part.item_end is not None
                                    and part.item_count is not None
                                    else ""
                                )
                            ).strip()
                            content = f"{header}\n\n{part.text}".strip()
                            src, source_method = await add_source_resilient(
                                title=f"Morning Report Part {part.index}/{part.total}",
                                content=content,
                                file_name=f"{_sanitize_filename(account.name)}_part{part.index:02d}_{job.id}.txt",
                                part=part.index,
                            )
                            sources.append((part, src))
                            await publish(
                                {
                                    "type": "split_source_ready",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "notebook_id": nb.id,
                                    "part": part.index,
                                    "source_id": src.id,
                                    "source_method": source_method,
                                    "item_start": part.item_start,
                                    "item_end": part.item_end,
                                    "item_count": part.item_count,
                                }
                            )

                        parts_paths: list[Path] = []
                        for part, src in sources:
                            if job.is_cancelled:
                                return
                            async with success_lock:
                                if job.successes >= target:
                                    return

                            accepted_path: Path | None = None
                            generate_lock = asyncio.Lock()
                            task_id_by_attempt: dict[int, str] = {}
                            attempt_by_task: dict[asyncio.Task, int] = {}

                            async def run_part_attempt(attempt: int) -> dict[str, Any] | None:
                                tmp_path: Path | None = None
                                task_id: str | None = None

                                if job.is_cancelled:
                                    return None
                                async with success_lock:
                                    if job.successes >= target:
                                        return None

                                await publish(
                                    {
                                        "type": "part_attempt_started",
                                        "ts": _now_iso(),
                                        "account_id": account.id,
                                        "notebook_id": nb.id,
                                        "part": part.index,
                                        "attempt": attempt,
                                        "parallel": per_account_concurrency > 1,
                                    }
                                )

                                try:
                                    part_instructions = _select_part_instructions(
                                        cfg=cfg,
                                        base=instructions,
                                        part_index=part.index,
                                        part_total=part.total,
                                        item_start=part.item_start,
                                        item_end=part.item_end,
                                        target_minutes=float(cfg.split_min_duration_minutes),
                                    )
                                    async with generate_lock:
                                        status = await client.artifacts.generate_audio(
                                            nb.id,
                                            source_ids=[src.id],
                                            language=language,
                                            instructions=part_instructions,
                                            audio_format=audio_format,
                                            audio_length=audio_length,
                                        )
                                    task_id = getattr(status, "task_id", None) or ""
                                    if not task_id:
                                        await publish(
                                            {
                                                "type": "part_generation_failed",
                                                "ts": _now_iso(),
                                                "account_id": account.id,
                                                "notebook_id": nb.id,
                                                "part": part.index,
                                                "attempt": attempt,
                                                "task_id": "",
                                                "error": "generate_audio returned empty task id",
                                                "error_code": "NO_TASK_ID",
                                            }
                                        )
                                        return None
                                    task_id_by_attempt[attempt] = task_id

                                    await publish(
                                        {
                                            "type": "part_generation_started",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "attempt": attempt,
                                            "task_id": task_id,
                                        }
                                    )

                                    final = await _wait_for_completion_resilient(
                                        client,
                                        nb.id,
                                        task_id,
                                        timeout=split_task_timeout,
                                        initial_interval=2.0,
                                        max_interval=12.0,
                                    )

                                    if final.is_failed:
                                        await publish(
                                            {
                                                "type": "part_generation_failed",
                                                "ts": _now_iso(),
                                                "account_id": account.id,
                                                "notebook_id": nb.id,
                                                "part": part.index,
                                                "attempt": attempt,
                                                "task_id": task_id,
                                                "error": final.error,
                                                "error_code": final.error_code,
                                            }
                                        )
                                        if final.is_rate_limited:
                                            await asyncio.sleep(10.0)
                                        return None

                                    tmp_name = (
                                        f"{_sanitize_filename(account.name)}_p{part.index:02d}_"
                                        f"a{attempt:02d}_{task_id}.mp4"
                                    )
                                    tmp_path = job.outputs_dir / tmp_name
                                    await download_audio_with_storage(
                                        artifacts_api=client.artifacts,
                                        storage_state_path=Path(account.storage_path),
                                        notebook_id=nb.id,
                                        artifact_id=task_id,
                                        output_path=tmp_path,
                                    )

                                    duration = get_audio_duration(tmp_path)
                                    duration_minutes = duration.minutes
                                    await publish(
                                        {
                                            "type": "part_downloaded",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "attempt": attempt,
                                            "task_id": task_id,
                                            "file": tmp_name,
                                            "duration_seconds": duration.seconds,
                                            "duration_minutes": round(duration_minutes, 2),
                                            "duration_method": duration.method,
                                        }
                                    )

                                    if duration.seconds >= split_min_seconds:
                                        final_name = (
                                            f"{_sanitize_filename(account.name)}_p{part.index:02d}_"
                                            f"{math.floor(duration_minutes):02d}min_{task_id}.mp4"
                                        )
                                        final_path = job.outputs_dir / final_name
                                        tmp_path.replace(final_path)
                                        tmp_path = None
                                        return {
                                            "ok": True,
                                            "attempt": attempt,
                                            "task_id": task_id,
                                            "file": final_name,
                                            "duration_minutes": duration_minutes,
                                            "path": final_path,
                                        }

                                    await publish(
                                        {
                                            "type": "part_rejected",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "attempt": attempt,
                                            "task_id": task_id,
                                            "file": tmp_name,
                                            "duration_minutes": round(duration_minutes, 2),
                                            "min_duration_minutes": cfg.split_min_duration_minutes,
                                        }
                                    )

                                    if not cfg.keep_short_files:
                                        try:
                                            tmp_path.unlink(missing_ok=True)
                                        except Exception:
                                            pass
                                    if cfg.delete_short_artifacts:
                                        try:
                                            await client.artifacts.delete(nb.id, task_id)
                                        except Exception:
                                            pass
                                    return None

                                except asyncio.CancelledError:
                                    if tmp_path:
                                        try:
                                            tmp_path.unlink(missing_ok=True)
                                        except Exception:
                                            pass
                                    raise
                                except TimeoutError as e:
                                    await publish(
                                        {
                                            "type": "part_attempt_error",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "attempt": attempt,
                                            "task_id": task_id,
                                            "error": str(e),
                                            "error_type": "TimeoutError",
                                        }
                                    )
                                    if task_id and delete_cancelled_artifacts:
                                        try:
                                            await client.artifacts.delete(nb.id, task_id)
                                        except Exception:
                                            pass
                                    return None
                                except RPCError as e:
                                    await publish(
                                        {
                                            "type": "part_attempt_error",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "attempt": attempt,
                                            **_rpc_error_details(e),
                                        }
                                    )
                                    await asyncio.sleep(5.0)
                                    return None
                                except Exception as e:
                                    await publish(
                                        {
                                            "type": "part_attempt_error",
                                            "ts": _now_iso(),
                                            "account_id": account.id,
                                            "notebook_id": nb.id,
                                            "part": part.index,
                                            "attempt": attempt,
                                            "error": str(e),
                                            "error_type": type(e).__name__,
                                        }
                                    )
                                    await asyncio.sleep(2.0)
                                    return None

                            next_attempt = 1
                            active: set[asyncio.Task[dict[str, Any] | None]] = set()

                            try:
                                while True:
                                    if job.is_cancelled:
                                        return
                                    async with success_lock:
                                        if job.successes >= target:
                                            return

                                    while (
                                        next_attempt <= max_attempts
                                        and len(active) < per_account_concurrency
                                        and accepted_path is None
                                    ):
                                        attempt_no = next_attempt
                                        next_attempt += 1
                                        t = asyncio.create_task(run_part_attempt(attempt_no))
                                        attempt_by_task[t] = attempt_no
                                        active.add(t)

                                    if not active or accepted_path is not None:
                                        break

                                    done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                                    for t in done:
                                        active.remove(t)
                                        try:
                                            outcome = t.result()
                                        except asyncio.CancelledError:
                                            continue
                                        except Exception:
                                            continue

                                        if not outcome or not outcome.get("ok"):
                                            continue

                                        accepted_path = outcome.get("path")
                                        duration_minutes = float(outcome["duration_minutes"])
                                        await publish(
                                            {
                                                "type": "part_accepted",
                                                "ts": _now_iso(),
                                                "account_id": account.id,
                                                "notebook_id": nb.id,
                                                "part": part.index,
                                                "attempt": int(outcome["attempt"]),
                                                "task_id": str(outcome["task_id"]),
                                                "file": str(outcome["file"]),
                                                "duration_minutes": round(duration_minutes, 2),
                                                "min_duration_minutes": cfg.split_min_duration_minutes,
                                            }
                                        )

                                        accepted_task_id = str(outcome.get("task_id") or "")
                                        extra_task_ids = []
                                        if delete_cancelled_artifacts:
                                            for p in active:
                                                a_no = attempt_by_task.get(p)
                                                tid = task_id_by_attempt.get(int(a_no)) if a_no is not None else None
                                                if tid and tid != accepted_task_id:
                                                    extra_task_ids.append(tid)

                                        for p in active:
                                            p.cancel()
                                        await asyncio.gather(*active, return_exceptions=True)
                                        active.clear()
                                        if delete_cancelled_artifacts and extra_task_ids:
                                            deletes = [
                                                asyncio.create_task(client.artifacts.delete(nb.id, tid))
                                                for tid in set(extra_task_ids)
                                            ]
                                            await asyncio.gather(*deletes, return_exceptions=True)
                                        break
                            finally:
                                extra_task_ids = []
                                if delete_cancelled_artifacts:
                                    for p in active:
                                        a_no = attempt_by_task.get(p)
                                        tid = task_id_by_attempt.get(int(a_no)) if a_no is not None else None
                                        if tid:
                                            extra_task_ids.append(tid)
                                for p in active:
                                    p.cancel()
                                await asyncio.gather(*active, return_exceptions=True)
                                if delete_cancelled_artifacts and extra_task_ids:
                                    deletes = [
                                        asyncio.create_task(client.artifacts.delete(nb.id, tid))
                                        for tid in set(extra_task_ids)
                                    ]
                                    await asyncio.gather(*deletes, return_exceptions=True)

                            if accepted_path is None:
                                await publish(
                                    {
                                        "type": "split_failed",
                                        "ts": _now_iso(),
                                        "account_id": account.id,
                                        "notebook_id": nb.id,
                                        "error": f"part {part.index} failed to reach duration threshold",
                                    }
                                )
                                return

                            parts_paths.append(accepted_path)

                        output_format = (
                            split_output_format
                            if split_output_format in {"mp3", "mp4", "m4a"}
                            else "m4a"
                        )

                        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        merged_tmp = job.outputs_dir / (
                            f"{_sanitize_filename(account.name)}_merged_{job.id}_{ts}.{output_format}"
                        )
                        await publish(
                            {
                                "type": "stitch_started",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "notebook_id": nb.id,
                                "parts": [p.name for p in parts_paths],
                                "output": merged_tmp.name,
                                "output_format": output_format,
                            }
                        )

                        result = concat_audio(parts_paths, merged_tmp, output_format=output_format)
                        merged_duration = get_audio_duration(result.output_path)
                        merged_minutes = merged_duration.minutes
                        await publish(
                            {
                                "type": "stitch_completed",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "notebook_id": nb.id,
                                "file": result.output_path.name,
                                "duration_minutes": round(merged_minutes, 2),
                                "duration_method": merged_duration.method,
                                "method": result.method,
                            }
                        )

                        if merged_duration.seconds < min_seconds:
                            await publish(
                                {
                                    "type": "stitch_rejected",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "notebook_id": nb.id,
                                    "file": result.output_path.name,
                                    "duration_minutes": round(merged_minutes, 2),
                                    "min_duration_minutes": cfg.min_duration_minutes,
                                }
                            )
                            try:
                                result.output_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return

                        final_name = (
                            f"{_sanitize_filename(account.name)}_merged_{math.floor(merged_minutes):02d}min_"
                            f"{job.id}_{ts}.{output_format}"
                        )
                        final_path = job.outputs_dir / final_name
                        result.output_path.replace(final_path)

                        if not split_keep_parts:
                            for p in parts_paths:
                                try:
                                    p.unlink(missing_ok=True)
                                except Exception:
                                    pass

                        async with success_lock:
                            job.downloads += 1
                            job.successes += 1
                            current = job.successes
                            downloads_current = job.downloads

                        await publish(
                            {
                                "type": "accepted",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "notebook_id": nb.id,
                                "attempt": 1,
                                "task_id": "merged",
                                "file": final_name,
                                "duration_minutes": round(merged_minutes, 2),
                                "successes": current,
                                "downloads": downloads_current,
                                "target": target,
                                "mode": "split",
                            }
                        )
                        return

                    # Add report as a source
                    source, source_method = await add_source_resilient(
                        title="Morning Report",
                        content=report_text,
                        file_name=f"{_sanitize_filename(account.name)}_report_{job.id}.txt",
                        part=None,
                    )
                    await publish(
                        {
                            "type": "source_ready",
                            "ts": _now_iso(),
                            "account_id": account.id,
                            "notebook_id": nb.id,
                            "source_id": source.id,
                            "source_method": source_method,
                        }
                    )

                    generate_lock = asyncio.Lock()
                    task_id_by_attempt: dict[int, str] = {}
                    attempt_by_task: dict[asyncio.Task, int] = {}

                    async def run_attempt(attempt: int) -> dict[str, Any] | None:
                        tmp_path: Path | None = None
                        task_id: str | None = None

                        if job.is_cancelled:
                            return None
                        async with success_lock:
                            if progress_count_unlocked() >= target:
                                return None

                        await publish(
                            {
                                "type": "attempt_started",
                                "ts": _now_iso(),
                                "account_id": account.id,
                                "notebook_id": nb.id,
                                "attempt": attempt,
                                "parallel": per_account_concurrency > 1,
                            }
                        )

                        try:
                            async with generate_lock:
                                status = await client.artifacts.generate_audio(
                                    nb.id,
                                    language=language,
                                    instructions=instructions,
                                    audio_format=audio_format,
                                    audio_length=audio_length,
                                )
                            task_id = getattr(status, "task_id", None) or ""
                            if not task_id:
                                await publish(
                                    {
                                        "type": "generation_failed",
                                        "ts": _now_iso(),
                                        "account_id": account.id,
                                        "notebook_id": nb.id,
                                        "attempt": attempt,
                                        "task_id": "",
                                        "error": "generate_audio returned empty task id",
                                        "error_code": "NO_TASK_ID",
                                    }
                                )
                                return None
                            task_id_by_attempt[attempt] = task_id

                            await publish(
                                {
                                    "type": "generation_started",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "notebook_id": nb.id,
                                    "attempt": attempt,
                                    "task_id": task_id,
                                }
                            )

                            final = await _wait_for_completion_resilient(
                                client,
                                nb.id,
                                task_id,
                                timeout=3600.0,
                                initial_interval=2.0,
                                max_interval=12.0,
                            )

                            if final.is_failed:
                                await publish(
                                    {
                                        "type": "generation_failed",
                                        "ts": _now_iso(),
                                        "account_id": account.id,
                                        "notebook_id": nb.id,
                                        "attempt": attempt,
                                        "task_id": task_id,
                                        "error": final.error,
                                        "error_code": final.error_code,
                                    }
                                )
                                if final.is_rate_limited:
                                    await asyncio.sleep(10.0)
                                return None

                            tmp_name = f"{_sanitize_filename(account.name)}_{attempt:02d}_{task_id}.mp4"
                            tmp_path = job.outputs_dir / tmp_name
                            await download_audio_with_storage(
                                artifacts_api=client.artifacts,
                                storage_state_path=Path(account.storage_path),
                                notebook_id=nb.id,
                                artifact_id=task_id,
                                output_path=tmp_path,
                            )

                            duration = get_audio_duration(tmp_path)
                            duration_minutes = duration.minutes
                            accepted = duration.seconds >= min_seconds
                            keep_file = accepted or cfg.keep_short_files or target_mode == "downloaded"

                            file_name = tmp_name
                            if keep_file:
                                mm = max(0, math.floor(duration_minutes))
                                if accepted:
                                    file_name = (
                                        f"{_sanitize_filename(account.name)}_{attempt:02d}_{mm:02d}min_{task_id}.mp4"
                                    )
                                else:
                                    file_name = (
                                        f"{_sanitize_filename(account.name)}_{attempt:02d}_{mm:02d}min_short_{task_id}.mp4"
                                    )
                                final_path = job.outputs_dir / file_name
                                tmp_path.replace(final_path)
                                tmp_path = None

                            counted = False
                            progress_current: int | None = None
                            successes_current: int | None = None
                            downloads_current: int | None = None
                            if keep_file:
                                async with success_lock:
                                    before = progress_count_unlocked()
                                    job.downloads += 1
                                    if accepted:
                                        job.successes += 1
                                    downloads_current = job.downloads
                                    successes_current = job.successes
                                    progress_current = progress_count_unlocked()
                                    counted = progress_current > before

                            await publish(
                                {
                                    "type": "downloaded",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "account_name": account.name,
                                    "notebook_id": nb.id,
                                    "attempt": attempt,
                                    "task_id": task_id,
                                    "file": file_name,
                                    "kept": keep_file,
                                    "accepted": accepted,
                                    "successes": successes_current,
                                    "downloads": downloads_current,
                                    "progress": progress_current,
                                    "target": target,
                                    "target_mode": target_mode,
                                    "counted": counted,
                                    "duration_seconds": duration.seconds,
                                    "duration_minutes": round(duration_minutes, 2),
                                    "duration_method": duration.method,
                                }
                            )

                            if accepted:
                                await publish(
                                    {
                                        "type": "accepted",
                                        "ts": _now_iso(),
                                        "account_id": account.id,
                                        "account_name": account.name,
                                        "notebook_id": nb.id,
                                        "attempt": attempt,
                                        "task_id": task_id,
                                        "file": file_name,
                                        "duration_minutes": round(duration_minutes, 2),
                                        "successes": successes_current,
                                        "downloads": downloads_current,
                                        "progress": progress_current,
                                        "target": target,
                                        "target_mode": target_mode,
                                        "counted": counted,
                                    }
                                )
                                return {
                                    "ok": True,
                                    "attempt": attempt,
                                    "task_id": task_id,
                                    "file": file_name,
                                    "duration_minutes": duration_minutes,
                                    "accepted": True,
                                }

                            await publish(
                                {
                                    "type": "rejected",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "account_name": account.name,
                                    "notebook_id": nb.id,
                                    "attempt": attempt,
                                    "task_id": task_id,
                                    "file": file_name,
                                    "duration_minutes": round(duration_minutes, 2),
                                    "min_duration_minutes": cfg.min_duration_minutes,
                                    "successes": successes_current,
                                    "downloads": downloads_current,
                                    "progress": progress_current,
                                    "target": target,
                                    "target_mode": target_mode,
                                    "counted": counted,
                                }
                            )
                            if not keep_file:
                                if tmp_path:
                                    try:
                                        tmp_path.unlink(missing_ok=True)
                                    except Exception:
                                        pass
                            if cfg.delete_short_artifacts:
                                try:
                                    await client.artifacts.delete(nb.id, task_id)
                                except Exception:
                                    pass
                            if keep_file:
                                return {
                                    "ok": True,
                                    "attempt": attempt,
                                    "task_id": task_id,
                                    "file": file_name,
                                    "duration_minutes": duration_minutes,
                                    "accepted": False,
                                }
                            return None

                        except asyncio.CancelledError:
                            if tmp_path:
                                try:
                                    tmp_path.unlink(missing_ok=True)
                                except Exception:
                                    pass
                            raise
                        except TimeoutError as e:
                            await publish(
                                {
                                    "type": "attempt_error",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "notebook_id": nb.id,
                                    "attempt": attempt,
                                    "task_id": task_id,
                                    "error": str(e),
                                    "error_type": "TimeoutError",
                                }
                            )
                            if task_id and delete_cancelled_artifacts:
                                try:
                                    await client.artifacts.delete(nb.id, task_id)
                                except Exception:
                                    pass
                            return None
                        except RPCError as e:
                            await publish(
                                {
                                    "type": "attempt_error",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "notebook_id": nb.id,
                                    "attempt": attempt,
                                    **_rpc_error_details(e),
                                }
                            )
                            await asyncio.sleep(5.0)
                            return None
                        except Exception as e:
                            await publish(
                                {
                                    "type": "attempt_error",
                                    "ts": _now_iso(),
                                    "account_id": account.id,
                                    "notebook_id": nb.id,
                                    "attempt": attempt,
                                    "error": str(e),
                                    "error_type": type(e).__name__,
                                }
                            )
                            await asyncio.sleep(2.0)
                            return None

                    next_attempt = 1
                    active: set[asyncio.Task[dict[str, Any] | None]] = set()

                    try:
                        while True:
                            if job.is_cancelled:
                                return
                            async with success_lock:
                                if progress_count_unlocked() >= target:
                                    return

                            while next_attempt <= max_attempts and len(active) < per_account_concurrency:
                                attempt_no = next_attempt
                                next_attempt += 1
                                t = asyncio.create_task(run_attempt(attempt_no))
                                attempt_by_task[t] = attempt_no
                                active.add(t)

                            if not active:
                                return

                            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                            for t in done:
                                active.remove(t)
                                try:
                                    t.result()
                                except asyncio.CancelledError:
                                    continue
                                except Exception:
                                    continue
                            reached = False
                            async with success_lock:
                                reached = progress_count_unlocked() >= target
                            if reached:
                                extra_task_ids = []
                                if delete_cancelled_artifacts:
                                    for p in active:
                                        a_no = attempt_by_task.get(p)
                                        tid = task_id_by_attempt.get(int(a_no)) if a_no is not None else None
                                        if tid:
                                            extra_task_ids.append(tid)
                                for p in active:
                                    p.cancel()
                                await asyncio.gather(*active, return_exceptions=True)
                                if delete_cancelled_artifacts and extra_task_ids:
                                    deletes = [
                                        asyncio.create_task(client.artifacts.delete(nb.id, tid))
                                        for tid in set(extra_task_ids)
                                    ]
                                    await asyncio.gather(*deletes, return_exceptions=True)
                                return
                    finally:
                        extra_task_ids = []
                        if delete_cancelled_artifacts:
                            for p in active:
                                a_no = attempt_by_task.get(p)
                                tid = task_id_by_attempt.get(int(a_no)) if a_no is not None else None
                                if tid:
                                    extra_task_ids.append(tid)
                        for p in active:
                            p.cancel()
                        await asyncio.gather(*active, return_exceptions=True)
                        if delete_cancelled_artifacts and extra_task_ids:
                            deletes = [
                                asyncio.create_task(client.artifacts.delete(nb.id, tid))
                                for tid in set(extra_task_ids)
                            ]
                            await asyncio.gather(*deletes, return_exceptions=True)

            except Exception as e:
                await publish(
                    {
                        "type": "account_error",
                        "ts": _now_iso(),
                        "account_id": account_id,
                        **_rpc_error_details(e),
                    }
                )
            finally:
                await publish(
                    {
                        "type": "account_finished",
                        "ts": _now_iso(),
                        "account_id": account_id,
                    }
                )

    tasks = [
        asyncio.create_task(account_worker(a.account_id, a.max_attempts)) for a in cfg.accounts
    ]

    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    # If we didn't hit the target, mark job as failed (so UI doesn't show a false “completed”).
    if not job.is_cancelled and progress_count_unlocked() < target:
        raise RuntimeError(f"target not reached: {progress_count_unlocked()}/{target}")
