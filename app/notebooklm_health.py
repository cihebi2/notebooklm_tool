from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(value: Any, *, limit: int = 800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return _short(value, limit=300)


def classify_notebooklm_error(exc: BaseException | str) -> dict[str, Any]:
    """Classify NotebookLM failures into UI-actionable buckets."""

    message = _short(exc, limit=1600)
    lower = message.lower()

    category = "UNKNOWN"
    expired = False
    hint = "保留完整错误日志；如果多个账号同时失败，再判断是否为 NotebookLM 私有接口变化。"

    if (
        "authentication expired" in lower
        or "run 'notebooklm login'" in lower
        or "accounts.google.com" in lower
        or "redirected to:" in lower
        or "http 401" in lower
        or "unauthorized" in lower
    ):
        category = "AUTH_EXPIRED"
        expired = True
        hint = "账号授权已失效：请重新授权，用浏览器登录添加，或从当前已登录的 Edge/Chrome Profile 重新导入。"
    elif "csrf token not found" in lower or "session id not found" in lower:
        category = "TOKEN_EXTRACT_FAILED"
        hint = "NotebookLM 页面 token 提取失败：可能是授权失效，也可能是页面结构变化；先重新授权，再考虑升级 notebooklm-py。"
    elif "no result found for rpc id" in lower or ("rpc id" in lower and "not found" in lower):
        category = "API_CHANGED"
        hint = "NotebookLM 私有 RPC 可能变化：升级 notebooklm-py，并打开 NOTEBOOKLM_DEBUG_RPC=1 采集 RPC 细节。"
    elif "rate limit" in lower or "quota" in lower or "user_displayable_error" in lower:
        category = "RATE_LIMITED"
        hint = "账号可能触发额度或风控：降低账号并发、单账号并行数，并换账号/等待额度恢复。"
    elif "timeout" in lower or "readtimeout" in lower or "remoteprotocolerror" in lower:
        category = "NETWORK"
        hint = "网络或 NotebookLM 长任务连接不稳定：可重试，但不要同时提高并发。"

    return {
        "category": category,
        "expired": expired,
        "message": message,
        "hint": hint,
    }


def no_task_id_failure_details(status: Any) -> dict[str, Any]:
    """Preserve upstream generation failure fields when task_id is empty."""

    upstream_error = _get_value(status, "error")
    upstream_error_code = _get_value(status, "error_code")
    upstream_status = _get_value(status, "status")
    metadata = _get_value(status, "metadata")

    error = _short(upstream_error, limit=1200) or "generate_audio returned empty task id"
    error_code = _short(upstream_error_code, limit=120) or "NO_TASK_ID"

    details: dict[str, Any] = {
        "task_id": "",
        "error": error,
        "error_code": error_code,
        "local_reason": "generate_audio returned empty task id",
    }
    if upstream_status:
        details["status"] = _short(upstream_status, limit=120)
    if upstream_error:
        details["upstream_error"] = _short(upstream_error, limit=1200)
    if upstream_error_code:
        details["upstream_error_code"] = _short(upstream_error_code, limit=120)
    if metadata is not None:
        details["metadata"] = _jsonable(metadata)
    return details


async def check_account_health(account: Any, *, timeout: float = 30.0) -> dict[str, Any]:
    """Fetch NotebookLM notebooks to prove the stored auth still works."""

    checked_at = _now_iso()
    base = {
        "account_id": getattr(account, "id", None),
        "account_name": getattr(account, "name", None),
        "storage_path": getattr(account, "storage_path", None),
        "checked_at": checked_at,
    }

    try:
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage(
            getattr(account, "storage_path"), timeout=timeout
        ) as client:
            notebooks = await client.notebooks.list()
        return {
            **base,
            "ok": True,
            "category": "OK",
            "expired": False,
            "notebooks": len(notebooks),
            "message": "NotebookLM auth is healthy.",
            "hint": "账号可访问 NotebookLM。",
        }
    except Exception as exc:
        detail = classify_notebooklm_error(exc)
        return {
            **base,
            "ok": False,
            **detail,
        }
