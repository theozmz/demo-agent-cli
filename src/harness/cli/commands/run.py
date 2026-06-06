"""'harness run' subcommand — send a prompt to the agent."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown

from harness.cli.context import AppContext
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig, LoopEvent
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage
from harness.logging.task_logger import TaskLogger

console = Console()
logger = logging.getLogger(__name__)

# Max chars for tool output display before truncation
_MAX_TOOL_DISPLAY = 500
# Max retries for LLM calls (matches loop.py)
_MAX_RETRIES = 3


def handle_run(
    ctx: AppContext,
    prompt_text: str,
    max_turns: int,
    debug: bool,
    workspace: str = "",
    mode: str = "",
) -> None:
    """Execute the 'run' command — send a one-shot prompt to the agent.

    All infrastructure is provided via *ctx* (already initialized).
    This function only deals with assembly and execution.

    ## Autonomous Multi-Agent Triggering (ComplexityGate)

    Before any agent runs, the ``ComplexityGate`` assesses the task:

    ┌──────────────────────────────────────────────────────────────┐
    │  User prompt → ComplexityGate.assess_and_select()            │
    │                                                              │
    │  SIMPLE       → native standard (single agent, low overhead) │
    │  INTEGRATION  → langgraph pair_coding (coder + reviewer)     │
    │  ARCHITECTURE → langgraph multi_agent (controller + team)    │
    │                                                              │
    │  Low confidence → fall back to configured engine/mode        │
    └──────────────────────────────────────────────────────────────┘

    The gate enables agents to autonomously evaluate "how complex is this
    task?" and self-select the appropriate collaboration topology —
    no user configuration needed.

    Controlled by harness.toml [loop] auto_mode, auto_mode_threshold.
    """
    from harness.langgraph.gate import create_complexity_gate, ComplexityGate

    session_id = str(uuid.uuid4())
    task_logger = TaskLogger(session_id=session_id)

    # Resolve workspace root
    workspace_root = _resolve_workspace(workspace, ctx)

    # ==================================================================
    # ComplexityGate — autonomous pre-flight assessment
    # ==================================================================
    gate = create_complexity_gate(ctx.config)

    selection = gate.assess_and_select(
        task=prompt_text,
        current_engine=ctx.config.loop.engine,
        current_mode=mode or ctx.config.loop.mode,
        force_engine="langgraph" if ctx.config.loop.engine == "langgraph" else "",
        force_mode=mode,
    )

    if debug or selection.auto_triggered:
        console.print(
            f"[dim]ComplexityGate: {selection.complexity.tier.value} "
            f"(confidence: {selection.complexity.confidence:.0%}) → "
            f"engine={selection.engine}, mode={selection.mode}"
            f"{' [AUTO]' if selection.auto_triggered else ''}[/dim]"
        )

    # ==================================================================
    # Dispatch: LangGraph or Native
    # ==================================================================
    delegate = ctx.langgraph_delegate

    # If gate auto-triggered langgraph but no delegate exists yet, build one
    if selection.engine == "langgraph" and delegate is None:
        delegate = _build_langgraph_on_demand(
            ctx=ctx,
            mode=selection.mode,
            human_approval=ctx.config.loop.human_approval,
            max_review_iterations=ctx.config.loop.max_review_iterations,
        )
        if delegate:
            console.print(
                f"[bold cyan]🚀 Auto-triggered LangGraph multi-agent "
                f"(mode={selection.mode})[/bold cyan]"
            )

    if delegate is not None:
        delegate._mode = selection.mode  # Ensure mode matches selection
        _run_langgraph(
            ctx=ctx,
            delegate=delegate,
            prompt_text=prompt_text,
            session_id=session_id,
            workspace_root=workspace_root,
            task_logger=task_logger,
            debug=debug,
        )
        return

    # ------------------------------------------------------------------
    # Native path (existing)
    # ------------------------------------------------------------------
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

    # Progress callback — real-time console output with truncation
    def _on_event(ev: LoopEvent) -> None:
        if ev.kind == "thinking":
            console.print(f"[dim]Turn {ev.iteration} — thinking...[/dim]")
        elif ev.kind == "retry":
            console.print(
                f"[yellow]Retry {ev.retry_attempt}/{_MAX_RETRIES}: "
                f"{ev.retry_error[:100]}[/yellow]"
            )
        elif ev.kind == "tool_call":
            params_str = _format_params(ev.tool_input or {})
            console.print(f"[cyan]→ {ev.tool_name}({params_str})[/cyan]")
        elif ev.kind == "tool_result":
            output = ev.tool_output
            if len(output) > _MAX_TOOL_DISPLAY:
                output = output[:_MAX_TOOL_DISPLAY] + f"\n...<truncated {len(ev.tool_output) - _MAX_TOOL_DISPLAY} chars>"
            style = "red" if ev.tool_error else ""
            console.print(f"  {output}", style=style)
        elif ev.kind == "done":
            pass  # handled below

    # Run
    loop = AgenticLoop(delegate=delegate, ctx=loop_ctx, config=loop_config)

    async def _run():
        outcome = await loop.run(on_event=_on_event)
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


def _build_langgraph_on_demand(
    ctx: AppContext,
    mode: str,
    human_approval: bool,
    max_review_iterations: int,
):
    """Build a LangGraph delegate on-the-fly when the ComplexityGate triggers.

    This is called when:
    - The gate auto-selects engine="langgraph"
    - But no delegate was pre-built (because config had engine="native")

    Builds the appropriate graph based on the gate's mode selection.
    """
    from harness.langgraph.delegate import LangGraphDelegate
    from harness.langgraph.graphs import (
        build_pair_coding_graph,
        build_multi_agent_graph,
    )
    from harness.langgraph.checkpointer import create_checkpointer

    checkpointer = create_checkpointer(backend="memory")

    try:
        if mode == "multi_agent":
            graph = build_multi_agent_graph(
                llm=ctx.llm,
                tool_registry=ctx.tool_registry,
                tool_executor=ctx.tool_executor,
                context_gatherer=ctx.context_gatherer,
                checkpointer=checkpointer,
                fan_out_implementers=True,
            )
        else:  # pair_coding or standard
            graph = build_pair_coding_graph(
                llm=ctx.llm,
                checkpointer=checkpointer,
                interrupt_on_approval=False,  # Autonomous mode: no human interrupt
                max_review_iterations=max_review_iterations,
            )

        delegate = LangGraphDelegate(
            graph=graph,
            mode=mode,
            llm=ctx.llm,
            tool_executor=ctx.tool_executor,
            gatherer=ctx.context_gatherer,
        )
        logger.info("LangGraph delegate built on-demand — mode=%s", mode)
        return delegate

    except Exception as exc:
        logger.error("Failed to build LangGraph delegate on-demand: %s", exc)
        console.print(
            f"[yellow]⚠ Could not auto-trigger multi-agent mode: {exc}[/yellow]"
        )
        return None


def _resolve_workspace(workspace: str, ctx: AppContext) -> str:
    """Resolve the workspace root path."""
    if workspace:
        p = Path(workspace)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        return str(p.resolve())
    # Default: restrict to CWD (the harness project root)
    return str(Path(ctx.cwd).resolve())


def _run_langgraph(
    ctx: AppContext,
    delegate,
    prompt_text: str,
    session_id: str,
    workspace_root: str,
    task_logger: TaskLogger,
    debug: bool,
) -> None:
    """Run a LangGraph-powered agent session.

    The LangGraph delegate handles the full graph execution.
    This function provides the CLI-facing wrapper: logging, progress display,
    and human-in-the-loop interrupt handling.
    """
    from harness.core.loop_delegate import LoopContext
    from harness.llm.types import ChatMessage
    from harness.core.loop import LoopEvent, LoopConfig, AgenticLoop

    # Build context
    blocks = ctx.context_gatherer.gather()
    system_prompt = ctx.context_gatherer.to_system_prompt(blocks)
    messages = [ChatMessage.user(prompt_text)]

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
        max_turns=ctx.config.loop.max_turns,
    )

    loop_ctx = LoopContext(
        messages=messages,
        system_prompt=system_prompt,
        tool_registry=ctx.tool_registry,
        llm=ctx.llm,
        cwd=ctx.cwd,
    )

    # Progress callback for real-time display
    def _on_event(ev: LoopEvent) -> None:
        if ev.kind == "thinking":
            console.print("[dim]LangGraph agent thinking...[/dim]")
        elif ev.kind == "tool_call":
            params_str = _format_params(ev.tool_input or {})
            console.print(f"[cyan]→ {ev.tool_name}({params_str})[/cyan]")
        elif ev.kind == "tool_result":
            output = ev.tool_output
            if len(output) > _MAX_TOOL_DISPLAY:
                output = output[:_MAX_TOOL_DISPLAY] + f"\n...<truncated {len(ev.tool_output) - _MAX_TOOL_DISPLAY} chars>"
            style = "red" if ev.tool_error else ""
            console.print(f"  {output}", style=style)
        elif ev.kind == "text":
            # Streaming text chunks
            pass  # Too verbose for CLI; final output shown at end
        elif ev.kind == "done":
            pass  # Handled below

    # Wire progress callback
    delegate._on_event = _on_event
    delegate._session_id = session_id
    delegate._workspace_root = workspace_root

    loop_config = LoopConfig(
        max_turns=ctx.config.loop.max_turns,
        compaction_threshold=ctx.config.loop.compaction_threshold,
    )
    loop = AgenticLoop(delegate=delegate, ctx=loop_ctx, config=loop_config)

    async def _run():
        outcome = await loop.run(on_event=_on_event)

        if outcome.content:
            console.print(Markdown(outcome.content))
        elif outcome.kind == "error":
            console.print(f"[red]Error: {outcome.content}[/red]")

        if debug:
            console.print(
                f"\n[dim]Mode: {delegate.mode}, "
                f"Thread: {delegate.thread_id[:8]}, "
                f"Duration: {outcome.duration_ms:.0f}ms[/dim]"
            )

        task_logger.log_task_end(
            outcome=outcome.kind,
            turns=outcome.turns,
            total_duration_ms=outcome.duration_ms,
            tokens_used=outcome.tokens_used,
            error=outcome.content if outcome.kind == "error" else "",
        )
        task_logger.close()

    asyncio.run(_run())


def _format_params(params: dict[str, Any]) -> str:
    """Format tool params for compact display."""
    if not params:
        return ""
    items = []
    for k, v in params.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        items.append(f"{k}={s}")
    return ", ".join(items)


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
        default=500,
        help="Maximum tool-calling turns (default: 500)",
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
    parser.add_argument(
        "--mode",
        choices=["standard", "pair_coding", "multi_agent"],
        default=None,
        help="Agent collaboration mode (overrides config; requires engine=langgraph)",
    )
    parser.add_argument(
        "--no-approval",
        action="store_true",
        default=False,
        help="Disable human-in-the-loop approval in pair coding mode",
    )
    parser.set_defaults(func=_run_dispatch)
    return parser


def _run_dispatch(args, ctx: AppContext) -> None:
    """Bridge from argparse namespace to handle_run."""
    # Apply mode override from CLI
    if getattr(args, "mode", None) and ctx.config.loop.engine == "langgraph":
        ctx.config.loop.mode = args.mode
        # Re-initialize LangGraph delegate with the new mode
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.langgraph.graphs import (
            build_pair_coding_graph,
            build_multi_agent_graph,
        )
        from harness.langgraph.checkpointer import create_checkpointer

        checkpointer = create_checkpointer(backend="memory")
        if args.mode == "pair_coding":
            graph = build_pair_coding_graph(
                llm=ctx.llm,
                checkpointer=checkpointer,
                interrupt_on_approval=not getattr(args, "no_approval", False),
                max_review_iterations=ctx.config.loop.max_review_iterations,
            )
        elif args.mode == "multi_agent":
            graph = build_multi_agent_graph(
                llm=ctx.llm,
                tool_registry=ctx.tool_registry,
                tool_executor=ctx.tool_executor,
                context_gatherer=ctx.context_gatherer,
                checkpointer=checkpointer,
                fan_out_implementers=True,
            )
        else:
            graph = build_pair_coding_graph(
                llm=ctx.llm,
                checkpointer=checkpointer,
                interrupt_on_approval=False,
                max_review_iterations=1,
            )

        ctx.langgraph_delegate = LangGraphDelegate(
            graph=graph,
            mode=args.mode,
            llm=ctx.llm,
            tool_executor=ctx.tool_executor,
            gatherer=ctx.context_gatherer,
        )

    handle_run(
        ctx=ctx,
        prompt_text=args.text,
        max_turns=args.max_turns,
        debug=args.debug,
        workspace=getattr(args, "workspace", ""),
        mode=getattr(args, "mode", ""),
    )
