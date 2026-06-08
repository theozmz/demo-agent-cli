"""Main CLI entry point — argparse-based with git-style subcommands."""

from __future__ import annotations

import argparse
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Windows Unicode fix — must run before any output
# ---------------------------------------------------------------------------
os.environ.setdefault("PYTHONUTF8", "1")
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import uuid

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from harness.cli.context import AppContext
from harness.cli.commands.run import add_run_subparser
from harness.cli.commands.doctor import add_doctor_subparser
from harness.cli.commands.repl import add_repl_subparser
from harness.cli.commands.tui import add_tui_subparser
from harness.cli.commands.eval import add_eval_subparser

console = Console()
logger = logging.getLogger(__name__)


def _setup_logging(debug: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _show_banner(ctx: AppContext) -> None:
    """Print a stylish welcome banner with config summary."""
    session_id = str(uuid.uuid4())[:8]

    title = Text("🛠️  H A R N E S S", style="bold cyan")
    subtitle = Text("AI Coding Agent CLI — v0.1.0", style="dim")

    info = Table(show_header=False, box=None, padding=(0, 2))
    info.add_column(style="dim", width=12)
    info.add_column(style="white")
    info.add_row("Workdir:", ctx.cwd)
    info.add_row(
        "Provider:",
        f"{ctx.config.llm.provider} ({ctx.config.llm.model})",
    )
    info.add_row("MaxTurns:", str(ctx.config.loop.max_turns))
    sandbox_info = ctx.config.sandbox.runtime
    info.add_row("Sandbox:", sandbox_info)
    info.add_row("Session:", session_id)

    content = Table.grid(padding=(0, 0))
    content.add_row(title)
    content.add_row(subtitle)
    content.add_row("")
    content.add_row(info)

    panel = Panel(content, border_style="cyan", padding=(1, 2))
    console.print(panel)
    console.print("")


def _shared_flags() -> argparse.ArgumentParser:
    """Parent parser with flags accepted by every subcommand."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "-c", "--config",
        default=None,
        help="Path to harness.toml configuration file",
    )
    parent.add_argument(
        "-d", "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )
    return parent


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with global flags and subcommands."""
    parser = argparse.ArgumentParser(
        prog="harness",
        description="AI coding agent CLI — secure, high-performance, local-first",
    )
    # Global flags (work before the subcommand)
    parser.add_argument("-c", "--config", default=None, help=argparse.SUPPRESS)
    parser.add_argument("-d", "--debug", action="store_true", default=False, help=argparse.SUPPRESS)

    # Subcommands — each inherits shared flags so they also work after
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        help="Available commands",
    )
    # Subcommands are optional — default to REPL when none given

    shared = _shared_flags()
    add_run_subparser(subparsers, shared)
    add_doctor_subparser(subparsers, shared)
    add_repl_subparser(subparsers, shared)
    add_tui_subparser(subparsers, shared)
    add_eval_subparser(subparsers, shared)

    return parser


def main() -> None:
    """Entry point — parse args, initialize, dispatch."""
    parser = build_parser()
    args = parser.parse_args()

    # Setup logging first so --debug is effective during init
    _setup_logging(args.debug)

    # ---- Initialization phase ----
    # Build the AppContext once; every subcommand receives it.
    provider_override = getattr(args, "provider", None)
    model_override = getattr(args, "model", None)
    repomap_override = getattr(args, "repomap", None)
    ctx = AppContext.initialize(
        config_path=args.config,
        provider_override=provider_override,
        model_override=model_override,
        repomap_override=repomap_override,
        debug=args.debug,
    )

    # ---- Interactive banner ----
    is_interactive = args.command in (None, "repl", "tui")
    if is_interactive and sys.stdout.isatty():
        _show_banner(ctx)

    # ---- Dispatch ----
    dispatch_func = getattr(args, "func", None)
    if dispatch_func is None:
        # No subcommand — default to REPL
        from harness.cli.commands.repl import handle_repl

        handle_repl(ctx=ctx, debug=args.debug)
    else:
        dispatch_func(args, ctx)


if __name__ == "__main__":
    main()
