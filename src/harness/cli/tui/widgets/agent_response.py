"""AgentResponse — Markdown-rendered agent response widget."""

from __future__ import annotations

from textual.widgets import Markdown


class AgentResponse(Markdown):
    """Thin wrapper around Textual's built-in Markdown widget."""
