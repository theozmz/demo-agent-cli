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


class MockFailingLlmClient:
    """Mock LLM that fails N times before succeeding."""

    def __init__(self, fail_count: int = 0, error_msg: str = "timeout", response_text: str = "ok"):
        self.fail_count = fail_count
        self.error_msg = error_msg
        self.response_text = response_text
        self.call_count = 0

    async def generate(self, messages, tools=None, system_prompt=None, max_tokens=4096, temperature=0.0, **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_count:
            raise RuntimeError(self.error_msg)
        return LlmResponse(
            id="mock-1", text=self.response_text, model="mock",
            usage=LlmUsage(input_tokens=10, output_tokens=5),
        )

    async def stream(self, messages, tools=None, system_prompt=None, **kwargs):
        yield LlmResponse(text=self.response_text)

    def estimate_tokens(self, messages):
        return 100


class TestRetry:
    """Tests for LLM retry behaviour in ChatDelegate."""

    @pytest.fixture
    def registry_and_gatherer(self):
        registry = ToolRegistry()
        gatherer = ContextGatherer(tool_registry=registry)
        return registry, gatherer

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self, registry_and_gatherer):
        """LLM should retry on transient errors and eventually succeed."""
        registry, gatherer = registry_and_gatherer
        llm = MockFailingLlmClient(fail_count=2, error_msg="connection timeout")

        delegate = ChatDelegate(
            llm=llm,
            tool_executor=ToolExecutor(registry=registry),
            gatherer=gatherer,
        )
        blocks = gatherer.gather()
        ctx = LoopContext(
            messages=[ChatMessage.user("test")],
            system_prompt=gatherer.to_system_prompt(blocks),
            tool_registry=registry, llm=llm,
        )

        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=3))
        outcome = await loop.run()

        assert outcome.kind == "completed"
        assert outcome.content == "ok"
        assert llm.call_count == 3  # 2 fails + 1 success

    @pytest.mark.asyncio
    async def test_no_retry_on_permanent_error(self, registry_and_gatherer):
        """LLM should NOT retry on permanent errors (auth, bad request)."""
        registry, gatherer = registry_and_gatherer
        llm = MockFailingLlmClient(fail_count=0, error_msg="authentication failed")

        # Override: first call succeeds but we need a permanent error on first call
        class PermFailLlm:
            def __init__(self):
                self.call_count = 0
            async def generate(self, **kwargs):
                self.call_count += 1
                raise RuntimeError("invalid api key — authentication failed")
            def estimate_tokens(self, messages):
                return 100

        llm2 = PermFailLlm()
        delegate = ChatDelegate(
            llm=llm2,
            tool_executor=ToolExecutor(registry=registry),
            gatherer=gatherer,
        )
        blocks = gatherer.gather()
        ctx = LoopContext(
            messages=[ChatMessage.user("test")],
            system_prompt=gatherer.to_system_prompt(blocks),
            tool_registry=registry, llm=llm2,
        )

        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=3))
        outcome = await loop.run()

        # "authentication" is NOT in _TRANSIENT_PATTERNS
        assert outcome.kind == "error"
        assert "authentication" in (outcome.content or "").lower()
        assert llm2.call_count == 1  # no retries

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, registry_and_gatherer):
        """After 3 persistent transient failures, the loop should return error."""
        registry, gatherer = registry_and_gatherer
        llm = MockFailingLlmClient(fail_count=5, error_msg="server error timeout")

        delegate = ChatDelegate(
            llm=llm,
            tool_executor=ToolExecutor(registry=registry),
            gatherer=gatherer,
        )
        blocks = gatherer.gather()
        ctx = LoopContext(
            messages=[ChatMessage.user("test")],
            system_prompt=gatherer.to_system_prompt(blocks),
            tool_registry=registry, llm=llm,
        )

        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=3))
        outcome = await loop.run()

        assert outcome.kind == "error"
        assert llm.call_count == 3  # _MAX_RETRIES=3, no more

    @pytest.mark.asyncio
    async def test_retry_emits_progress_events(self, registry_and_gatherer):
        """Retry attempts should be reported via the on_event callback."""
        registry, gatherer = registry_and_gatherer
        llm = MockFailingLlmClient(fail_count=1, error_msg="gateway timeout")

        delegate = ChatDelegate(
            llm=llm,
            tool_executor=ToolExecutor(registry=registry),
            gatherer=gatherer,
        )
        blocks = gatherer.gather()
        ctx = LoopContext(
            messages=[ChatMessage.user("test")],
            system_prompt=gatherer.to_system_prompt(blocks),
            tool_registry=registry, llm=llm,
        )

        events = []

        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=3))
        outcome = await loop.run(on_event=events.append)

        retry_events = [e for e in events if e.kind == "retry"]
        assert len(retry_events) == 1
        assert retry_events[0].retry_attempt == 1

        think_events = [e for e in events if e.kind == "thinking"]
        assert len(think_events) >= 1

        assert outcome.kind == "completed"

    @pytest.mark.asyncio
    async def test_progress_events_on_normal_run(self, registry_and_gatherer):
        """Even without retries, progress events should be emitted."""
        registry, gatherer = registry_and_gatherer
        llm = MockLlmClient("Hello!")

        delegate = ChatDelegate(
            llm=llm,
            tool_executor=ToolExecutor(registry=registry),
            gatherer=gatherer,
        )
        blocks = gatherer.gather()
        ctx = LoopContext(
            messages=[ChatMessage.user("hi")],
            system_prompt=gatherer.to_system_prompt(blocks),
            tool_registry=registry, llm=llm,
        )

        events = []
        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=3))
        outcome = await loop.run(on_event=events.append)

        kinds = [e.kind for e in events]
        assert "thinking" in kinds
        assert "done" in kinds
        assert outcome.kind == "completed"
