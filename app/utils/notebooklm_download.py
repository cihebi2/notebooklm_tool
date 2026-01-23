from __future__ import annotations

from pathlib import Path

import httpx
from notebooklm.auth import load_httpx_cookies
from notebooklm.rpc.types import ArtifactStatus, StudioContentType


def _extract_audio_download_url(artifacts_data: list, artifact_id: str | None) -> str:
    audio_candidates = [
        a
        for a in artifacts_data
        if isinstance(a, list)
        and len(a) > 6
        and a[2] == StudioContentType.AUDIO
        and a[4] == ArtifactStatus.COMPLETED
    ]

    audio_art = None
    if artifact_id:
        audio_art = next((a for a in audio_candidates if a and a[0] == artifact_id), None)
        if not audio_art:
            raise ValueError(f"Audio artifact {artifact_id} not found or not ready.")
    else:
        audio_art = audio_candidates[0] if audio_candidates else None

    if not audio_art:
        raise ValueError("No completed audio overview found.")

    try:
        metadata = audio_art[6]
        if not isinstance(metadata, list) or len(metadata) <= 5:
            raise ValueError("Invalid audio metadata structure.")

        media_list = metadata[5]
        if not isinstance(media_list, list) or len(media_list) == 0:
            raise ValueError("No media URLs found.")

        url: str | None = None
        for item in media_list:
            if isinstance(item, list) and len(item) > 2 and item[2] == "audio/mp4":
                url = item[0]
                break

        if not url and len(media_list) > 0 and isinstance(media_list[0], list) and media_list[0]:
            url = media_list[0][0]

        if not url or not isinstance(url, str):
            raise ValueError("Could not extract download URL.")

        return url
    except (IndexError, TypeError) as e:  # pragma: no cover
        raise ValueError(f"Failed to parse audio artifact structure: {e}") from e


async def download_audio_with_storage(
    *,
    artifacts_api: object,
    storage_state_path: Path,
    notebook_id: str,
    artifact_id: str,
    output_path: Path,
) -> Path:
    """Download NotebookLM audio using cookies from a specific storage_state.json.

    notebooklm-py 的 download_audio() 当前会从默认的 ~/.notebooklm/storage_state.json 取 cookies，
    这会导致多账号场景下载失败。这里显式使用传入的 storage_state_path 来下载。
    """
    # We intentionally call the internal method used by notebooklm-py for listing studio artifacts.
    list_raw = getattr(artifacts_api, "_list_raw", None)
    if list_raw is None or not callable(list_raw):
        raise RuntimeError("artifacts_api missing _list_raw (notebooklm-py API changed?)")

    artifacts_data = await list_raw(notebook_id)
    url = _extract_audio_download_url(artifacts_data, artifact_id)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cookies = load_httpx_cookies(storage_state_path)
    async with httpx.AsyncClient(cookies=cookies, follow_redirects=True, timeout=60.0) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                raise ValueError(
                    "Download failed: received HTML instead of media file. Authentication may have expired."
                )
            with output_path.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

    return output_path
