"""Tests for LangGraphDelegate integration with LoopDelegate."""

import pytest
from unittest.mock import MagicMock, AsyncMock


@pytest.fixture
def mock_graph():
    """Create a mock compiled LangGraph graph."""
    graph = MagicMock()
    # Mock astream_events to yield one event then stop
    async def mock_stream(initial_state, config, version="v2"):
        yield {
            "event": "on_chain_start",
            "name": "LangGraph",
            "data": {"input": initial_state},
        }
        yield {
            "event": "on_chain_end",
            "name": "LangGraph",
            "data": {"output": {"terminal_reason": "completed", "code": "print('hello')"}},
        }
    graph.astream_events = mock_stream
    # Mock get_state — next=() means no interrupt pending (graph completed)
    state_mock = MagicMock()
    state_mock.values = {"code": "print('hello')", "terminal_reason": "completed"}
    state_mock.next = ()  # empty = no pending nodes, graph completed
    graph.get_state.return_value = state_mock
    graph.update_state = MagicMock()
    return graph


class TestLangGraphDelegate:
    """Verify LangGraphDelegate implements LoopDelegate correctly."""

    def test_creation(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate

        delegate = LangGraphDelegate(
            graph=mock_graph,
            mode="pair_coding",
        )
        assert delegate.mode == "pair_coding"
        assert delegate.thread_id is not None

    def test_check_signals(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate

        delegate = LangGraphDelegate(graph=mock_graph, mode="pair_coding")
        import asyncio
        signal = asyncio.run(delegate.check_signals())
        from harness.core.loop_delegate import LoopSignal
        assert signal == LoopSignal.NONE

    def test_before_llm_call_returns_none(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext

        delegate = LangGraphDelegate(graph=mock_graph, mode="pair_coding")
        ctx = LoopContext(messages=[], system_prompt="", cwd=".")
        import asyncio
        result = asyncio.run(delegate.before_llm_call(ctx, 1))
        assert result is None

    def test_call_llm_pair_coding(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext
        from harness.llm.types import ChatMessage

        delegate = LangGraphDelegate(
            graph=mock_graph,
            mode="pair_coding",
            session_id="test-session",
            thread_id="test-thread",
        )
        ctx = LoopContext(
            messages=[ChatMessage.user("write a hello world")],
            system_prompt="Test system prompt",
            cwd=".",
        )
        import asyncio
        response = asyncio.run(delegate.call_llm(ctx, 1))
        assert response is not None
        assert response.text is not None
        assert "print" in response.text

    def test_call_llm_multi_agent(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext
        from harness.llm.types import ChatMessage

        delegate = LangGraphDelegate(
            graph=mock_graph,
            mode="multi_agent",
            session_id="test-session",
            thread_id="test-thread",
        )
        ctx = LoopContext(
            messages=[ChatMessage.user("build a TODO app")],
            system_prompt="Test system prompt",
            cwd=".",
        )
        import asyncio
        response = asyncio.run(delegate.call_llm(ctx, 1))
        assert response is not None

    def test_handle_text_response(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext, TextAction

        delegate = LangGraphDelegate(graph=mock_graph, mode="pair_coding")
        ctx = LoopContext(messages=[], system_prompt="", cwd=".")
        import asyncio
        action = asyncio.run(delegate.handle_text_response("some text", ctx))
        assert action == TextAction.RETURN

    def test_execute_tool_calls_returns_none(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext

        delegate = LangGraphDelegate(graph=mock_graph, mode="pair_coding")
        ctx = LoopContext(messages=[], system_prompt="", cwd=".")
        import asyncio
        result = asyncio.run(delegate.execute_tool_calls([], ctx))
        assert result is None

    def test_get_final_state(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate

        delegate = LangGraphDelegate(graph=mock_graph, mode="pair_coding")
        # Run call_llm first to populate _final_state
        from harness.core.loop_delegate import LoopContext
        from harness.llm.types import ChatMessage

        ctx = LoopContext(
            messages=[ChatMessage.user("test")],
            system_prompt="",
            cwd=".",
        )
        import asyncio
        asyncio.run(delegate.call_llm(ctx, 1))

        state = delegate.get_final_state()
        assert isinstance(state, dict)

    def test_build_initial_state_pair_coding(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext
        from harness.llm.types import ChatMessage

        delegate = LangGraphDelegate(graph=mock_graph, mode="pair_coding")
        ctx = LoopContext(
            messages=[ChatMessage.user("test task")],
            system_prompt="",
            cwd=".",
        )
        state = delegate._build_initial_state(ctx, 1)
        assert state["task"] == "test task"
        assert state["code"] == ""
        assert "human_approval_required" in state

    def test_build_initial_state_multi_agent(self, mock_graph):
        from harness.langgraph.delegate import LangGraphDelegate
        from harness.core.loop_delegate import LoopContext
        from harness.llm.types import ChatMessage

        delegate = LangGraphDelegate(graph=mock_graph, mode="multi_agent")
        ctx = LoopContext(
            messages=[ChatMessage.user("build a TODO app")],
            system_prompt="",
            cwd=".",
        )
        state = delegate._build_initial_state(ctx, 1)
        assert state["plan"] == "build a TODO app"
        assert state["task_list"] == []
        assert state["review_stage"] == "spec"
