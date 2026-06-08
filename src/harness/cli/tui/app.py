"""Harness TUI — textual-based terminal UI for interactive agent sessions."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Header, Footer, Input, Static, RichLog
from textual import events

from harness.cli.context import AppContext
from harness.cli.status import SessionStatus, format_status_bar
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig, LoopEvent
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
        self._status = SessionStatus(model=ctx.config.llm.model)
        # Wire sub-agent manager for status tracking
        agent_tool = ctx.tool_registry.get("agent")
        if agent_tool is not None and hasattr(agent_tool, '_manager'):
            agent_tool._manager._status = self._status

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="chat-log", highlight=True, markup=True)
        yield Static(id="status-bar", markup=False)
        yield Input(placeholder="Type your message... (Ctrl+C to quit)")

    def on_mount(self) -> None:
        self.query_one("#chat-log", RichLog).write("[bold green]Harness TUI[/] — ready.")
        self._update_status_bar()
        self.set_interval(1.0, self._refresh_status_bar)

    def _refresh_status_bar(self) -> None:
        """Periodic refresh for running sub-agent durations."""
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        status_widget = self.query_one("#status-bar", Static)
        line = format_status_bar(self._status, self.size.width)
        status_widget.update(line)

    def _on_loop_event(self, ev: LoopEvent) -> None:
        """Handle loop events: update log and status."""
        log = self.query_one("#chat-log", RichLog)
        if ev.kind == "thinking":
            self._status.current_turn = ev.iteration
            self._status.current_instruction = f"Turn {ev.iteration} — thinking..."
        elif ev.kind == "tool_call":
            inp = ev.tool_input or {}
            args_str = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:2])
            self._status.current_instruction = f"→ {ev.tool_name}({args_str})"
            log.write(f"[cyan]  → {ev.tool_name}({args_str})[/cyan]")
        elif ev.kind == "tool_result":
            display = ev.tool_output
            if len(display) > 300:
                display = display[:300] + "..."
            style = "red" if ev.tool_error else "dim"
            log.write(f"[{style}]    {display}[/{style}]")
            marker = "✗" if ev.tool_error else "←"
            self._status.current_instruction = f"  {marker} {ev.tool_name}"
        elif ev.kind == "llm_tokens":
            if self._status.token_counter:
                self._status.context_tokens = self._status.token_counter.total
        elif ev.kind == "compact":
            self._status.current_instruction = "compacting..."
        elif ev.kind == "done":
            self._status.current_instruction = "idle"
            self._status.current_turn = 0
        elif ev.kind == "retry":
            log.write(f"[yellow]Retry {ev.retry_attempt}: {ev.retry_error[:100]}[/yellow]")
        self._update_status_bar()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        log = self.query_one("#chat-log", RichLog)
        inp = self.query_one(Input)

        inp.value = ""
        inp.disabled = True

        log.write(f"\n[bold blue]You:[/] {text}")
        self._status.current_instruction = "thinking..."

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
            outcome = await asyncio.wait_for(
                loop.run(on_event=self._on_loop_event, status=self._status),
                timeout=3600,
            )
            self._status.snapshot_totals()
            if outcome.kind == "completed" and outcome.content:
                log.write(f"[bold green]Agent:[/] {outcome.content}")
                self._messages.append(ChatMessage.assistant(outcome.content))
                self._status.current_instruction = "idle"
            elif outcome.kind == "error":
                log.write(f"[red]Error: {outcome.content}[/red]")
                self._status.current_instruction = "idle"
            else:
                log.write(f"[dim]({outcome.kind})[/dim]")
                self._status.current_instruction = "idle"
        except asyncio.TimeoutError:
            log.write("[yellow]Turn timed out.[/yellow]")
            self._status.current_instruction = "idle"
            self._status.current_turn = 0
        except Exception as exc:
            log.write(f"[red]Unexpected error: {exc}[/red]")
            self._status.current_instruction = "idle"
            self._status.current_turn = 0

        self._update_status_bar()
        inp.disabled = False
        inp.focus()


def run_tui(ctx: AppContext) -> None:
    """Launch the TUI application."""
    app = HarnessTui(ctx)
    app.run()
