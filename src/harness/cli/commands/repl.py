"""Deprecated: 'harness repl' — use 'harness' or 'harness tui' instead.

The prompt_toolkit REPL has been replaced by the full-screen Textual TUI.
This module exists as a forwarding shim for backward compatibility.
"""

from __future__ import annotations

import logging
import warnings

from harness.cli.context import AppContext

logger = logging.getLogger(__name__)


def handle_repl(ctx: AppContext, debug: bool) -> None:
    """Deprecated entry point — forwards to the Textual TUI."""
    warnings.warn(
        "The 'repl' command is deprecated. Use 'harness' or 'harness tui' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from harness.cli.tui.app import run_tui
    run_tui(ctx)


def add_repl_subparser(subparsers, shared_parent) -> None:
    """Deprecated subparser — registers 'repl' as alias for 'tui'."""
    parser = subparsers.add_parser(
        "repl",
        parents=[shared_parent],
        help="[DEPRECATED] Start an interactive session (now launches Textual TUI)",
    )
    parser.set_defaults(func=_repl_dispatch)
    return parser


def _repl_dispatch(args, ctx: AppContext) -> None:
    handle_repl(ctx=ctx, debug=args.debug)
