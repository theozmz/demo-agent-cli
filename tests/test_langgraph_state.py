"""Tests for LangGraph state definitions."""

import pytest
from harness.langgraph.state import (
    BaseAgentState,
    PairCodingState,
    MultiAgentState,
    TaskItem,
    ReviewComment,
    ReviewResult,
)


class TestBaseAgentState:
    """Verify the base state structure."""

    def test_default_values(self):
        state: BaseAgentState = {
            "messages": [],
            "iteration": 0,
            "max_iterations": 30,
            "terminal_reason": None,
            "errors": [],
            "session_id": "",
            "thread_id": "",
        }
        assert state["iteration"] == 0
        assert state["max_iterations"] == 30
        assert state["terminal_reason"] is None


class TestPairCodingState:
    """Verify pair coding state fields."""

    def test_minimal_state(self):
        state: PairCodingState = {
            "messages": [],
            "iteration": 0,
            "max_iterations": 30,
            "terminal_reason": None,
            "errors": [],
            "session_id": "s1",
            "thread_id": "t1",
            "task": "write a hello world",
            "code": "",
            "review_comments": [],
            "review_iteration": 0,
            "max_review_iterations": 5,
            "final_decision": None,
            "human_approval_required": True,
        }
        assert state["task"] == "write a hello world"
        assert state["code"] == ""
        assert state["max_review_iterations"] == 5
        assert state["human_approval_required"] is True

    def test_review_comment_structure(self):
        comment: ReviewComment = {
            "severity": "MUST_FIX",
            "file": "main.py",
            "line": 42,
            "comment": "Use a context manager for file handling",
        }
        assert comment["severity"] == "MUST_FIX"
        assert comment["line"] == 42


class TestMultiAgentState:
    """Verify multi-agent collaboration state."""

    def test_task_item_structure(self):
        task: TaskItem = {
            "id": "task-1",
            "description": "Add user authentication",
            "dependencies": [],
            "status": "PENDING",
            "assigned_to": "",
            "result": None,
            "complexity": "architecture",
        }
        assert task["id"] == "task-1"
        assert task["complexity"] == "architecture"
        assert task["dependencies"] == []

    def test_task_with_dependencies(self):
        task: TaskItem = {
            "id": "task-2",
            "description": "Add login endpoint",
            "dependencies": ["task-1"],
            "status": "PENDING",
            "assigned_to": "",
            "result": None,
            "complexity": "integration",
        }
        assert "task-1" in task["dependencies"]

    def test_review_result_structure(self):
        review: ReviewResult = {
            "passed": True,
            "issues": [],
            "file": "auth.py",
            "line": 0,
        }
        assert review["passed"] is True

    def test_review_result_with_issues(self):
        review: ReviewResult = {
            "passed": False,
            "issues": ["Missing password hashing", "No rate limiting"],
            "file": "auth.py",
            "line": 15,
        }
        assert len(review["issues"]) == 2

    def test_minimal_state(self):
        state: MultiAgentState = {
            "messages": [],
            "iteration": 0,
            "max_iterations": 30,
            "terminal_reason": None,
            "errors": [],
            "session_id": "s1",
            "thread_id": "t1",
            "plan": "Build a TODO app",
            "task_list": [],
            "current_task_index": 0,
            "implementation_results": {},
            "spec_review": None,
            "code_quality_review": None,
            "review_stage": "spec",
            "final_code": "",
            "pending_tasks": [],
            "completed_tasks": [],
        }
        assert state["plan"] == "Build a TODO app"
        assert state["review_stage"] == "spec"
