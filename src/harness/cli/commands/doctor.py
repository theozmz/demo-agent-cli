"""'harness doctor' subcommand — system health check."""

from __future__ import annotations

import logging
import sys

from rich.console import Console

from harness.config.config import Config

console = Console()
logger = logging.getLogger(__name__)


def handle_doctor() -> None:
    """Run the doctor health check.

    Shows config file location, provider / model / api_key status,
    and Python version.  api_key is read exclusively from harness.toml.
    """
    console.print("[bold]Harness Doctor[/bold]\n")

    # ---- config file ----
    config_path = Config._find_config()
    if config_path:
        console.print(f"[green]OK[/green] Config found: {config_path}")
        config = Config.load(config_path)
        console.print(f"  Provider:  {config.llm.provider}")
        console.print(f"  Model:     {config.llm.model}")
        if config.llm.api_key:
            masked = config.llm.api_key[:8] + "..." if len(config.llm.api_key) > 8 else "***"
            console.print(f"  api_key:   {masked}")
        else:
            console.print("  [yellow]api_key:   (not set)[/yellow]")
        if config.llm.api_base:
            console.print(f"  api_base:  {config.llm.api_base}")
    else:
        console.print("[yellow]WARN[/yellow] No harness.toml found (using defaults)")

    # ---- Python version ----
    console.print(f"[green]OK[/green] Python {sys.version}")

    console.print("\n[dim]Run 'harness run \"hello\"' to test the agent.[/dim]")


def add_doctor_subparser(subparsers, shared_parent) -> None:
    """Add the 'doctor' subcommand to an argparse subparsers group."""
    parser = subparsers.add_parser(
        "doctor",
        parents=[shared_parent],
        help="Check system health and configuration",
    )
    parser.set_defaults(func=_doctor_dispatch)
    return parser


def _doctor_dispatch(args, ctx) -> None:
    """Bridge from argparse namespace to handle_doctor."""
    handle_doctor()
