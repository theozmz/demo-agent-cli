"""TaskLogger — structured JSONL logger for task execution traces.

Each **session** gets its own file: ``logs/<YYYY-MM-DD-HHMMSS>-<name>.jsonl``.
All interactions within a session append to that file.

Implements the credit-assignment event schema from Li et al. (2026):
context variable dimensions (P/S/M) × feedback granularity (G0–G3).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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


def _safe_filename(name: str, max_len: int = 50) -> str:
    """Sanitize a string for use as a filename segment."""
    safe = re.sub(r'[^\w\-]', '_', name).strip('_') or "session"
    return safe[:max_len]


class TaskLogger:
    """Structured JSONL logger — one file per session.

    File naming: ``logs/<YYYY-MM-DD-HHMMSS>-<session_name>.jsonl``

    Usage::

        task_log = TaskLogger(session_name="refactor-auth")
        task_log.log_turn(turn=1, messages_in=3, response="...",
                          tool_calls=[...], tokens_in=1200, tokens_out=300,
                          duration_ms=1520.5)
        task_log.log_task_end(outcome="completed", ...)
        task_log.close()
    """

    def __init__(self, session_id: str = "", session_name: str = "", log_dir: str | Path = "logs"):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self._session_id = session_id or timestamp
        self._session_name = session_name or ""
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # File naming:
        # - If session_name is given → YYYYMMDD-HHMMSS-name.jsonl
        # - If only session_id (legacy) → session_id.jsonl (backward compatible)
        if session_name:
            name_part = _safe_filename(session_name)
            filename = f"{timestamp}-{name_part}.jsonl"
        elif session_id:
            filename = f"{session_id}.jsonl"
        else:
            filename = f"{timestamp}.jsonl"

        self._file_path = self._log_dir / filename
        self._file: Any = None
        self._open()

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open (or reopen) the session log file in append mode."""
        if self._file:
            self._file.close()
            self._file = None
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

    def log_turn(
        self,
        *,
        turn: int = 0,
        event: str = "",
        messages_in: int = 0,
        response: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
        tokens_input: int = 0,
        tokens_output: int = 0,
        duration_ms: float = 0.0,
        error: str = "",
    ) -> None:
        """Log a consolidated turn record — one per agent loop iteration.

        Each turn captures: LLM input message count, LLM response text,
        tool calls made with params, tool results, token usage, and timing.
        This is the primary log format for session analysis.
        """
        self._emit(
            event or "turn",
            turn=turn,
            messages_in=messages_in,
            response=_truncate(response, 2000),
            tool_calls=[
                {
                    "name": tc.get("name", ""),
                    "params": _sanitize_params(tc.get("params", {})),
                }
                for tc in (tool_calls or [])
            ],
            tool_results=[
                {
                    "name": tr.get("name", ""),
                    "is_error": tr.get("is_error", False),
                    "summary": _truncate(tr.get("summary", ""), 300),
                }
                for tr in (tool_results or [])
            ],
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            duration_ms=round(duration_ms, 1),
            error=error,
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

    def log_attribution(
        self,
        *,
        dimension: Literal["P", "S", "M"] = "S",
        granularity: Literal["G0", "G1", "G2", "G3"] = "G0",
        event_kind: str = "",
        tool_name: str = "",
        iteration: int = 0,
        outcome: str = "",
        detail: str = "",
    ) -> None:
        """Log a credit-assignment signal per Li et al. (2026) taxonomy.

        Args:
            dimension: Context variable — P (prompt), S (structural), M (memory).
            granularity: Feedback signal granularity — G0 (outcome scalar),
                G1 (process text), G2 (component-attributed), G3 (harness-level).
            event_kind: The ``LoopEvent.kind`` that produced this signal.
            tool_name: Tool involved (for G2 component-attributed signals).
            iteration: Turn number in the agent loop.
            outcome: Task-level outcome for G0 signals.
            detail: Human-readable attribution note.
        """
        self._emit(
            "attribution",
            dimension=dimension,
            granularity=granularity,
            event_kind=event_kind,
            tool_name=tool_name,
            iteration=iteration,
            outcome=outcome,
            detail=detail,
        )

    def log_compaction(
        self,
        *,
        strategy: str = "",
        tokens_before: int = 0,
        tokens_after: int = 0,
        truncated_count: int = 0,
        iteration: int = 0,
    ) -> None:
        """Log a compaction event — G3 cross-dimensional harness signal.

        Compaction reflects tension across all three context dimensions:
        P (what text survives), S (which tool results are stubbed),
        M (how and when state is reduced).
        """
        self._emit(
            "compaction",
            strategy=strategy,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            truncated_count=truncated_count,
            iteration=iteration,
            dimension="M",
            granularity="G3",
        )

    def log_event_summary(
        self,
        *,
        p_count: int = 0,
        s_count: int = 0,
        m_count: int = 0,
        g0_count: int = 0,
        g1_count: int = 0,
        g2_count: int = 0,
        g3_count: int = 0,
    ) -> None:
        """Log a summary of P/S/M and G0–G3 event counts for the session.

        Called at session end to enable quick credit-assignment analysis
        without re-parsing the entire JSONL file.
        """
        self._emit(
            "event_summary",
            p_count=p_count,
            s_count=s_count,
            m_count=m_count,
            g0_count=g0_count,
            g1_count=g1_count,
            g2_count=g2_count,
            g3_count=g3_count,
        )

    def close(self) -> None:
        """Close the underlying log file."""
        if self._file:
            self._file.close()
            self._file = None
