"""TaskLogger — structured JSONL logger for task execution traces.

Each **session** gets its own file: ``logs/<session_id>.jsonl``.
All interactions within a session append to that file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive parameter keys that should never appear in logs
_SENSITIVE_PARAMS = {"api_key", "password", "secret", "token", "authorization"}


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *params* with sensitive values redacted."""
    if not params:
        return {}
    return {
        k: "***" if k.lower() in _SENSITIVE_PARAMS else v
        for k, v in params.items()
    }


def _truncate(text: str, max_len: int = 500) -> str:
    """Truncate long strings for log readability."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...<truncated {len(text) - max_len} chars>"


class TaskLogger:
    """Structured JSONL logger — one file per session.

    Usage::

        task_log = TaskLogger(session_id="abc-123")
        task_log.log_task_start(user_prompt="...")
        task_log.log_llm_call(model="gpt-4o", ...)
        task_log.log_task_end(outcome="completed", ...)
        task_log.close()
    """

    def __init__(self, session_id: str, log_dir: str | Path = "logs"):
        self._session_id = session_id
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._file_path = self._log_dir / f"{session_id}.jsonl"
        self._file: Any = None
        self._open()

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open (or reopen) the session log file in append mode."""
        if self._file:
            self._file.close()
        self._file = open(str(self._file_path), "a", encoding="utf-8")

    def _emit(self, event: str, **fields: Any) -> None:
        """Write a single JSON line to the session log file."""
        try:
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "session_id": self._session_id,
                "event": event,
                **fields,
            }
            if self._file is None:
                self._open()
            self._file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._file.flush()
        except Exception as exc:
            logger.warning("Failed to write task log: %s", exc)

    # ------------------------------------------------------------------
    # public logging methods
    # ------------------------------------------------------------------

    def log_task_start(
        self,
        user_prompt: str,
        *,
        provider: str = "",
        model: str = "",
        cwd: str = "",
        max_turns: int = 0,
    ) -> None:
        self._emit(
            "task_start",
            user_prompt=_truncate(user_prompt, 2000),
            provider=provider,
            model=model,
            cwd=cwd,
            max_turns=max_turns,
        )

    def log_task_end(
        self,
        *,
        outcome: str = "completed",
        turns: int = 0,
        total_duration_ms: float = 0.0,
        tokens_used: int = 0,
        error: str = "",
    ) -> None:
        self._emit(
            "task_end",
            outcome=outcome,
            turns=turns,
            total_duration_ms=round(total_duration_ms, 1),
            tokens_used=tokens_used,
            error=error,
        )

    def log_llm_call(
        self,
        *,
        model: str = "",
        provider: str = "",
        messages_count: int = 0,
        has_tools: bool = False,
        response_type: str = "",
        tokens_input: int = 0,
        tokens_output: int = 0,
        duration_ms: float = 0.0,
        iteration: int = 0,
        error: str = "",
    ) -> None:
        self._emit(
            "llm_call",
            model=model,
            provider=provider,
            messages_count=messages_count,
            has_tools=has_tools,
            response_type=response_type,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            duration_ms=round(duration_ms, 1),
            iteration=iteration,
            error=error,
        )

    def log_tool_call(
        self,
        *,
        tool_name: str = "",
        params: dict[str, Any] | None = None,
        is_error: bool = False,
        exit_code: int = 0,
        result_summary: str = "",
        duration_ms: float = 0.0,
        blocked: bool = False,
    ) -> None:
        self._emit(
            "tool_call",
            tool_name=tool_name,
            params=_sanitize_params(params or {}),
            is_error=is_error,
            exit_code=exit_code,
            result_summary=_truncate(result_summary, 300),
            duration_ms=round(duration_ms, 1),
            blocked=blocked,
        )

    def log_memory_op(
        self,
        *,
        operation: str = "",
        key: str = "",
        value_summary: str = "",
        found: bool | None = None,
    ) -> None:
        self._emit(
            "memory_op",
            operation=operation,
            key=key,
            value_summary=_truncate(value_summary, 300),
            found=found,
        )

    def log_context(
        self,
        *,
        block_count: int = 0,
        block_types: list[str] | None = None,
        tool_count: int = 0,
        has_repomap: bool = False,
        cwd: str = "",
    ) -> None:
        self._emit(
            "context",
            block_count=block_count,
            block_types=block_types or [],
            tool_count=tool_count,
            has_repomap=has_repomap,
            cwd=cwd,
        )

    def log_error(
        self,
        *,
        source: str = "",
        message: str = "",
        tool_name: str = "",
    ) -> None:
        self._emit(
            "error",
            source=source,
            message=_truncate(message, 1000),
            tool_name=tool_name,
        )

    def close(self) -> None:
        """Close the underlying log file."""
        if self._file:
            self._file.close()
            self._file = None
