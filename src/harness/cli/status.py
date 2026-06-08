"""SessionStatus — shared mutable state for the CLI status bar."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.core.token_counter import TokenCounter


@dataclass
class SubAgentStatus:
    """Tracks a single sub-agent's runtime state."""
    task: str
    start_time: float = field(default_factory=time.monotonic)
    status: str = "running"  # "running" | "completed" | "error" | "timeout"
    duration_ms: float = 0.0


_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-sonnet-4": 200_000,
    "claude-opus-4": 200_000,
    "claude-haiku-4": 200_000,
    "claude-sonnet-3": 200_000,
    "claude-opus-3": 200_000,
    "claude-haiku-3": 200_000,
    "claude-3.5": 200_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_384,
    "deepseek": 128_000,
    "gemini": 1_000_000,
    "qwen": 128_000,
}
_DEFAULT_CONTEXT_WINDOW = 200_000


def _detect_context_window(model: str) -> int:
    """Return the known context window size for a model, or the default."""
    key = model.lower().split("/")[-1]
    for prefix, size in _KNOWN_CONTEXT_WINDOWS.items():
        if key.startswith(prefix):
            return size
    return _DEFAULT_CONTEXT_WINDOW


def _fmt_tokens_compact(n: int) -> str:
    """1234 -> '1.2k', 12345 -> '12.3k', 1234567 -> '1.2M'"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_duration(ms: float) -> str:
    """Format milliseconds: 1234 -> '1.2s', 123456 -> '2m3s'"""
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    minutes = int(ms / 60_000)
    seconds = int((ms % 60_000) / 1000)
    return f"{minutes}m{seconds}s"


class SessionStatus:
    """Mutable shared state tracking the agent session for the status bar.

    Updated by AgenticLoop / ChatDelegate / SubAgentManager during execution.
    Read by CLI layers (REPL, TUI) to render the status bar.
    """

    def __init__(
        self,
        model: str = "",
        context_limit: int | None = None,
    ):
        self.current_instruction: str = "idle"
        self.current_turn: int = 0
        self.model: str = model
        self.token_counter: TokenCounter | None = None
        self.context_tokens: int = 0
        self.context_limit: int = context_limit or _detect_context_window(model)
        self.subagent_tasks: dict[str, SubAgentStatus] = {}
        self._session_input_tokens: int = 0
        self._session_output_tokens: int = 0
        self._session_call_count: int = 0

    def subagent_start(self, tag: str, task: str) -> None:
        self.subagent_tasks[tag] = SubAgentStatus(task=task[:60])

    def subagent_end(self, tag: str, outcome: str = "completed") -> None:
        entry = self.subagent_tasks.get(tag)
        if entry:
            entry.status = outcome
            entry.duration_ms = (time.monotonic() - entry.start_time) * 1000
        self._prune_old_subagents()

    def snapshot_totals(self) -> None:
        """Capture cumulative totals for display across multiple turns."""
        if self.token_counter:
            self._session_input_tokens = self.token_counter.input_tokens
            self._session_output_tokens = self.token_counter.output_tokens
            self._session_call_count = self.token_counter.call_count

    def _prune_old_subagents(self) -> None:
        """Remove completed sub-agents older than 60 seconds."""
        now = time.monotonic()
        to_remove = []
        for tag, sas in self.subagent_tasks.items():
            if sas.status != "running" and (now - sas.start_time) > 60:
                to_remove.append(tag)
        for tag in to_remove:
            del self.subagent_tasks[tag]


def format_status_bar(status: SessionStatus, width: int = 80) -> str:
    """Render a compact single-line status bar.

    Layout: T{N} {instruction} | {tokens} ({ctx}/{limit}) | sub:{tasks} | {model}
    Narrow terminals drop trailing sections.
    """
    if status is None:
        return " Harness | idle"

    parts: list[str] = []

    # 1. Current instruction
    instruction = status.current_instruction or "idle"
    if status.current_turn > 0 and not instruction.startswith("T"):
        instruction = f"T{status.current_turn} {instruction}"
    parts.append(instruction)

    # 2. Token usage + context fill
    total_from_counter = status.token_counter.total if status.token_counter else 0
    session_total = status._session_input_tokens + status._session_output_tokens
    display_total = max(total_from_counter, session_total)
    if display_total > 0:
        used = _fmt_tokens_compact(display_total)
        limit = _fmt_tokens_compact(status.context_limit)
        ctx = _fmt_tokens_compact(status.context_tokens)
        parts.append(f"{used} tok ({ctx}/{limit})")
    elif status.context_tokens > 0:
        ctx = _fmt_tokens_compact(status.context_tokens)
        limit = _fmt_tokens_compact(status.context_limit)
        parts.append(f"ctx {ctx}/{limit}")

    # 3. Sub-agent tasks
    if status.subagent_tasks:
        sub_parts: list[str] = []
        for sas in status.subagent_tasks.values():
            dur = _fmt_duration(sas.duration_ms) if sas.duration_ms > 0 else ""
            label = sas.task[:20] + ("..." if len(sas.task) > 20 else "")
            if sas.status == "running":
                running_ms = (time.monotonic() - sas.start_time) * 1000
                sub_parts.append(f"{label}({_fmt_duration(running_ms)})")
            else:
                sub_parts.append(f"{label}({sas.status[0]},{dur})")
        if sub_parts:
            parts.append("sub:" + ", ".join(sub_parts[:3]))

    # 4. Model
    if status.model:
        short_model = status.model.split("/")[-1] if "/" in status.model else status.model
        parts.append(short_model)

    line = " | ".join(parts)

    if len(line) <= width:
        return line

    # Progressive truncation: drop model, then sub-agents
    fallback = [instruction]
    if display_total > 0:
        fallback.append(f"{_fmt_tokens_compact(display_total)}/{_fmt_tokens_compact(status.context_limit)}")
    elif status.context_tokens > 0:
        fallback.append(f"{_fmt_tokens_compact(status.context_tokens)}/{_fmt_tokens_compact(status.context_limit)}")
    line = " | ".join(fallback)
    if len(line) > width:
        line = line[:width - 1]
    return line
