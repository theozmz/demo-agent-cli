"""'harness run' subcommand — send a prompt to the agent."""

from __future__ import annotations

import asyncio
import logging

from rich.console import Console
from rich.markdown import Markdown

from harness.cli.context import AppContext
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage

console = Console()
logger = logging.getLogger(__name__)


def handle_run(
    ctx: AppContext,
    prompt_text: str,
    max_turns: int,
    debug: bool,
) -> None:
    """Execute the 'run' command — send a one-shot prompt to the agent.

    All infrastructure is provided via *ctx* (already initialized).
    This function only deals with assembly and execution.
    """
    # Build loop delegate
    delegate = ChatDelegate(
        llm=ctx.llm,
        tool_executor=ctx.tool_executor,
        gatherer=ctx.context_gatherer,
    )

    # Loop configuration
    loop_config = LoopConfig(
        max_turns=max_turns,
        compaction_threshold=ctx.config.loop.compaction_threshold,
    )

    # Assemble context
    blocks = ctx.context_gatherer.gather()
    system_prompt = ctx.context_gatherer.to_system_prompt(blocks)
    messages = [ChatMessage.user(prompt_text)]

    loop_ctx = LoopContext(
        messages=messages,
        system_prompt=system_prompt,
        tool_registry=ctx.tool_registry,
        llm=ctx.llm,
        cwd=ctx.cwd,
    )

    # Run
    loop = AgenticLoop(delegate=delegate, ctx=loop_ctx, config=loop_config)

    async def _run():
        outcome = await loop.run()
        if outcome.content:
            console.print(Markdown(outcome.content))
        elif outcome.kind == "error":
            console.print(f"[red]Error: {outcome.content}[/red]")
        if debug:
            console.print(
                f"\n[dim]Turns: {outcome.turns}, "
                f"Duration: {outcome.duration_ms:.0f}ms[/dim]"
            )

    asyncio.run(_run())


def add_run_subparser(subparsers, shared_parent) -> None:
    """Add the 'run' subcommand to an argparse subparsers group."""
    parser = subparsers.add_parser(
        "run",
        parents=[shared_parent],
        help="Send a one-shot prompt to the agent",
    )
    parser.add_argument(
        "text",
        help="The prompt to send to the agent",
    )
    parser.add_argument(
        "-p", "--provider",
        default=None,
        choices=["anthropic", "openai", "groq", "deepseek", "openrouter", "ollama"],
        help="Override the configured provider (anthropic|openai|groq|deepseek|openrouter|ollama)",
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help="Override the configured model",
    )
    parser.add_argument(
        "-n", "--max-turns",
        type=int,
        default=30,
        help="Maximum tool-calling turns (default: 30)",
    )
    parser.set_defaults(func=_run_dispatch)
    return parser


def _run_dispatch(args, ctx: AppContext) -> None:
    """Bridge from argparse namespace to handle_run."""
    handle_run(
        ctx=ctx,
        prompt_text=args.text,
        max_turns=args.max_turns,
        debug=args.debug,
    )
