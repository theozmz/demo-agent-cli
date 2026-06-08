"""ToolCallCard — card showing a tool call and its result, Claude Code style."""

from __future__ import annotations

from typing import Literal

from rich.markup import escape
from textual.widgets import Static


class ToolCallCard(Static):
    """Displays a tool call with state-driven left border and inline icons.

    State transitions: pending → success | error
    CSS classes control the left border color:
        .tool-call-pending → claude-orange
        .tool-call-success → green
        .tool-call-error   → red

    Use set_result() to resolve the card after tool execution.
    """

    _TRUNCATE_AT = 500

    def __init__(self, tool_name: str, params_summary: str) -> None:
        self._tool_name = tool_name
        self._params = params_summary
        self._result_text = ""
        self._state: Literal["pending", "success", "error"] = "pending"
        self._is_expanded = False
        self._full_output = ""

        super().__init__(self._build(), classes="tool-call-pending")

    def set_result(self, output: str, is_error: bool = False) -> None:
        self._full_output = output
        self._state = "error" if is_error else "success"

        self.set_class(False, "tool-call-pending")
        if is_error:
            self.set_class(True, "tool-call-error")
        else:
            self.set_class(True, "tool-call-success")

        display = output
        if len(display) > self._TRUNCATE_AT:
            display = display[: self._TRUNCATE_AT] + (
                f"\n...<truncated {len(output) - self._TRUNCATE_AT} chars>"
            )
        self._result_text = escape(display)
        self.update(self._build())

    def toggle_expand(self) -> None:
        if self._state == "pending":
            return
        self._is_expanded = not self._is_expanded
        if self._is_expanded:
            self._result_text = self._full_output
        else:
            truncated = self._full_output[: self._TRUNCATE_AT]
            self._result_text = truncated + (
                f"\n...<truncated {len(self._full_output) - self._TRUNCATE_AT} chars>"
            )
        self.update(self._build())

    def _build(self) -> str:
        name = escape(self._tool_name)
        params = escape(self._params)

        if self._state == "pending":
            c = "rgb(215,119,87)"
            return f"[{c}]● → {name}({params})[/{c}]"

        marker = "✗" if self._state == "error" else "←"
        c = "rgb(215,119,87)"
        header = f"[{c}]{marker} {name}({params})[/{c}]"

        if self._result_text:
            if self._state == "error":
                return f"{header}\n[bold rgb(255,107,128)]  {self._result_text}[/]"
            return f"{header}\n[dim]  {self._result_text}[/dim]"

        return header
