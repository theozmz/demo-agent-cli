"""Tests for the agentic loop."""

import pytest

from harness.llm.types import ChatMessage, LlmResponse, LlmUsage
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig, LoopContext
from harness.core.loop_delegate import LoopOutcome
from harness.core.context import ContextGatherer
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.builtin.file_read import FileReadTool


class MockLlmClient:
    """Mock LLM that returns a fixed text response."""

    def __init__(self, response_text: str = "Hello!"):
        self.response_text = response_text
        self.call_count = 0

    async def generate(self, messages, tools=None, system_prompt=None, max_tokens=4096, temperature=0.0, **kwargs):
        self.call_count += 1
        return LlmResponse(
            id="mock-1",
            text=self.response_text,
            model="mock",
            usage=LlmUsage(input_tokens=10, output_tokens=5),
        )

    async def stream(self, messages, tools=None, system_prompt=None, **kwargs):
        yield LlmResponse(text=self.response_text)

    def estimate_tokens(self, messages):
        return 100


class TestAgenticLoop:
    """Test the core agent loop with mock dependencies."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """Loop should return immediately on text response."""
        llm = MockLlmClient("Hello, world!")
        registry = ToolRegistry()
        registry.register(FileReadTool())
        executor = ToolRegistry()  # minimal — tools not called for text response
        gatherer = ContextGatherer(tool_registry=registry)

        delegate = ChatDelegate(llm=llm, tool_executor=ToolExecutor(registry=registry), gatherer=gatherer)
        blocks = gatherer.gather()
        system_prompt = gatherer.to_system_prompt(blocks)

        ctx = LoopContext(
            messages=[ChatMessage.user("say hello")],
            system_prompt=system_prompt,
            tool_registry=registry,
            llm=llm,
        )

        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=5))
        outcome = await loop.run()

        assert outcome.kind == "completed"
        assert outcome.content == "Hello, world!"
        assert outcome.turns == 1
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_max_turns_reached(self):
        """Loop should stop when max turns is reached (text-only mock, but testing config)."""
        llm = MockLlmClient("Done")
        registry = ToolRegistry()
        gatherer = ContextGatherer(tool_registry=registry)

        delegate = ChatDelegate(llm=llm, tool_executor=ToolExecutor(registry=registry), gatherer=gatherer)
        blocks = gatherer.gather()

        ctx = LoopContext(
            messages=[ChatMessage.user("test")],
            system_prompt=gatherer.to_system_prompt(blocks),
            tool_registry=registry,
            llm=llm,
        )

        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=1))
        outcome = await loop.run()

        # Should complete since MockLlmClient returns text on first call
        assert outcome.kind == "completed"
        assert outcome.turns == 1
