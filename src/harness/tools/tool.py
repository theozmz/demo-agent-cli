"""Tool ABC — the base contract for all tools (built-in, MCP, plugin)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from harness.logging.task_logger import TaskLogger


class ToolDomain(Enum):
    ORCHESTRATOR = "orchestrator"  # In-process (Python)
    CONTAINER = "container"         # Docker sandbox


class ApprovalRequirement(Enum):
    NEVER = "never"
    UNLESS_AUTO = "unless_auto"
    ALWAYS = "always"


@dataclass
class ToolOutput:
    """Result of a tool execution."""

    content: str
    is_error: bool = False
    risk_level: str = "low"
    duration_ms: float = 0.0
    truncated: bool = False


@dataclass
class ToolContext:
    """Context passed to every tool.execute() call."""

    cwd: str = ""
    session_id: str = ""
    turn_id: str = ""
    workspace_root: str = ""
    task_logger: "TaskLogger | None" = None


class Tool(ABC):
    """
    Abstract base for all tools.

    Subclasses must provide: name, description, input_schema, execute().
    """

    name: str = ""
    description: str = ""
    domain: ToolDomain = ToolDomain.ORCHESTRATOR
    timeout_seconds: int = 30
    sensitive_params: set[str] = set()

    # ------------------------------------------------------------------
    # Path resolution with workspace boundary enforcement
    # ------------------------------------------------------------------

    def _resolve_path(self, path_str: str, ctx: ToolContext) -> Path:
        """Resolve a file path and enforce workspace boundary.

        1. Relative paths are resolved against *ctx.cwd*.
        2. If *ctx.workspace_root* is set, the resolved path must be
           inside it — otherwise a ``ValueError`` is raised.
        """
        p = Path(path_str)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        resolved = p.resolve()

        if ctx.workspace_root:
            ws = Path(ctx.workspace_root).resolve()
            try:
                resolved.relative_to(ws)
            except ValueError:
                raise ValueError(
                    f"Path '{resolved}' is outside workspace '{ws}'. "
                    f"Access denied."
                )

        return resolved

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema for the tool's parameters."""
        ...

    @abstractmethod
    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        """Execute the tool with the given parameters."""
        ...

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        """Default: NEVER for read-only tools, UNLESS_AUTO otherwise."""
        return ApprovalRequirement.NEVER if self.is_read_only else ApprovalRequirement.UNLESS_AUTO

    @property
    def is_read_only(self) -> bool:
        return False

    def is_enabled(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False
