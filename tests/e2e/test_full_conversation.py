"""E2E: Full conversation — text response + tool calls + max_turns."""

from unittest.mock import AsyncMock

from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage, LlmResponse, LlmUsage, ToolCall


class TestFullConversation:
    async def test_text_only_response(self, test_rig):
        llm = AsyncMock()
        llm.generate.return_value = LlmResponse(text="Hello, world!", usage=LlmUsage())
        llm.estimate_tokens = lambda msgs: 10

        delegate = ChatDelegate(llm=llm, tool_executor=test_rig.executor, gatherer=test_rig.gatherer)
        ctx = LoopContext(
            messages=[ChatMessage.user("hello")],
            system_prompt="You are a helpful assistant.",
            tool_registry=test_rig.registry,
            llm=llm,
            cwd=test_rig.cwd,
        )
        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=5))
        outcome = await loop.run()
        assert outcome.kind == "completed"
        assert "Hello" in outcome.content

    async def test_tool_then_text(self, test_rig):
        responses = [
            LlmResponse(
                tool_calls=[ToolCall(id="c1", name="glob_search", input={"pattern": "*.py"})],
                usage=LlmUsage(),
            ),
            LlmResponse(text="Found files.", usage=LlmUsage()),
        ]
        llm = AsyncMock()
        llm.generate.side_effect = responses
        llm.estimate_tokens = lambda msgs: 10

        delegate = ChatDelegate(llm=llm, tool_executor=test_rig.executor, gatherer=test_rig.gatherer)
        ctx = LoopContext(
            messages=[ChatMessage.user("find py files")],
            system_prompt="You have tools.",
            tool_registry=test_rig.registry,
            llm=llm,
            cwd=test_rig.cwd,
        )
        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=5))
        outcome = await loop.run()
        assert outcome.kind == "completed"

    async def test_max_turns(self, test_rig):
        loopback = LlmResponse(
            tool_calls=[ToolCall(id="x", name="glob_search", input={"pattern": "*"})],
            usage=LlmUsage(),
        )
        llm = AsyncMock()
        llm.generate.return_value = loopback
        llm.estimate_tokens = lambda msgs: 10

        delegate = ChatDelegate(llm=llm, tool_executor=test_rig.executor, gatherer=test_rig.gatherer)
        ctx = LoopContext(
            messages=[ChatMessage.user("do something")],
            system_prompt="Tools.",
            tool_registry=test_rig.registry,
            llm=llm,
            cwd=test_rig.cwd,
        )
        loop = AgenticLoop(delegate=delegate, ctx=ctx, config=LoopConfig(max_turns=3))
        outcome = await loop.run()
        assert outcome.kind == "max_turns"
