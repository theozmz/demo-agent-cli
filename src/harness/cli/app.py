"""CLI entry point — typer app with 'prompt' command."""

from __future__ import annotations

import asyncio
import logging
import sys
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown

# Fix Windows Unicode rendering
os.environ.setdefault("PYTHONUTF8", "1")
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from harness.config.config import Config
from harness.llm.providers.litellm_provider import LiteLlmProvider
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.builtin.file_read import FileReadTool
from harness.tools.builtin.file_write import FileWriteTool
from harness.tools.builtin.glob_search import GlobSearchTool
from harness.safety.pipeline import SafetyLayer
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig, LoopContext
from harness.core.context import ContextGatherer
from harness.llm.types import ChatMessage

app = typer.Typer(
    name="harness",
    help="AI coding agent CLI — secure, high-performance, local-first",
)
console = Console()
logger = logging.getLogger(__name__)


def _build_tool_registry() -> ToolRegistry:
    """Create and populate the tool registry with built-in tools."""
    registry = ToolRegistry()
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(GlobSearchTool())
    return registry


def _setup_logging(debug: bool = False):
    """Configure logging."""
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


@app.command()
def prompt(
    text: str = typer.Argument(..., help="The prompt to send to the agent"),
    model: str = typer.Option("", help="Override the configured model"),
    max_turns: int = typer.Option(30, help="Maximum tool-calling turns"),
    config_path: str = typer.Option("", help="Path to harness.toml"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging"),
):
    """Send a one-shot prompt to the agent."""
    _setup_logging(debug)

    # Load config
    path = config_path or None
    config = Config.load(path)
    if model:
        config.llm.model = model

    # Build infrastructure
    llm = LiteLlmProvider(
        model=config.llm.model,
        api_key=config.llm.api_key,
        api_base=config.llm.api_base,
    )
    registry = _build_tool_registry()
    safety = SafetyLayer()
    executor = ToolExecutor(registry=registry, safety=safety)
    gatherer = ContextGatherer(tool_registry=registry, cwd=str(Path.cwd()))

    # Build loop
    delegate = ChatDelegate(llm=llm, tool_executor=executor, gatherer=gatherer)
    loop_config = LoopConfig(
        max_turns=max_turns or config.loop.max_turns,
        compaction_threshold=config.loop.compaction_threshold,
    )

    # Assemble context
    blocks = gatherer.gather()
    system_prompt = gatherer.to_system_prompt(blocks)
    messages = [ChatMessage.user(text)]

    ctx = LoopContext(
        messages=messages,
        system_prompt=system_prompt,
        tool_registry=registry,
        llm=llm,
        cwd=str(Path.cwd()),
    )

    # Run
    loop = AgenticLoop(delegate=delegate, ctx=ctx, config=loop_config)

    async def _run():
        outcome = await loop.run()
        if outcome.content:
            console.print(Markdown(outcome.content))
        elif outcome.kind == "error":
            console.print(f"[red]Error: {outcome.content}[/red]")
        if debug:
            console.print(f"\n[dim]Turns: {outcome.turns}, Duration: {outcome.duration_ms:.0f}ms[/dim]")

    asyncio.run(_run())


@app.command()
def doctor():
    """Check system health and configuration."""
    console.print("[bold]Harness Doctor[/bold]\n")

    # Check config
    config_path = Config._find_config()
    if config_path:
        console.print(f"[green]OK[/green] Config found: {config_path}")
        config = Config.load(config_path)
        console.print(f"  Model: {config.llm.model}")
        console.print(f"  Provider: {config.llm.provider}")
    else:
        console.print("[yellow]WARN[/yellow] No harness.toml found (using defaults)")

    # Check API keys
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[green]OK[/green] ANTHROPIC_API_KEY set")
    else:
        console.print("[yellow]WARN[/yellow] ANTHROPIC_API_KEY not set")

    if os.environ.get("OPENAI_API_KEY"):
        console.print("[green]OK[/green] OPENAI_API_KEY set")
    else:
        console.print("[dim]  OPENAI_API_KEY not set[/dim]")

    # Check Python
    console.print(f"[green]OK[/green] Python {sys.version}")

    console.print("\n[dim]Run 'harness prompt \"hello\"' to test the agent.[/dim]")


def main():
    """Entry point for console_scripts."""
    app()
