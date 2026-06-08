"""Harness TUI — Textual-based terminal UI for interactive agent sessions."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, Binding

from harness.cli.context import AppContext
from harness.cli.tui.screens.chat import ChatScreen


class HarnessApp(App[None]):
    """Full-screen terminal UI for the Harness AI coding agent."""

    CSS_PATH = Path(__file__).parent / "styles" / "app.tcss"

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("ctrl+q", "quit", "", show=False),
    ]

    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        super().__init__()

    def on_mount(self) -> None:
        self.push_screen(ChatScreen(self.ctx))

    def action_quit(self) -> None:
        screen = self.screen
        if isinstance(screen, ChatScreen) and screen.is_processing:
            # TODO: show confirmation dialog
            pass
        self.exit()


def run_tui(ctx: AppContext) -> None:
    """Launch the TUI application."""
    app = HarnessApp(ctx)
    app.run()
