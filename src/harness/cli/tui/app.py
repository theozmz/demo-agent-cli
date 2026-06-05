"""Harness TUI — textual-based terminal UI for interactive agent sessions."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Input, Static, RichLog
from textual import events

from harness.cli.context import AppContext
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage


class HarnessTui(App):
    """Full-screen terminal UI for Harness."""

    CSS = """
    #chat-log {
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
    }
    #status-bar {
        height: 1;
        dock: bottom;
        background: $surface;
    }
    Input {
        dock: bottom;
    }
    """

    def __init__(self, ctx: AppContext):
        super().__init__()
        self._ctx = ctx
        self._messages: list[ChatMessage] = []
        self._delegate = ChatDelegate(
            llm=ctx.llm,
            tool_executor=ctx.tool_executor,
            gatherer=ctx.context_gatherer,
        )
        self._loop_cfg = LoopConfig(max_turns=ctx.config.loop.max_turns)

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat-log", highlight=True, markup=True)
        yield Static(id="status-bar", markup=False)
        yield Input(placeholder="Type your message... (Ctrl+C to quit)")

    def on_mount(self) -> None:
        self.query_one("#chat-log", RichLog).write("[bold green]Harness TUI[/] — ready.")
        self.query_one("#status-bar", Static).update(
            f"Provider: {self._ctx.config.llm.provider} | Model: {self._ctx.config.llm.model}"
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        log = self.query_one("#chat-log", RichLog)
        status = self.query_one("#status-bar", Static)
        inp = self.query_one(Input)

        inp.value = ""
        inp.disabled = True

        log.write(f"\n[bold blue]You:[/] {text}")
        status.update("Thinking...")

        self._messages.append(ChatMessage.user(text))

        blocks = self._ctx.context_gatherer.gather()
        system_prompt = self._ctx.context_gatherer.to_system_prompt(blocks)

        loop_ctx = LoopContext(
            messages=list(self._messages),
            system_prompt=system_prompt,
            tool_registry=self._ctx.tool_registry,
            llm=self._ctx.llm,
            cwd=self._ctx.cwd,
        )
        loop = AgenticLoop(delegate=self._delegate, ctx=loop_ctx, config=self._loop_cfg)

        try:
            outcome = await asyncio.wait_for(loop.run(), timeout=300)
            if outcome.content:
                log.write(f"[bold green]Agent:[/] {outcome.content}")
                self._messages.append(ChatMessage.assistant(outcome.content))
                status.update(f"Turns: {outcome.turns} | {outcome.duration_ms:.0f}ms")
            else:
                log.write(f"[red]Error: {outcome.content}[/red]")
                status.update("Error")
        except asyncio.TimeoutError:
            log.write("[yellow]Turn timed out.[/yellow]")
            status.update("Timeout")

        inp.disabled = False
        inp.focus()


def run_tui(ctx: AppContext) -> None:
    """Launch the TUI application."""
    app = HarnessTui(ctx)
    app.run()
