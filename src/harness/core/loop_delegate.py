"""LoopDelegate ABC — strategy interface for the agent loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class LoopSignal(Enum):
    NONE = "none"
    STOP = "stop"
    INJECT_MESSAGE = "inject_message"


class TextAction(Enum):
    RETURN = "return"
    CONTINUE = "continue"


@dataclass
class LoopOutcome:
    """Result of an agent loop execution."""

    kind: str  # "completed" | "max_turns" | "stopped" | "error"
    content: str | None = None
    tokens_used: int = 0
    duration_ms: float = 0.0
    turns: int = 0


@dataclass
class LoopContext:
    """Aggregate context for one agent loop invocation."""

    messages: list = field(default_factory=list)
    system_prompt: str = ""
    tool_registry: "ToolRegistry | None" = None  # type: ignore[name-defined]
    llm: "LlmClient | None" = None  # type: ignore[name-defined]
    cwd: str = ""
    iteration: int = 0
    subagent_depth: int = 0
    force_text: bool = False


class LoopDelegate(ABC):
    """Strategy interface decoupling agent loop from consumer types."""

    @abstractmethod
    async def check_signals(self) -> LoopSignal:
        ...

    @abstractmethod
    async def before_llm_call(self, ctx: LoopContext, iteration: int) -> LoopOutcome | None:
        ...

    @abstractmethod
    async def call_llm(self, ctx: LoopContext, iteration: int) -> "LlmResponse":  # type: ignore[name-defined]
        ...

    @abstractmethod
    async def handle_text_response(self, text: str, ctx: LoopContext) -> TextAction:
        ...

    @abstractmethod
    async def execute_tool_calls(self, tool_calls: list, ctx: LoopContext) -> LoopOutcome | None:
        ...

    @abstractmethod
    async def after_iteration(self, iteration: int, ctx: LoopContext):
        ...

    def wire_progress(
        self,
        on_event: "Callable[[LoopEvent], None] | None" = None,  # type: ignore[name-defined]
        token_counter: "TokenCounter | None" = None,  # type: ignore[name-defined]
        status: "SessionStatus | None" = None,  # type: ignore[name-defined]
    ) -> None:
        """Wire progress-callbacks and session state into the delegate.

        Called by AgenticLoop before the main loop. Default no-op;
        concrete delegates override to receive callbacks and state.
        """
