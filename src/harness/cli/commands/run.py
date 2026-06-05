"""'harness run' subcommand — send a prompt to the agent."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from harness.cli.context import AppContext
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage
from harness.logging.task_logger import TaskLogger

console = Console()
logger = logging.getLogger(__name__)


def handle_run(
    ctx: AppContext,
    prompt_text: str,
    max_turns: int,
    debug: bool,
    workspace: str = "",
) -> None:
    """Execute the 'run' command — send a one-shot prompt to the agent.

    All infrastructure is provided via *ctx* (already initialized).
    This function only deals with assembly and execution.
    """
    session_id = str(uuid.uuid4())
    task_logger = TaskLogger(session_id=session_id)

    # Resolve workspace root
    workspace_root = _resolve_workspace(workspace, ctx)

    # Build loop delegate
    delegate = ChatDelegate(
        llm=ctx.llm,
        tool_executor=ctx.tool_executor,
        gatherer=ctx.context_gatherer,
        task_logger=task_logger,
    )
    delegate._session_id = session_id
    delegate._workspace_root = workspace_root

    # Loop configuration
    loop_config = LoopConfig(
        max_turns=max_turns,
        compaction_threshold=ctx.config.loop.compaction_threshold,
    )

    # Assemble context
    blocks = ctx.context_gatherer.gather()
    system_prompt = ctx.context_gatherer.to_system_prompt(blocks)
    messages = [ChatMessage.user(prompt_text)]

    # Log context + start
    task_logger.log_context(
        block_count=len(blocks),
        block_types=[b.kind.value for b in blocks],
        tool_count=len(ctx.tool_registry.get_schemas()),
        has_repomap=ctx.config.repomap.enabled,
        cwd=ctx.cwd,
    )
    task_logger.log_task_start(
        user_prompt=prompt_text,
        provider=ctx.config.llm.provider,
        model=ctx.config.llm.model,
        cwd=ctx.cwd,
        max_turns=max_turns,
    )

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

        # Log task end
        task_logger.log_task_end(
            outcome=outcome.kind,
            turns=outcome.turns,
            total_duration_ms=outcome.duration_ms,
            tokens_used=outcome.tokens_used,
            error=outcome.content if outcome.kind == "error" else "",
        )
        task_logger.close()

    asyncio.run(_run())


def _resolve_workspace(workspace: str, ctx: AppContext) -> str:
    """Resolve the workspace root path."""
    if workspace:
        p = Path(workspace)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        return str(p.resolve())
    # Default: restrict to CWD (the harness project root)
    return str(Path(ctx.cwd).resolve())


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
    parser.add_argument(
        "-r", "--repomap",
        action="store_true",
        default=None,
        help="Enable repository map in system prompt (overrides config)",
    )
    parser.add_argument(
        "-w", "--workspace",
        default="",
        help="Restrict file tool access to this directory (absolute or relative to CWD)",
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
        workspace=getattr(args, "workspace", ""),
    )
