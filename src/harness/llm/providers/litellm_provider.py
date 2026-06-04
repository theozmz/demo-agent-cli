"""LiteLLM provider — single integration for all LLM backends."""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Any

from litellm import acompletion

from harness.llm.client import LlmClient
from harness.llm.types import ChatMessage, LlmResponse, LlmUsage, ToolCall

logger = logging.getLogger(__name__)


class LiteLlmProvider(LlmClient):
    """
    Multi-provider LLM client via litellm.

    Model name determines routing:
    - "claude-sonnet-4-6-*" → Anthropic
    - "gpt-4o" → OpenAI
    - "groq/*" → Groq
    etc.

    API keys are read from environment variables (ANTHROPIC_API_KEY,
    OPENAI_API_KEY, etc.) by litellm automatically.
    """

    def __init__(self, model: str = "claude-sonnet-4-6-20250514", api_key: str = "", api_base: str = ""):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base

    async def generate(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> LlmResponse:
        """Non-streaming LLM call via litellm."""
        litellm_messages = self._to_litellm_messages(messages, system_prompt)
        request_kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=litellm_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if tools:
            request_kwargs["tools"] = tools
        if self.api_key:
            request_kwargs["api_key"] = self.api_key
        if self.api_base:
            request_kwargs["api_base"] = self.api_base

        start = time.monotonic()
        try:
            response = await acompletion(**request_kwargs)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

        duration_ms = (time.monotonic() - start) * 1000
        return self._parse_response(response, duration_ms)

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> AsyncIterator[LlmResponse]:
        """Streaming LLM call via litellm."""
        litellm_messages = self._to_litellm_messages(messages, system_prompt)
        request_kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=litellm_messages,
            stream=True,
        )
        if tools:
            request_kwargs["tools"] = tools
        if self.api_key:
            request_kwargs["api_key"] = self.api_key
        if self.api_base:
            request_kwargs["api_base"] = self.api_base

        collected = []
        async for chunk in await acompletion(**request_kwargs):
            text = chunk.choices[0].delta.content if chunk.choices else ""
            if text:
                collected.append(text)
                yield LlmResponse(text=text)

    def estimate_tokens(self, messages: list[ChatMessage]) -> int:
        """Rough token estimation: words × 1.3 + 4 per message."""
        total = 0
        for msg in messages:
            words = len(msg.content.split()) if msg.content else 0
            total += int(words * 1.3) + 4
        return total

    def _to_litellm_messages(self, messages: list[ChatMessage], system_prompt: str | None) -> list[dict]:
        """Convert internal ChatMessage list to litellm dict format."""
        result: list[dict] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            entry: dict = {"role": msg.role, "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.name:
                entry["name"] = msg.name
            result.append(entry)
        return result

    def _parse_response(self, response: Any, duration_ms: float) -> LlmResponse:
        """Parse litellm response into standard LlmResponse."""
        choice = response.choices[0]
        msg = choice.message

        text = msg.content or ""
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=args))

        usage = LlmUsage(
            input_tokens=getattr(response, "usage", None) and response.usage.prompt_tokens or 0,
            output_tokens=getattr(response, "usage", None) and response.usage.completion_tokens or 0,
        )

        return LlmResponse(
            id=getattr(response, "id", ""),
            text=text if not tool_calls else None,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=choice.finish_reason or "end_turn",
            usage=usage,
            model=getattr(response, "model", self.model),
            duration_ms=duration_ms,
        )
