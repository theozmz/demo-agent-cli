"""Tool ABC — the base contract for all tools (built-in, MCP, plugin)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


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
