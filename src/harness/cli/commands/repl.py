"""'harness repl' subcommand — interactive prompt_toolkit REPL with subprocess execution."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from rich.console import Console
from rich.markdown import Markdown

from harness.cli.context import AppContext
from harness.llm.types import ChatMessage
from harness.logging.task_logger import TaskLogger

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
    """Interactive REPL — each user input spawns a subprocess ``harness run``.

    Ctrl+C interrupts.  Ctrl+D or ``exit`` / ``quit`` exits.
    """
    console.print(
        "[bold]Harness REPL[/bold] — each task runs as a subprocess. "
        "Ctrl+C to interrupt, Ctrl+D to exit\n"
    )

    # Resolve the harness executable for subprocess spawning
    harness_exe = _find_harness_exe()
    config_arg = _config_arg(ctx)

    if harness_exe is None:
        console.print(
            "[yellow]Warning: harness executable not found on PATH — "
            "falling back to in-process execution.[/yellow]\n"
        )
        _handle_repl_inprocess(ctx, debug)
        return

    console.print(f"[dim]Runner: {harness_exe}[/dim]\n")

    session_id = str(uuid.uuid4())
    prompt_session = PromptSession(
        history=FileHistory(str(_history_path())),
        style=REPL_STYLE,
    )

    asyncio.run(_repl_loop(prompt_session, ctx, harness_exe, config_arg, session_id, debug))


# ---------------------------------------------------------------------------
# Async REPL loop — subprocess mode
# ---------------------------------------------------------------------------
async def _repl_loop(
    prompt_session: PromptSession,
    ctx: AppContext,
    harness_exe: str,
    config_arg: str,
    session_id: str,
    debug: bool,
) -> None:
    """Main async REPL loop — reads input, spawns subprocess, streams output."""

    task_num = 0

    while True:
        # --- read input ---
        try:
            user_input = await prompt_session.prompt_async([("class:prompt", "> ")])
        except KeyboardInterrupt:
            console.print("\n[dim](interrupted)[/dim]")
            continue
        except EOFError:
            console.print("\n[dim]Goodbye.[/dim]")
            return

        text = user_input.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit", "/q", "/exit"):
            console.print("[dim]Goodbye.[/dim]")
            return

        task_num += 1
        task_id = f"{session_id}-{task_num}"

        # --- log task start ---
        repl_logger = TaskLogger(session_id=task_id)
        repl_logger.log_task_start(
            user_prompt=text,
            provider=ctx.config.llm.provider,
            model=ctx.config.llm.model,
            cwd=ctx.cwd,
            max_turns=ctx.config.loop.max_turns,
        )

        # --- build subprocess command ---
        cmd = [harness_exe, "run", text]
        if config_arg:
            cmd = [harness_exe, config_arg, "run", text]
        if debug:
            cmd.extend(["-d"])

        start = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Stream stdout/stderr concurrently
            async def _read_stream(stream, prefix: str):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    if decoded:
                        console.print(f"{prefix}{decoded}")

            _, _ = await asyncio.wait_for(
                asyncio.gather(
                    _read_stream(proc.stdout, ""),
                    _read_stream(proc.stderr, "[red]"),
                ),
                timeout=3600,  # 1-hour hard cap per task
            )
            await proc.wait()

        except asyncio.TimeoutError:
            console.print("[yellow]Task timed out after 5 minutes.[/yellow]")
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            repl_logger.log_task_end(
                outcome="timeout",
                total_duration_ms=(time.monotonic() - start) * 1000,
                error="Task timed out after 5 minutes",
            )
            repl_logger.close()
            continue
        except Exception as exc:
            console.print(f"[red]Subprocess error: {exc}[/red]")
            repl_logger.log_task_end(
                outcome="error",
                total_duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
            repl_logger.close()
            continue
            continue

        duration_ms = (time.monotonic() - start) * 1000
        outcome = "completed" if proc.returncode == 0 else "error"

        repl_logger.log_task_end(
            outcome=outcome,
            turns=task_num,
            total_duration_ms=duration_ms,
        )
        repl_logger.close()

        if debug:
            console.print(f"\n[dim]Exit {proc.returncode} · {duration_ms:.0f}ms[/dim]")


# ---------------------------------------------------------------------------
# In-process fallback (when harness exe not on PATH)
# ---------------------------------------------------------------------------
def _handle_repl_inprocess(ctx: AppContext, debug: bool) -> None:
    """Fallback REPL that runs the agent loop in-process."""
    from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig, LoopEvent
    from harness.core.loop_delegate import LoopContext

    messages: list[ChatMessage] = []

    loop_cfg = LoopConfig(max_turns=ctx.config.loop.max_turns)

    blocks = ctx.context_gatherer.gather()
    system_prompt = ctx.context_gatherer.to_system_prompt(blocks)

    prompt_session = PromptSession(
        history=FileHistory(str(_history_path())),
        style=REPL_STYLE,
    )

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

        session_id = str(uuid.uuid4())
        task_logger = TaskLogger(session_id=session_id)

        # Real-time progress display (with truncation)
        def _on_repl_event(ev: LoopEvent) -> None:
            if ev.kind == "thinking":
                console.print(f"[dim]Turn {ev.iteration} — thinking...[/dim]")
            elif ev.kind == "retry":
                console.print(
                    f"[yellow]Retry {ev.retry_attempt}: "
                    f"{ev.retry_error[:100]}[/yellow]"
                )
            elif ev.kind == "tool_call":
                p = ev.tool_input or {}
                params_str = ", ".join(
                    f"{k}={str(v)[:50]}" for k, v in list(p.items())[:3]
                )
                console.print(f"[cyan]→ {ev.tool_name}({params_str})[/cyan]")
            elif ev.kind == "tool_result":
                output = ev.tool_output
                if len(output) > 400:
                    output = output[:400] + f"\n...<truncated {len(ev.tool_output) - 400} chars>"
                style = "red" if ev.tool_error else ""
                console.print(f"  {output}", style=style)

        delegate = ChatDelegate(
            llm=ctx.llm,
            tool_executor=ctx.tool_executor,
            gatherer=ctx.context_gatherer,
            task_logger=task_logger,
        )
        delegate._session_id = session_id
        delegate._workspace_root = str(Path(ctx.cwd).resolve())

        task_logger.log_task_start(
            user_prompt=text,
            provider=ctx.config.llm.provider,
            model=ctx.config.llm.model,
            cwd=ctx.cwd,
            max_turns=ctx.config.loop.max_turns,
        )

        messages.append(ChatMessage.user(text))

        loop_ctx = LoopContext(
            messages=list(messages),
            system_prompt=system_prompt,
            tool_registry=ctx.tool_registry,
            llm=ctx.llm,
            cwd=ctx.cwd,
        )

        loop = AgenticLoop(delegate=delegate, ctx=loop_ctx, config=loop_cfg)

        start = time.monotonic()
        try:
            outcome = asyncio.run(
                asyncio.wait_for(loop.run(on_event=_on_repl_event), timeout=3600)
            )
        except asyncio.TimeoutError:
            console.print("[yellow]Turn timed out after 1 hour.[/yellow]")
            task_logger.log_task_end(
                outcome="timeout",
                total_duration_ms=(time.monotonic() - start) * 1000,
                error="Turn timed out",
            )
            task_logger.close()
            continue
        except Exception as exc:
            console.print(f"[red]Unexpected error: {exc}[/red]")
            task_logger.log_task_end(
                outcome="error",
                total_duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
            task_logger.close()
            continue

        duration_ms = (time.monotonic() - start) * 1000

        if outcome.content:
            console.print(Markdown(outcome.content))
            messages.append(ChatMessage.assistant(outcome.content))
        elif outcome.kind == "error":
            console.print(f"[red]Error: {outcome.content}[/red]")
        else:
            console.print("[dim](no response)[/dim]")

        task_logger.log_task_end(
            outcome=outcome.kind,
            turns=outcome.turns,
            total_duration_ms=duration_ms,
            tokens_used=outcome.tokens_used,
        )
        task_logger.close()

        if debug:
            console.print(f"\n[dim]T{outcome.turns} · {outcome.duration_ms:.0f}ms[/dim]")

        if len(messages) > 50:
            messages = messages[-40:]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _find_harness_exe() -> str | None:
    """Locate the harness executable on PATH or in the venv."""
    import shutil

    # Prefer harness.exe in the same environment as the running process
    exe_dir = Path(sys.executable).parent
    candidates = [
        exe_dir / "harness.exe",
        exe_dir / "harness",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)

    # Fall back to PATH search
    found = shutil.which("harness")
    return found


def _config_arg(ctx: AppContext) -> str:
    """If a specific config path was used, return the CLI arg to propagate it."""
    # We don't have a reference to the original config path after load,
    # but if the user passed -c, propagate it via environment variable.
    # For simplicity, we use HARNESS_CONFIG env var as a signal.
    import os
    cfg_path = os.environ.get("HARNESS_CONFIG", "")
    if cfg_path:
        return f"-c {cfg_path}"
    return ""


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------
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
