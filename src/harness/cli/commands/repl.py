"""'harness repl' subcommand — interactive prompt_toolkit REPL."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from harness.cli.context import AppContext
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage

console = Console()
logger = logging.getLogger(__name__)

REPL_STYLE = Style.from_dict({
    "prompt": "bold ansigreen",
})


def _history_path() -> Path:
    base = Path.home() / ".harness"
    base.mkdir(parents=True, exist_ok=True)
    return base / "repl_history"


def handle_repl(ctx: AppContext, debug: bool) -> None:
    """Interactive REPL — multi-turn conversation with the agent.

    Ctrl+C interrupts the current response.  Ctrl+D exits.
    """
    console.print("[bold]Harness REPL[/bold] — type your message, Ctrl+C to interrupt, Ctrl+D to exit\n")

    session_id = str(uuid.uuid4())
    messages: list[ChatMessage] = []

    # Build delegate and loop config once
    delegate = ChatDelegate(
        llm=ctx.llm,
        tool_executor=ctx.tool_executor,
        gatherer=ctx.context_gatherer,
    )
    loop_cfg = LoopConfig(max_turns=ctx.config.loop.max_turns)

    blocks = ctx.context_gatherer.gather()
    system_prompt = ctx.context_gatherer.to_system_prompt(blocks)

    prompt_session = PromptSession(
        history=FileHistory(str(_history_path())),
        style=REPL_STYLE,
    )

    thread_id = str(uuid.uuid4())
    turn_num = 0

    while True:
        try:
            user_input = prompt_session.prompt([("class:prompt", "> ")])
        except KeyboardInterrupt:
            console.print("\n[dim](interrupted)[/dim]")
            continue
        except EOFError:
            console.print("\n[dim]Goodbye.[/dim]")
            break

        text = user_input.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit", "/q", "/exit"):
            console.print("[dim]Goodbye.[/dim]")
            break

        turn_num += 1
        messages.append(ChatMessage.user(text))

        loop_ctx = LoopContext(
            messages=list(messages),
            system_prompt=system_prompt,
            tool_registry=ctx.tool_registry,
            llm=ctx.llm,
            cwd=ctx.cwd,
        )

        loop = AgenticLoop(delegate=delegate, ctx=loop_ctx, config=loop_cfg)

        async def _run():
            return await loop.run()

        start = time.monotonic()
        try:
            outcome = asyncio.run(asyncio.wait_for(_run(), timeout=300))
        except asyncio.TimeoutError:
            console.print("[yellow]Turn timed out after 5 minutes.[/yellow]")
            continue

        elapsed = (time.monotonic() - start) * 1000

        if outcome.content:
            console.print(Markdown(outcome.content))
            messages.append(ChatMessage.assistant(outcome.content))
        elif outcome.kind == "error":
            console.print(f"[red]Error: {outcome.content}[/red]")
        else:
            console.print("[dim](no response)[/dim]")

        if debug:
            console.print(f"\n[dim]T{outcome.turns} · {outcome.duration_ms:.0f}ms[/dim]")

        # Prune accumulated messages to avoid unbounded growth
        if len(messages) > 50:
            messages = messages[-40:]


def add_repl_subparser(subparsers, shared_parent) -> None:
    """Add the 'repl' subcommand to an argparse subparsers group."""
    parser = subparsers.add_parser(
        "repl",
        parents=[shared_parent],
        help="Start an interactive REPL session",
    )
    parser.set_defaults(func=_repl_dispatch)
    return parser


def _repl_dispatch(args, ctx: AppContext) -> None:
    """Bridge from argparse namespace to handle_repl."""
    handle_repl(ctx=ctx, debug=args.debug)
