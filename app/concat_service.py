from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import AppPaths


@dataclass
class AudioInfo:
    codec_name: str
    sample_rate: int
    channels: int
    duration_seconds: float


@dataclass
class ConcatJob:
    id: str
    job_dir: Path
    upload_paths: list[Path]
    output_file: str
    output_path: Path
    repeat: int
    quality: int
    created_at_iso: str
    stage: str = "queued"
    message: str = "等待开始"
    progress: float = 0.0
    done: bool = False
    error: str | None = None
    output_duration_seconds: int | None = None
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    _loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _emit(self, ev_type: str, data: dict[str, Any]) -> None:
        if self._loop is None:
            return

        def _put() -> None:
            try:
                self.events.put_nowait({"type": ev_type, "data": data})
            except asyncio.QueueFull:
                pass

        self._loop.call_soon_threadsafe(_put)

    def publish_stage(self, stage: str, message: str) -> None:
        self.stage = stage
        self.message = message
        self._emit("stage", {"stage": stage, "message": message})

    def publish_progress(self, pct: float, speed: str | None = None, part: int | None = None, parts: int | None = None) -> None:
        self.progress = pct
        payload: dict[str, Any] = {"stage": "transcoding_main", "pct": pct}
        if speed:
            payload["speed"] = speed
        if part is not None:
            payload["part"] = part
        if parts is not None:
            payload["parts"] = parts
        self._emit("progress", payload)

    def publish_done(self, data: dict[str, Any]) -> None:
        self.done = True
        self._emit("done", data)
        self._close_events()

    def publish_error(self, message: str) -> None:
        self.stage = "error"
        self.error = message
        self._emit("job_error", {"message": message})
        self._close_events()

    def _close_events(self) -> None:
        if self._loop is None:
            return

        def _put() -> None:
            try:
                self.events.put_nowait({"type": "__close__", "data": {}})
            except asyncio.QueueFull:
                pass

        self._loop.call_soon_threadsafe(_put)


class ConcatService:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.jobs: dict[str, ConcatJob] = {}

        self.ffmpeg = shutil.which("ffmpeg") or ""
        self.ffprobe = shutil.which("ffprobe") or ""

        self.assets_dir = paths.base_dir / "assets" / "concat_fixed"
        self.output_dir = paths.data_dir / "concat_output"
        self.cache_dir = paths.data_dir / "concat_cache"
        self.jobs_dir = paths.data_dir / "concat_jobs"
        self.latest_txt_path = self.output_dir / "latest.txt"

        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

        self.intro_path = self.assets_dir / "商业早新闻开头加关注.mp3"
        self.outro_path = self.assets_dir / "商业早新闻结尾.mp3"
        self.tail_path = self.assets_dir / "少年的模样终版.mp3"

    def get_job(self, job_id: str) -> ConcatJob | None:
        return self.jobs.get(job_id)

    def create_job(
        self,
        *,
        upload_paths: list[Path],
        repeat: int,
        quality: int,
        output_name: str,
        loop: asyncio.AbstractEventLoop,
        job_id: str | None = None,
        job_dir: Path | None = None,
    ) -> ConcatJob:
        job_id = job_id or uuid.uuid4().hex
        job_dir = job_dir or (self.jobs_dir / job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        output_file = self._normalize_output_name(output_name, upload_paths)
        output_path = self.output_dir / output_file
        if output_path.exists():
            stem = output_path.stem
            output_file = f"{stem}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.mp3"
            output_path = self.output_dir / output_file

        job = ConcatJob(
            id=job_id,
            job_dir=job_dir,
            upload_paths=upload_paths,
            output_file=output_file,
            output_path=output_path,
            repeat=repeat,
            quality=quality,
            created_at_iso=datetime.now(timezone.utc).isoformat(),
        )
        job.bind_loop(loop)
        self.jobs[job_id] = job

        loop.call_soon_threadsafe(self._start_job_thread, job)
        return job

    def _start_job_thread(self, job: ConcatJob) -> None:
        thread = asyncio.get_running_loop().run_in_executor(None, self._process_job, job)
        asyncio.ensure_future(thread)

    def _normalize_output_name(self, output_name: str, upload_paths: list[Path]) -> str:
        name = (output_name or "").strip()
        if not name:
            if len(upload_paths) > 1:
                tomorrow = datetime.now().date() + timedelta(days=1)
                name = f"刘润早间新闻-{tomorrow:%Y-%m-%d}"
            else:
                name = upload_paths[0].stem if upload_paths else "拼接输出"
        name = self._sanitize_filename(name)
        if not name:
            name = f"拼接输出_{datetime.now():%Y%m%d_%H%M%S}"
        if not name.lower().endswith(".mp3"):
            name += ".mp3"
        return name

    def _sanitize_filename(self, name: str) -> str:
        invalid = r'\\/:*?"<>|'
        out = "".join("_" if ch in invalid else ch for ch in name)
        return out.strip()

    def _ensure_tools(self) -> None:
        if not self.ffmpeg:
            raise RuntimeError("未找到 ffmpeg，请确认已安装并加入 PATH。")
        if not self.ffprobe:
            raise RuntimeError("未找到 ffprobe，请确认已安装并加入 PATH。")

    def _audio_info(self, path: Path) -> AudioInfo:
        self._ensure_tools()
        cmd = [
            self.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            raise RuntimeError(f"未找到音频流：{path}")
        stream = streams[0]
        codec = str(stream.get("codec_name") or "")
        sample_rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
        duration = 0.0
        fmt = data.get("format") or {}
        dur = fmt.get("duration")
        if dur is not None:
            try:
                duration = float(dur)
            except Exception:
                duration = 0.0
        return AudioInfo(codec, sample_rate, channels, duration)

    def _run_ffmpeg(self, args: list[str]) -> None:
        self._ensure_tools()
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"] + args
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip() or "ffmpeg failed")

    def _ensure_mp3(
        self,
        source: Path,
        dest: Path,
        sample_rate: int,
        channels: int,
        quality: int,
        on_progress: Callable[[float, str | None], None] | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        if dest.exists():
            if dest.stat().st_mtime >= source.stat().st_mtime:
                return

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp.mp3")

        args = [
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "libmp3lame",
            "-q:a",
            str(quality),
            "-progress",
            "pipe:1",
            "-nostats",
            str(tmp),
        ]

        self._ensure_tools()
        proc = subprocess.Popen(
            [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        last_pct = -1.0
        speed = None
        duration = duration_seconds or 0.0

        if proc.stdout:
            for line in proc.stdout:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key == "speed":
                    speed = value
                    continue
                if duration <= 0.01:
                    continue
                pct = None
                if key == "out_time_ms":
                    try:
                        pct = (int(value) / 1_000_000.0) / duration
                    except Exception:
                        pct = None
                elif key == "out_time_us":
                    try:
                        pct = (int(value) / 1_000_000.0) / duration
                    except Exception:
                        pct = None
                elif key == "progress" and value == "end":
                    pct = 1.0

                if pct is None:
                    continue
                pct = max(0.0, min(pct, 1.0))
                if on_progress and (pct - last_pct >= 0.002 or pct == 1.0):
                    last_pct = pct
                    on_progress(pct, speed)

        stderr = proc.stderr.read() if proc.stderr else ""
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(stderr.strip() or "ffmpeg failed")

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dest)
        if on_progress:
            on_progress(1.0, speed)

    def _escape_for_concat(self, path: Path) -> str:
        full = str(path.resolve()).replace("\\", "/")
        return full.replace("'", "\\'")

    def _process_job(self, job: ConcatJob) -> None:
        sw_start = time.perf_counter()
        try:
            self._ensure_tools()
            for p in (self.intro_path, self.outro_path, self.tail_path):
                if not p.exists():
                    raise RuntimeError(f"缺少固定音频资源：{p}")

            job.publish_stage("analyzing", "分析主音频…")
            main_infos = [self._audio_info(p) for p in job.upload_paths]
            if not main_infos:
                raise RuntimeError("未找到主音频。")

            main0 = main_infos[0]
            if main0.sample_rate <= 0 or main0.channels <= 0:
                raise RuntimeError("无法识别主音频参数（sample_rate/channels）。")

            sample_rate = main0.sample_rate
            channels = main0.channels

            job.publish_stage("preparing_fixed", "准备固定片头/片尾…")
            cache_key = f"fixed_{sample_rate}hz_{channels}ch_q{job.quality}"
            cache_dir = self.cache_dir / cache_key
            cache_dir.mkdir(parents=True, exist_ok=True)
            intro_mp3 = cache_dir / "intro.mp3"
            outro_mp3 = cache_dir / "outro.mp3"
            tail_mp3 = cache_dir / "tail.mp3"

            self._ensure_mp3(self.intro_path, intro_mp3, sample_rate, channels, job.quality)
            self._ensure_mp3(self.outro_path, outro_mp3, sample_rate, channels, job.quality)
            self._ensure_mp3(self.tail_path, tail_mp3, sample_rate, channels, job.quality)

            main_mp3 = job.job_dir / "main.mp3"
            part_count = len(job.upload_paths)
            if part_count == 1:
                job.publish_stage("transcoding_main", "转码主音频…")
                info = main_infos[0]
                need_transcode = (
                    info.codec_name.lower() != "mp3" or info.sample_rate != sample_rate or info.channels != channels
                )
                if need_transcode:
                    self._ensure_mp3(
                        job.upload_paths[0],
                        main_mp3,
                        sample_rate,
                        channels,
                        job.quality,
                        on_progress=lambda pct, speed: job.publish_progress(pct, speed),
                        duration_seconds=info.duration_seconds,
                    )
                else:
                    shutil.copy2(job.upload_paths[0], main_mp3)
                    job.publish_progress(1.0, "copy")
            else:
                can_use_weights = all(i.duration_seconds > 0.01 for i in main_infos)
                total_weight = sum(i.duration_seconds for i in main_infos) if can_use_weights else float(part_count)
                done_weight = 0.0

                part_mp3s: list[Path] = []
                for idx, (src, info) in enumerate(zip(job.upload_paths, main_infos, strict=False), start=1):
                    job.publish_stage("transcoding_main", f"转码主音频（{idx}/{part_count}）…")
                    part_mp3 = job.job_dir / f"main_part_{idx}.mp3"
                    part_mp3s.append(part_mp3)
                    need_transcode = (
                        info.codec_name.lower() != "mp3"
                        or info.sample_rate != sample_rate
                        or info.channels != channels
                    )
                    weight = info.duration_seconds if can_use_weights else 1.0

                    def _progress(pct: float, speed: str | None) -> None:
                        overall = (done_weight + pct * weight) / total_weight if total_weight > 0 else pct
                        job.publish_progress(overall, speed, idx, part_count)

                    if need_transcode:
                        self._ensure_mp3(
                            src,
                            part_mp3,
                            sample_rate,
                            channels,
                            job.quality,
                            on_progress=_progress,
                            duration_seconds=info.duration_seconds,
                        )
                    else:
                        shutil.copy2(src, part_mp3)
                        _progress(1.0, "copy")

                    done_weight += weight

                job.publish_stage("combining_main", f"合并主音频（{part_count} 段）…")
                list_path = job.job_dir / "main_parts_list.txt"
                list_path.write_text(
                    "\n".join([f"file '{self._escape_for_concat(p)}'" for p in part_mp3s]),
                    encoding="utf-8",
                )
                self._run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(main_mp3)])

            job.publish_stage("concatenating", "拼接输出…")
            concat_list = job.job_dir / "concat_list.txt"
            lines = [f"file '{self._escape_for_concat(intro_mp3)}'"]
            for _ in range(job.repeat):
                lines.append(f"file '{self._escape_for_concat(main_mp3)}'")
            lines.append(f"file '{self._escape_for_concat(outro_mp3)}'")
            lines.append(f"file '{self._escape_for_concat(tail_mp3)}'")
            concat_list.write_text("\n".join(lines), encoding="utf-8")
            self._run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(job.output_path)])

            job.publish_stage("finalizing", "读取结果时长…")
            out_info = self._audio_info(job.output_path)
            duration_seconds = int(round(out_info.duration_seconds))
            job.output_duration_seconds = duration_seconds

            try:
                stem = Path(job.output_file).stem
                text = f"{stem}\t{duration_seconds}\n"
                self.latest_txt_path.write_text(text, encoding="utf-8")
            except Exception:
                pass

            elapsed_ms = int((time.perf_counter() - sw_start) * 1000)
            job.publish_done(
                {
                    "outputFile": job.output_file,
                    "outputPath": str(job.output_path),
                    "downloadUrl": f"/concat/download/{job.output_file}",
                    "elapsedMs": elapsed_ms,
                    "durationSeconds": duration_seconds,
                    "latestTxtPath": str(self.latest_txt_path),
                }
            )
        except Exception as exc:
            job.publish_error(str(exc))
        finally:
            try:
                shutil.rmtree(job.job_dir, ignore_errors=True)
            except Exception:
                pass

    def fixed_items(self) -> list[dict[str, Any]]:
        return [
            self._fixed_item("intro", self.intro_path),
            self._fixed_item("outro", self.outro_path),
            self._fixed_item("tail", self.tail_path),
        ]

    def _fixed_item(self, kind: str, path: Path) -> dict[str, Any]:
        url = f"/concat/fixed/{kind}"
        if not path.exists():
            return {
                "kind": kind,
                "exists": False,
                "fileName": path.name,
                "relativePath": f"assets/concat_fixed/{path.name}",
                "sizeBytes": 0,
                "durationSeconds": 0,
                "lastWriteUnixMs": 0,
                "url": url,
            }

        info = path.stat()
        duration = 0
        try:
            duration = int(round(self._audio_info(path).duration_seconds))
        except Exception:
            duration = 0
        return {
            "kind": kind,
            "exists": True,
            "fileName": path.name,
            "relativePath": f"assets/concat_fixed/{path.name}",
            "sizeBytes": info.st_size,
            "durationSeconds": duration,
            "lastWriteUnixMs": int(info.st_mtime * 1000),
            "url": url,
        }

    def replace_fixed(self, kind: str, file_path: Path) -> dict[str, Any]:
        dest = {
            "intro": self.intro_path,
            "outro": self.outro_path,
            "tail": self.tail_path,
        }.get(kind)
        if not dest:
            raise ValueError("kind 仅支持 intro/outro/tail")

        if file_path.suffix.lower() != ".mp3":
            raise ValueError("仅支持 .mp3 文件")

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".upload.tmp.mp3")
        shutil.copy2(file_path, tmp)

        # validate audio
        _ = self._audio_info(tmp)
        tmp.replace(dest)
        return self._fixed_item(kind, dest)
