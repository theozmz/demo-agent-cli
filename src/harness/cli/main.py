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

from harness.cli.context import AppContext
from harness.cli.commands.run import add_run_subparser
from harness.cli.commands.doctor import add_doctor_subparser

logger = logging.getLogger(__name__)


def _setup_logging(debug: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


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
    subparsers.required = True

    shared = _shared_flags()
    add_run_subparser(subparsers, shared)
    add_doctor_subparser(subparsers, shared)

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
    ctx = AppContext.initialize(
        config_path=args.config,
        provider_override=provider_override,
        model_override=model_override,
        debug=args.debug,
    )

    # ---- Dispatch ----
    dispatch_func = getattr(args, "func", None)
    if dispatch_func is None:
        parser.print_help()
        sys.exit(1)

    dispatch_func(args, ctx)


if __name__ == "__main__":
    main()
