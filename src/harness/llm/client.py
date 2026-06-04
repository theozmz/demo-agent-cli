"""LlmClient ABC — the abstraction all LLM providers implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from harness.llm.types import ChatMessage, LlmResponse


class LlmClient(ABC):
    """Abstract LLM client — domain layer defines, infrastructure layer implements."""

    @abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> LlmResponse:
        """Non-streaming LLM call."""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> AsyncIterator[LlmResponse]:
        """Streaming LLM call."""
        ...

    @abstractmethod
    def estimate_tokens(self, messages: list[ChatMessage]) -> int:
        """Rough token count estimation."""
        ...
