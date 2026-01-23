from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


def _print(msg: str) -> None:
    print(msg, flush=True)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _safe_label(value: str) -> str:
    value = value.strip()
    # Make it safe as a filename on Windows (keep Unicode, only remove illegal chars).
    value = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1F]+', "_", value)
    value = value.strip().rstrip(" .")
    return value or "item"


def _list_storage_files(accounts_dir: Path) -> list[Path]:
    if not accounts_dir.exists():
        return []
    return sorted(p for p in accounts_dir.iterdir() if p.is_file() and p.suffix.lower() == ".json")


def _read_text_auto(path: Path, encoding: str | None = None) -> str:
    if encoding:
        return path.read_text(encoding=encoding)

    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue

    # Last resort: replace undecodable bytes
    return path.read_text(encoding="utf-8", errors="replace")


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def probe_duration_seconds(path: Path) -> float:
    ffprobe = _which("ffprobe")
    if ffprobe:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()
            return float(out)
        except Exception as e:
            _eprint(f"[WARN] ffprobe failed, will try mutagen: {e}")

    try:
        from mutagen import File as MutagenFile  # type: ignore

        audio = MutagenFile(str(path))
        if audio is None or not hasattr(audio, "info") or audio.info is None:
            raise RuntimeError("mutagen failed to parse audio file")
        length = getattr(audio.info, "length", None)
        if length is None:
            raise RuntimeError("mutagen could not read duration")
        return float(length)
    except Exception as e:
        raise RuntimeError(
            "Cannot determine audio duration. Install FFmpeg (ffprobe) or `pip install mutagen`."
        ) from e


def format_duration(seconds: float) -> str:
    seconds_i = max(0, int(seconds))
    m, s = divmod(seconds_i, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}h{m:02d}m{s:02d}s"
    return f"{m:02d}m{s:02d}s"


def convert_to_mp3(input_path: Path, output_path: Path, bitrate: str = "128k") -> None:
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH (cannot convert to mp3)")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(output_path),
    ]
    subprocess.check_call(cmd)


def _is_probable_text_file(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".md", ".markdown"}


def _looks_rate_limited(message: str) -> bool:
    msg = message.lower()
    return "rate limit" in msg or "quota" in msg or "too many requests" in msg


@dataclass
class AccountState:
    label: str
    storage_path: Path
    notebook_id: str | None = None
    disabled_reason: str | None = None

    @property
    def is_enabled(self) -> bool:
        return self.disabled_reason is None


async def ensure_notebook_ready(
    account: AccountState,
    report_path: Path,
    report_text: str | None,
    notebook_title: str,
    source_title: str,
    source_wait_timeout: float,
) -> None:
    # Import lazily so the script can print a helpful error if deps missing.
    from notebooklm import NotebookLMClient

    if account.notebook_id is not None:
        return

    _print(f"[INFO] ({account.label}) Creating notebook + adding source…")
    async with await NotebookLMClient.from_storage(str(account.storage_path)) as client:
        nb = await client.notebooks.create(notebook_title)
        account.notebook_id = nb.id

        if report_text is not None:
            await client.sources.add_text(
                nb.id,
                source_title,
                report_text,
                wait=True,
                wait_timeout=source_wait_timeout,
            )
        else:
            await client.sources.add_file(
                nb.id,
                str(report_path),
                wait=True,
                wait_timeout=source_wait_timeout,
            )


async def attempt_generate_and_check(
    account: AccountState,
    attempt_index: int,
    tmp_dir: Path,
    out_dir: Path,
    report_stem: str,
    min_seconds: float,
    generation_timeout: float,
    language: str,
    instructions: str,
    audio_format: str,
    audio_length: str,
    convert_mp3_flag: bool,
    keep_short_files: bool,
    delete_short_artifacts: bool,
) -> Path | None:
    from notebooklm import NotebookLMClient, RPCError
    from notebooklm.types import AudioFormat, AudioLength

    if account.notebook_id is None:
        raise RuntimeError("Internal error: notebook_id not set")

    # Map string -> enum (keep it simple and explicit)
    fmt = getattr(AudioFormat, audio_format, None)
    lng = getattr(AudioLength, audio_length, None)
    if fmt is None:
        raise ValueError(f"Unknown audio format: {audio_format}")
    if lng is None:
        raise ValueError(f"Unknown audio length: {audio_length}")

    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_audio = tmp_dir / f"{report_stem}_{account.label}_attempt{attempt_index:02d}.m4a"

    _print(f"[INFO] ({account.label}) Attempt {attempt_index}: generating audio…")
    try:
        async with await NotebookLMClient.from_storage(str(account.storage_path)) as client:
            status = await client.artifacts.generate_audio(
                account.notebook_id,
                language=language,
                instructions=instructions,
                audio_format=fmt,
                audio_length=lng,
            )

            if not status.task_id:
                raise RuntimeError("Generation returned no task_id")

            final = await client.artifacts.wait_for_completion(
                account.notebook_id,
                status.task_id,
                timeout=generation_timeout,
            )

            if final.is_failed:
                msg = final.error or "unknown error"
                _eprint(f"[WARN] ({account.label}) Generation failed: {msg}")
                if final.is_rate_limited or _looks_rate_limited(msg):
                    account.disabled_reason = f"rate_limited: {msg}"
                return None

            if not final.is_complete:
                _eprint(f"[WARN] ({account.label}) Generation not completed: {final.status}")
                return None

            _print(f"[INFO] ({account.label}) Downloading audio…")
            await client.artifacts.download_audio(
                account.notebook_id,
                str(tmp_audio),
                artifact_id=final.task_id,
            )

            dur = probe_duration_seconds(tmp_audio)
            _print(f"[INFO] ({account.label}) Duration: {format_duration(dur)}")

            if dur >= min_seconds:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                base = f"{report_stem}_{ts}_{account.label}_{format_duration(dur)}"

                if convert_mp3_flag:
                    mp3_path = out_dir / f"{base}.mp3"
                    try:
                        _print(f"[INFO] Converting to mp3: {mp3_path.name}")
                        convert_to_mp3(tmp_audio, mp3_path)
                        if not keep_short_files:
                            tmp_audio.unlink(missing_ok=True)
                        return mp3_path
                    except Exception as e:
                        _eprint(f"[WARN] mp3 convert failed, keeping .m4a: {e}")

                final_path = out_dir / f"{base}.m4a"
                shutil.move(str(tmp_audio), str(final_path))
                return final_path

            # Too short
            _print(f"[INFO] ({account.label}) Too short (< {format_duration(min_seconds)}), retrying…")
            if not keep_short_files:
                tmp_audio.unlink(missing_ok=True)

            if delete_short_artifacts:
                try:
                    await client.artifacts.delete(account.notebook_id, final.task_id)
                except Exception as e:
                    _eprint(f"[WARN] ({account.label}) Failed to delete short artifact: {e}")

            return None

    except RPCError as e:
        msg = str(e)
        _eprint(f"[WARN] ({account.label}) RPCError: {msg}")
        if _looks_rate_limited(msg):
            account.disabled_reason = f"rate_limited: {msg}"
        return None
    except Exception as e:
        msg = str(e)
        _eprint(f"[WARN] ({account.label}) Error: {msg}")
        if "authenticate" in msg.lower() or "login" in msg.lower():
            account.disabled_reason = f"auth_error: {msg}"
        return None


def build_default_instructions(min_minutes: int) -> str:
    return (
        "你是一档新闻解读播客的两位主持人（对话形式）。\n"
        "请基于全部资料生成中文播客，内容务必详尽、信息密度高。\n"
        "要求：\n"
        f"- 时长尽量接近 45-60 分钟，至少 {min_minutes} 分钟。\n"
        "- 覆盖所有重要新闻点：背景、数据/事实、影响、趋势、争议与不同观点。\n"
        "- 适当加入例子、类比和过渡，让内容连贯但不要空话。\n"
        "- 结尾做一个结构化总结（要点列表 + 今日关注点）。\n"
    ).strip()


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-generate NotebookLM audio until duration threshold.")
    p.add_argument("--report", type=Path, default=Path("report.md"), help="Path to report file")
    p.add_argument("--encoding", type=str, default=None, help="Report file encoding (default: auto)")
    p.add_argument("--accounts-dir", type=Path, default=Path("accounts"), help="Directory of *.json")
    p.add_argument("--out-dir", type=Path, default=Path("outputs"), help="Output directory")
    p.add_argument("--tmp-dir", type=Path, default=Path(".tmp_audio"), help="Temp directory")

    p.add_argument("--min-minutes", type=int, default=40, help="Minimum duration in minutes")
    p.add_argument("--max-attempts", type=int, default=20, help="Max total attempts across accounts")
    p.add_argument("--strategy", choices=["sequential", "roundrobin"], default="sequential")

    p.add_argument("--notebook-title", type=str, default=None, help="Notebook title (default: report stem + date)")
    p.add_argument("--source-title", type=str, default="Daily Report", help="Source title inside NotebookLM")
    p.add_argument("--source-wait-timeout", type=float, default=600.0, help="Seconds to wait for source ready")

    p.add_argument("--generation-timeout", type=float, default=1800.0, help="Seconds to wait per generation")
    p.add_argument("--language", type=str, default="zh", help="Language code passed to NotebookLM (e.g. zh/en)")

    p.add_argument("--audio-format", choices=["DEEP_DIVE", "BRIEF", "CRITIQUE", "DEBATE"], default="DEEP_DIVE")
    p.add_argument("--audio-length", choices=["SHORT", "DEFAULT", "LONG"], default="LONG")

    p.add_argument("--instructions", type=str, default=None, help="Override instructions text")
    p.add_argument("--instructions-file", type=Path, default=None, help="Load instructions from a file")

    p.add_argument("--convert-mp3", action="store_true", help="Convert output to mp3 (requires ffmpeg)")
    p.add_argument("--keep-short-files", action="store_true", help="Keep short temp files (debug)")
    p.add_argument(
        "--delete-short-artifacts",
        action="store_true",
        help="Delete short audio artifacts in NotebookLM (keeps notebook clean)",
    )
    return p.parse_args(argv)


async def run() -> int:
    args = parse_args(sys.argv[1:])

    report_path: Path = args.report
    if not report_path.exists():
        _eprint(f"[ERROR] Report not found: {report_path}")
        return 2

    storage_files = _list_storage_files(args.accounts_dir)
    if not storage_files:
        _eprint(f"[ERROR] No accounts found in: {args.accounts_dir}")
        _eprint("Put each account's storage_state.json as a *.json file in that folder.")
        return 2

    accounts = [
        AccountState(label=_safe_label(p.stem), storage_path=p.resolve())
        for p in storage_files
    ]

    min_seconds = float(args.min_minutes) * 60.0
    report_stem = _safe_label(report_path.stem)

    # Instructions
    instructions = args.instructions
    if args.instructions_file:
        instructions = _read_text_auto(args.instructions_file, None)
    if not instructions:
        instructions = build_default_instructions(args.min_minutes)

    # Read report or upload as file
    report_text: str | None = None
    if _is_probable_text_file(report_path):
        report_text = _read_text_auto(report_path, args.encoding)

    date_tag = datetime.now().strftime("%Y%m%d")
    notebook_title = args.notebook_title or f"{report_path.stem} {date_tag}"

    # Default behavior: keep NotebookLM clean by deleting short artifacts
    delete_short_artifacts = bool(args.delete_short_artifacts)

    _print("[INFO] Accounts:")
    for a in accounts:
        _print(f"  - {a.label}: {a.storage_path}")
    _print("")

    # Create notebook+source once per account as needed
    # Strategy: sequential = finish one account before moving on
    #           roundrobin = rotate accounts per attempt
    attempts_done = 0
    start = time.time()

    if args.strategy == "sequential":
        for account in accounts:
            await ensure_notebook_ready(
                account,
                report_path=report_path,
                report_text=report_text,
                notebook_title=f"{notebook_title} ({account.label})",
                source_title=args.source_title,
                source_wait_timeout=args.source_wait_timeout,
            )

            local_attempt = 0
            while attempts_done < args.max_attempts and account.is_enabled:
                local_attempt += 1
                attempts_done += 1
                result = await attempt_generate_and_check(
                    account=account,
                    attempt_index=local_attempt,
                    tmp_dir=args.tmp_dir,
                    out_dir=args.out_dir,
                    report_stem=report_stem,
                    min_seconds=min_seconds,
                    generation_timeout=args.generation_timeout,
                    language=args.language,
                    instructions=instructions,
                    audio_format=args.audio_format,
                    audio_length=args.audio_length,
                    convert_mp3_flag=bool(args.convert_mp3),
                    keep_short_files=bool(args.keep_short_files),
                    delete_short_artifacts=delete_short_artifacts,
                )
                if result:
                    elapsed = time.time() - start
                    _print("")
                    _print(f"[SUCCESS] Output: {result}")
                    _print(f"[INFO] Attempts: {attempts_done}, elapsed: {format_duration(elapsed)}")
                    return 0

            if not account.is_enabled:
                _eprint(f"[WARN] ({account.label}) Disabled: {account.disabled_reason}")

        _eprint("")
        _eprint("[FAIL] No audio met the duration threshold.")
        _eprint(f"[INFO] Attempts: {attempts_done}, elapsed: {format_duration(time.time() - start)}")
        return 1

    # roundrobin
    enabled: list[AccountState] = accounts[:]
    idx = 0
    while attempts_done < args.max_attempts and any(a.is_enabled for a in enabled):
        account = enabled[idx % len(enabled)]
        idx += 1
        if not account.is_enabled:
            continue

        await ensure_notebook_ready(
            account,
            report_path=report_path,
            report_text=report_text,
            notebook_title=f"{notebook_title} ({account.label})",
            source_title=args.source_title,
            source_wait_timeout=args.source_wait_timeout,
        )

        attempts_done += 1
        result = await attempt_generate_and_check(
            account=account,
            attempt_index=attempts_done,
            tmp_dir=args.tmp_dir,
            out_dir=args.out_dir,
            report_stem=report_stem,
            min_seconds=min_seconds,
            generation_timeout=args.generation_timeout,
            language=args.language,
            instructions=instructions,
            audio_format=args.audio_format,
            audio_length=args.audio_length,
            convert_mp3_flag=bool(args.convert_mp3),
            keep_short_files=bool(args.keep_short_files),
            delete_short_artifacts=delete_short_artifacts,
        )
        if result:
            elapsed = time.time() - start
            _print("")
            _print(f"[SUCCESS] Output: {result}")
            _print(f"[INFO] Attempts: {attempts_done}, elapsed: {format_duration(elapsed)}")
            return 0

        if not account.is_enabled:
            _eprint(f"[WARN] ({account.label}) Disabled: {account.disabled_reason}")

    _eprint("")
    _eprint("[FAIL] No audio met the duration threshold.")
    _eprint(f"[INFO] Attempts: {attempts_done}, elapsed: {format_duration(time.time() - start)}")
    return 1


def main() -> None:
    try:
        rc = asyncio.run(run())
    except KeyboardInterrupt:
        _eprint("\n[ABORT] Interrupted by user.")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()
