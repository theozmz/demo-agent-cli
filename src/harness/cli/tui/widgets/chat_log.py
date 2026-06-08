"""ChatLog — scrollable message container for the TUI, Claude Code style."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from rich.markup import escape
from textual.containers import VerticalScroll
from textual.widgets import Static, Markdown

from harness.cli.tui.widgets.tool_call_card import ToolCallCard


class ChatLog(VerticalScroll):
    """Vertical-scrolling message list. Messages are mounted as child widgets.

    Visual styling is driven by CSS classes:
        .user-message     — dark background block + claude-orange left border
        .assistant-message — plain white text, no background
        .system-message    — dimmed text
        .thinking-indicator — italic dim, shown during LLM calls
        .compact-summary   — dimmed dashed-border box for compaction notices
        .divider           — Unicode box-drawing horizontal rule
        .timestamp         — dimmed timestamp between message groups
    """

    def add_message(self, text: str, role: str, markup: bool = False) -> Static:
        """Add a message with role-based CSS class routing.

        User: dark background block, claude-orange left border.
        Assistant: plain text on dark background.
        System: dimmed subtle text.
        Thinking: italic dim indicator.
        Compact: dashed-border dimmed notice.
        """
        safe = escape(text) if not markup else text

        css_class: str
        if role == "user":
            css_class = "user-message"
        elif role == "assistant":
            css_class = "assistant-message"
        elif role == "thinking":
            css_class = "thinking-indicator"
        elif role == "system":
            css_class = "system-message"
        elif role == "compact":
            css_class = "compact-summary"
        else:
            css_class = ""

        msg = Static(safe, classes=css_class)
        self.mount(msg)
        self.call_after_refresh(self.scroll_end, animate=False)
        return msg

    def add_rich(self, segments: list[tuple[str, str]]) -> Static:
        """Add a message built from (style, text) segments.

        Text is automatically escaped to prevent markup injection.
        """
        parts: list[str] = []
        for style, text in segments:
            safe = escape(text)
            if style:
                parts.append(f"[{style}]{safe}[/{style}]")
            else:
                parts.append(safe)
        msg = Static("".join(parts))
        self.mount(msg)
        self.call_after_refresh(self.scroll_end, animate=False)
        return msg

    def add_markdown(self, text: str) -> Markdown:
        """Add a Markdown-rendered agent response."""
        md = Markdown(text, classes="assistant-markdown")
        self.mount(md)
        self.call_after_refresh(self.scroll_end, animate=False)
        return md

    def add_tool_call(self, name: str, params_summary: str) -> ToolCallCard:
        """Mount a ToolCallCard and return it for later result updates."""
        card = ToolCallCard(name, params_summary)
        self.mount(card)
        self.call_after_refresh(self.scroll_end, animate=False)
        return card

    def add_thinking(self) -> Static:
        """Mount a thinking indicator."""
        indicator = Static("Thinking...", classes="thinking-indicator")
        self.mount(indicator)
        self.call_after_refresh(self.scroll_end, animate=False)
        return indicator

    def remove_thinking(self) -> None:
        """Remove all thinking indicators."""
        for child in self.query(".thinking-indicator"):
            child.remove()

    def add_divider(self, label: str = "") -> Static:
        """Horizontal rule with optional centered label using box-drawing chars.

        Example:
            "──────────"  (no label)
            "───── compaction ─────"  (with label)
        """
        width = self.size.width if self.size else 80
        if label:
            remaining = max(4, width - len(label) - 2)
            left = remaining // 2
            right = remaining - left
            text = f"{'─' * left} {label} {'─' * right}"
        else:
            text = "─" * width
        div = Static(f"[dim]{text}[/dim]", classes="divider")
        self.mount(div)
        self.call_after_refresh(self.scroll_end, animate=False)
        return div

    def add_timestamp(self, dt: Optional[datetime] = None) -> Static:
        """Dimmed timestamp between message groups (e.g., '2:34 PM')."""
        ts = dt or datetime.now()
        formatted = ts.strftime("%-I:%M %p")
        stamp = Static(f"[dim]{formatted}[/dim]", classes="timestamp")
        self.mount(stamp)
        self.call_after_refresh(self.scroll_end, animate=False)
        return stamp

    def add_compact_summary(self, text: str) -> Static:
        """Compaction notice in dimmed box with dashed border."""
        msg = Static(f"[dim]{escape(text)}[/dim]", classes="compact-summary")
        self.mount(msg)
        self.call_after_refresh(self.scroll_end, animate=False)
        return msg
