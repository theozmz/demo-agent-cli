"""Core type definitions — ChatMessage, LlmResponse, tool types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SystemPromptPart(Enum):
    STATIC = "static"
    REPO_MAP = "repo_map"
    DYNAMIC = "dynamic"
    MEMORY = "memory"


@dataclass
class ChatMessage:
    """A single chat message in the LLM conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str = ""
    tool_calls: list["ToolCall"] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    @classmethod
    def system(cls, content: str) -> "ChatMessage":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, tool_calls: list["ToolCall"] | None = None) -> "ChatMessage":
        return cls(role="assistant", content=content, tool_calls=tool_calls)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str, name: str = "", is_error: bool = False) -> "ChatMessage":
        prefix = "Error: " if is_error else ""
        return cls(role="user", content=prefix + content, tool_call_id=tool_call_id, name=name)


@dataclass
class ToolCall:
    """An LLM-requested tool invocation."""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class LlmUsage:
    """Token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class LlmResponse:
    """Standardized LLM response — all providers map to this."""

    id: str = ""
    text: str | None = None
    tool_calls: list[ToolCall] | None = None
    stop_reason: str = "end_turn"
    usage: LlmUsage = field(default_factory=LlmUsage)
    model: str = ""
    duration_ms: float = 0.0


@dataclass
class SystemPromptBlock:
    """A segment of the assembled system prompt."""

    kind: SystemPromptPart
    text: str
