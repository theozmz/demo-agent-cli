"""StatusBar — reactive widget showing session state, Claude Code style."""

from __future__ import annotations

import time

from rich.markup import escape
from textual.widgets import Static


def _fmt_tokens_compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    minutes = int(ms / 60_000)
    seconds = int((ms % 60_000) / 1000)
    return f"{minutes}m{seconds}s"


class StatusBar(Static):
    """Compact one-line status bar. Uses middot (·) separators and Unicode
    status icons — matching Claude Code's Byline component style.

    Rendered in inactive text color via TCSS (.StatusBar).
    """

    def refresh_(
        self,
        instruction: str = "idle",
        turn: int = 0,
        token_count: int = 0,
        token_limit: int = 200_000,
        context_tokens: int = 0,
        model: str = "",
        subagent_tasks: dict | None = None,
    ) -> None:
        width = self.size.width if self.size else 80
        parts: list[str] = []

        label = escape(instruction) if instruction else "idle"
        if turn > 0 and not label.startswith("T"):
            label = f"T{turn} {label}"
        parts.append(label)

        if token_count > 0:
            used = _fmt_tokens_compact(token_count)
            limit = _fmt_tokens_compact(token_limit)
            ctx = _fmt_tokens_compact(context_tokens)
            parts.append(f"{used} tok ({ctx}/{limit})")
        elif context_tokens > 0:
            ctx = _fmt_tokens_compact(context_tokens)
            limit = _fmt_tokens_compact(token_limit)
            parts.append(f"ctx {ctx}/{limit}")

        if subagent_tasks:
            sub_parts: list[str] = []
            for sas in list(subagent_tasks.values())[:3]:
                label_s = escape(sas.task[:20])
                if len(sas.task) > 20:
                    label_s += "..."
                if sas.status == "running":
                    running_ms = (time.monotonic() - sas.start_time) * 1000
                    sub_parts.append(
                        f"● {label_s} {_fmt_duration(running_ms)}"
                    )
                elif sas.status == "completed":
                    sub_parts.append(f"✓ {label_s}")
                elif sas.status == "error":
                    sub_parts.append(f"✗ {label_s}")
                else:
                    sub_parts.append(f"{label_s}({sas.status[0]})")
            if sub_parts:
                parts.append(" ".join(sub_parts))

        if model:
            short = model.split("/")[-1] if "/" in model else model
            parts.append(short)

        sep = " · "
        line = sep.join(parts)

        if len(line) > width:
            line = (
                f"{label}{sep}"
                f"{_fmt_tokens_compact(token_count)}/{_fmt_tokens_compact(token_limit)}"
            )
            if len(line) > width:
                line = line[: width - 1]

        self.update(line)
