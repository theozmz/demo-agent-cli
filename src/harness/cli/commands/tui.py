"""'harness tui' subcommand — full-screen Textual terminal UI."""

from __future__ import annotations

from harness.cli.context import AppContext
from harness.cli.tui.app import run_tui


def add_tui_subparser(subparsers, shared_parent) -> None:
    parser = subparsers.add_parser(
        "tui",
        parents=[shared_parent],
        help="Launch the full-screen Textual TUI",
    )
    parser.set_defaults(func=_tui_dispatch)
    return parser


def _tui_dispatch(args, ctx: AppContext) -> None:
    run_tui(ctx)
