"""LangGraph state definitions — TypedDict states for all agent graphs.

State design follows the pattern from LANGGRAPH.md and DESIGN.md section 3.7.1:
- TypedDict + Annotated[list, add_messages] for message auto-append semantics
- Separation of concerns: iteration (total LLM calls) vs review_iteration (review cycles)
- TaskItem DAG for multi-agent topological scheduling
"""

from __future__ import annotations

from typing import TypedDict, Annotated, Optional, Literal

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ---------------------------------------------------------------------------
# Base agent state — shared across all LangGraph graphs
# ---------------------------------------------------------------------------

class BaseAgentState(TypedDict):
    """Common state shared across all LangGraph agent graphs.

    Uses Annotated[list, add_messages] so node returns are appended
    (not replaced) to the message history.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    iteration: int
    max_iterations: int
    terminal_reason: Optional[str]  # "completed" | "max_turns" | "error" | "approved"
    errors: list[str]
    session_id: str
    thread_id: str


# ---------------------------------------------------------------------------
# Pair Coding State
# ---------------------------------------------------------------------------

class ReviewComment(TypedDict):
    """A single review comment from the reviewer agent."""
    severity: Literal["MUST_FIX", "SUGGESTION"]
    file: str
    line: int
    comment: str


class PairCodingState(BaseAgentState):
    """State for the pair programming (coder + reviewer) graph.

    Tracks the coding task, current code snapshot, review feedback,
    and the review loop iteration count.

    iteration: total LLM calls across ALL nodes (coder + reviewer)
    review_iteration: review-specific cycle counter (for max_review_iterations)
    """
    task: str                          # Original programming task description
    code: str                          # Current code snapshot
    review_comments: list[ReviewComment]
    review_iteration: int              # Review-specific iteration counter
    max_review_iterations: int         # Max review cycles (default: 5)
    final_decision: Optional[Literal["APPROVED", "CHANGES_REQUESTED", "PENDING"]]
    human_approval_required: bool      # Whether to interrupt for human approval


# ---------------------------------------------------------------------------
# Multi-Agent Collaboration State
# ---------------------------------------------------------------------------

class TaskItem(TypedDict):
    """A single task in the controller's task list.

    dependencies: IDs of tasks that must complete before this one.
    Forms a DAG that the task_router resolves via topological sort.
    """
    id: str
    description: str
    dependencies: list[str]            # IDs of tasks that must complete first
    status: Literal["PENDING", "IN_PROGRESS", "DONE", "DONE_WITH_CONCERNS",
                    "NEEDS_CONTEXT", "BLOCKED"]
    assigned_to: str                   # Implementer agent ID
    result: Optional[str]              # Implementation output
    complexity: Literal["simple", "integration", "architecture"]


class ReviewResult(TypedDict):
    """Structured review output from spec or code quality reviewer."""
    passed: bool
    issues: list[str]
    file: str
    line: int


class MultiAgentState(BaseAgentState):
    """State for the Controller-Implementer-Reviewer multi-agent graph.

    Encodes the full collaboration lifecycle:
    1. Controller decomposes plan → task_list
    2. task_router selects next ready task (topological order)
    3. Implementer sub-agents execute individual tasks
    4. Two-stage review: spec compliance → code quality
    5. Remediation loop on review failure
    """
    plan: str                          # The implementation plan / source of truth
    task_list: list[TaskItem]          # Decomposed task list with dependencies
    current_task_index: int            # Index into task_list for current task
    implementation_results: dict[str, str]  # task_id -> result content
    spec_review: Optional[ReviewResult]
    code_quality_review: Optional[ReviewResult]
    review_stage: Literal["spec", "code_quality", "done"]
    final_code: str                    # Accumulated implementation output
    pending_tasks: list[str]           # IDs of tasks ready to execute
    completed_tasks: list[str]         # IDs of completed tasks
