"""Tests for LangGraph graph compilation and routing.

These tests verify that graphs can be compiled successfully without
actually invoking LLMs. They use the MemorySaver checkpointer.
"""

import pytest


@pytest.fixture
def mock_llm():
    """Create a mock LLM client for testing graph compilation."""
    from unittest.mock import MagicMock
    llm = MagicMock()
    llm.model = "test-model"
    llm.provider = "test"
    llm.generate.return_value = MagicMock(text="test output", tool_calls=None, usage=None)
    return llm


@pytest.fixture
def mock_tool_registry():
    """Create a mock tool registry."""
    from unittest.mock import MagicMock
    registry = MagicMock()
    registry.get_schemas.return_value = []
    registry.all.return_value = []
    return registry


@pytest.fixture
def mock_tool_executor():
    """Create a mock tool executor."""
    from unittest.mock import MagicMock
    executor = MagicMock()
    executor.execute.return_value = MagicMock(content="tool output", is_error=False)
    return executor


@pytest.fixture
def mock_context_gatherer():
    """Create a mock context gatherer."""
    from unittest.mock import MagicMock
    gatherer = MagicMock()
    gatherer.gather.return_value = []
    gatherer.to_system_prompt.return_value = "System prompt"
    return gatherer


class TestPairCodingGraph:
    """Verify pair coding graph compilation and structure."""

    def test_graph_compiles(self, mock_llm):
        """The pair coding graph should compile without errors."""
        from harness.langgraph.graphs import build_pair_coding_graph

        graph = build_pair_coding_graph(
            llm=mock_llm,
            checkpointer=None,
            interrupt_on_approval=False,
            max_review_iterations=3,
        )
        assert graph is not None

    def test_graph_with_interrupt(self, mock_llm):
        """The graph should accept interrupt_on_approval=True."""
        from harness.langgraph.graphs import build_pair_coding_graph

        graph = build_pair_coding_graph(
            llm=mock_llm,
            checkpointer=None,
            interrupt_on_approval=True,
        )
        assert graph is not None

    def test_graph_with_memory_checkpointer(self, mock_llm):
        """The graph should compile with MemorySaver."""
        from harness.langgraph.graphs import build_pair_coding_graph
        from harness.langgraph.checkpointer import create_checkpointer

        cp = create_checkpointer(backend="memory")
        graph = build_pair_coding_graph(
            llm=mock_llm,
            checkpointer=cp,
        )
        assert graph is not None


class TestMultiAgentGraph:
    """Verify multi-agent collaboration graph compilation."""

    def test_graph_compiles(
        self, mock_llm, mock_tool_registry, mock_tool_executor, mock_context_gatherer
    ):
        """The multi-agent graph should compile without errors."""
        from harness.langgraph.graphs import build_multi_agent_graph

        graph = build_multi_agent_graph(
            llm=mock_llm,
            tool_registry=mock_tool_registry,
            tool_executor=mock_tool_executor,
            context_gatherer=mock_context_gatherer,
            checkpointer=None,
        )
        assert graph is not None

    def test_graph_with_fan_out(
        self, mock_llm, mock_tool_registry, mock_tool_executor, mock_context_gatherer
    ):
        """The graph should compile with fan_out_implementers=True."""
        from harness.langgraph.graphs import build_multi_agent_graph

        graph = build_multi_agent_graph(
            llm=mock_llm,
            tool_registry=mock_tool_registry,
            tool_executor=mock_tool_executor,
            context_gatherer=mock_context_gatherer,
            fan_out_implementers=True,
        )
        assert graph is not None


class TestRouteFunctions:
    """Verify conditional routing logic."""

    def test_route_after_approval_approved(self):
        """APPROVED should route to 'done'."""
        from harness.langgraph.graphs import _route_after_approval

        state = {
            "final_decision": "APPROVED",
            "review_iteration": 2,
            "max_review_iterations": 5,
        }
        assert _route_after_approval(state) == "done"

    def test_route_after_approval_changes_requested(self):
        """CHANGES_REQUESTED with remaining iterations should route to 'coder'."""
        from harness.langgraph.graphs import _route_after_approval

        state = {
            "final_decision": "CHANGES_REQUESTED",
            "review_iteration": 2,
            "max_review_iterations": 5,
        }
        assert _route_after_approval(state) == "coder"

    def test_route_after_approval_max_iterations(self):
        """CHANGES_REQUESTED at max iterations should route to 'done'."""
        from harness.langgraph.graphs import _route_after_approval

        state = {
            "final_decision": "CHANGES_REQUESTED",
            "review_iteration": 5,
            "max_review_iterations": 5,
        }
        assert _route_after_approval(state) == "done"

    def test_route_next_task_has_pending(self):
        """Should route to implementer when tasks are PENDING."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "review_stage": "spec",
            "task_list": [
                {"id": "t1", "status": "PENDING", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "implementer"

    def test_route_next_task_all_done_spec_review(self):
        """Should route to spec_reviewer when all tasks done and review not started."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "review_stage": "spec",
            "task_list": [
                {"id": "t1", "status": "DONE", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "spec_reviewer"

    def test_route_next_task_review_done(self):
        """Should route to finalize when review is done."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "review_stage": "done",
            "task_list": [
                {"id": "t1", "status": "DONE", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "finalize"

    def test_route_after_quality_pass(self):
        """Passed review should route to finalize."""
        from harness.langgraph.graphs import _route_after_quality_review

        state = {
            "spec_review": {"passed": True, "issues": [], "file": "", "line": 0},
            "code_quality_review": {"passed": True, "issues": [], "file": "", "line": 0},
        }
        assert _route_after_quality_review(state) == "finalize"

    def test_route_after_quality_fail(self):
        """Failed review should route to remediation."""
        from harness.langgraph.graphs import _route_after_quality_review

        state = {
            "spec_review": {"passed": False, "issues": ["missing feature X"], "file": "a.py", "line": 1},
            "code_quality_review": {"passed": True, "issues": [], "file": "", "line": 0},
        }
        assert _route_after_quality_review(state) == "remediation"

    def test_route_after_quality_both_fail(self):
        """Both spec and code quality reviews fail → remediation."""
        from harness.langgraph.graphs import _route_after_quality_review

        state = {
            "spec_review": {"passed": False, "issues": ["spec issue"], "file": "a.py", "line": 1},
            "code_quality_review": {"passed": False, "issues": ["quality issue"], "file": "a.py", "line": 10},
        }
        assert _route_after_quality_review(state) == "remediation"

    def test_route_after_quality_code_quality_fail(self):
        """Only code quality fails → remediation."""
        from harness.langgraph.graphs import _route_after_quality_review

        state = {
            "spec_review": {"passed": True, "issues": [], "file": "", "line": 0},
            "code_quality_review": {"passed": False, "issues": ["needs refactoring"], "file": "x.py", "line": 5},
        }
        assert _route_after_quality_review(state) == "remediation"

    def test_route_next_task_code_quality_stage(self):
        """review_stage='code_quality' with no pending tasks → code_quality_reviewer."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "review_stage": "code_quality",
            "task_list": [
                {"id": "t1", "status": "DONE", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "code_quality_reviewer"

    def test_route_next_task_blocked_terminal(self):
        """terminal_reason='blocked' → finalize (error terminal)."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "terminal_reason": "blocked",
            "review_stage": "spec",
            "task_list": [
                {"id": "t1", "status": "BLOCKED", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "finalize"

    def test_route_next_task_in_progress(self):
        """Tasks IN_PROGRESS → route to implementer to continue work."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "review_stage": "spec",
            "task_list": [
                {"id": "t1", "status": "IN_PROGRESS", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "implementer"

    def test_route_next_task_done_with_concerns_no_pending(self):
        """All tasks DONE_WITH_CONCERNS, review_stage=done → finalize."""
        from harness.langgraph.graphs import _route_next_task

        state = {
            "review_stage": "done",
            "task_list": [
                {"id": "t1", "status": "DONE_WITH_CONCERNS", "dependencies": []},
            ],
        }
        assert _route_next_task(state) == "finalize"
