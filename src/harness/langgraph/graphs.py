"""LangGraph graph builders for all agent collaboration modes.

Provides three compiled StateGraph builders:
- build_pair_coding_graph: Coder → Reviewer → HumanApproval loop
- build_multi_agent_graph: Controller → Implementers → Two-Stage Review pipeline

All builders accept infrastructure (LlmClient, ToolExecutor, etc.) via parameters.
Graphs are compiled with checkpointing and optional interrupt points.
"""

from __future__ import annotations

import logging
from typing import Literal, TYPE_CHECKING

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from harness.langgraph.state import (
    PairCodingState,
    MultiAgentState,
)

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from harness.llm.client import LlmClient
    from harness.tools.registry import ToolRegistry
    from harness.tools.executor import ToolExecutor
    from harness.core.context import ContextGatherer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Route functions
# ---------------------------------------------------------------------------


def _route_after_approval(state: PairCodingState) -> Literal["coder", "done"]:
    """Conditional routing after human_approval node.

    APPROVED → done (terminal)
    CHANGES_REQUESTED + iteration < max → coder (continue loop)
    CHANGES_REQUESTED + iteration >= max → done (give up)
    """
    decision = state.get("final_decision", "APPROVED")
    review_iter = state.get("review_iteration", 0)
    max_review = state.get("max_review_iterations", 5)

    if decision == "APPROVED":
        return "done"

    if review_iter >= max_review:
        logger.warning(
            "Max review iterations (%d) reached — terminating loop", max_review
        )
        return "done"

    return "coder"


def _route_next_task(state: MultiAgentState) -> Literal["implementer", "spec_reviewer", "finalize"]:
    """Conditional routing from task_router.

    Has PENDING tasks → implementer
    All tasks done, review not started → spec_reviewer
    Review done → finalize
    """
    review_stage = state.get("review_stage", "spec")
    task_list = state.get("task_list", [])

    # Check if any tasks are still pending or in progress
    pending = [t for t in task_list if t.get("status") in ("PENDING", "IN_PROGRESS")]
    if pending:
        return "implementer"

    # All tasks done — route to review
    if review_stage == "spec":
        return "spec_reviewer"
    elif review_stage == "code_quality":
        return "spec_reviewer"  # spec_reviewer handles both stages
    else:
        return "finalize"


def _route_after_quality_review(state: MultiAgentState) -> Literal["remediation", "finalize"]:
    """Route after code quality review.

    Pass → finalize
    Fail → remediation (new fix tasks → back to implementer)
    """
    quality = state.get("code_quality_review")
    spec = state.get("spec_review")

    if spec and not spec.get("passed", False):
        return "remediation"
    if quality and not quality.get("passed", False):
        return "remediation"
    return "finalize"


# ---------------------------------------------------------------------------
# Graph builders
# ---------------------------------------------------------------------------


def build_pair_coding_graph(
    llm: "LlmClient",
    *,
    checkpointer: "BaseCheckpointSaver | None" = None,
    interrupt_on_approval: bool = True,
    max_review_iterations: int = 5,
    coder_system_prompt: str = "",
    reviewer_system_prompt: str = "",
) -> CompiledStateGraph:
    """Build the Pair Programming StateGraph.

    Graph topology (from LANGGRAPH.md section 2.3):

        ┌──────────┐
        │  coder   │◄──────────────────┐
        └────┬─────┘                   │
             │                         │
        ┌────▼──────┐                  │
        │  reviewer │                  │
        └────┬──────┘                  │
             │                         │
        ┌────▼────────┐                │
        │human_approval│ (interrupt)    │
        └────┬────────┘                │
             │                         │
        ┌────▼──────────┐    CHANGES_REQUESTED
        │ route_decision├───────────────────────┘
        └────┬──────────┘
             │ APPROVED
        ┌────▼─────┐
        │   done   │ → END
        └──────────┘

    Args:
        llm: LLM client for coder and reviewer nodes.
        checkpointer: LangGraph checkpointer for state persistence.
        interrupt_on_approval: If True, pause before human_approval for CLI input.
        max_review_iterations: Max review cycles before forced termination.
        coder_system_prompt: Custom system prompt for the coder agent.
        reviewer_system_prompt: Custom system prompt for the reviewer agent.

    Returns:
        A compiled LangGraph StateGraph.
    """
    from harness.langgraph.nodes.pair_coding import (
        make_coder_node,
        make_reviewer_node,
        make_human_approval_node,
        make_done_node,
    )

    workflow = StateGraph(PairCodingState)

    # Create nodes via factories (closures over llm)
    _coder = make_coder_node(llm, system_prompt=coder_system_prompt)
    _reviewer = make_reviewer_node(llm, system_prompt=reviewer_system_prompt)
    _approval = make_human_approval_node()
    _done = make_done_node()

    # Register nodes
    workflow.add_node("coder", _coder)
    workflow.add_node("reviewer", _reviewer)
    workflow.add_node("human_approval", _approval)
    workflow.add_node("done", _done)

    # Set entry
    workflow.set_entry_point("coder")

    # Edges: coder → reviewer → human_approval
    workflow.add_edge("coder", "reviewer")
    workflow.add_edge("reviewer", "human_approval")

    # Conditional routing from human_approval
    workflow.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {
            "coder": "coder",
            "done": "done",
        },
    )

    workflow.add_edge("done", END)

    # Compile with optional interrupt and checkpointer
    compile_kwargs: dict = {}
    if interrupt_on_approval:
        compile_kwargs["interrupt_before"] = ["human_approval"]
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    logger.info(
        "Pair coding graph built: max_review_iterations=%d, "
        "interrupt_on_approval=%s, checkpointer=%s",
        max_review_iterations, interrupt_on_approval,
        type(checkpointer).__name__ if checkpointer else "none",
    )

    return workflow.compile(**compile_kwargs)


def build_multi_agent_graph(
    llm: "LlmClient",
    tool_registry: "ToolRegistry",
    tool_executor: "ToolExecutor",
    context_gatherer: "ContextGatherer",
    *,
    checkpointer: "BaseCheckpointSaver | None" = None,
    fan_out_implementers: bool = False,
) -> CompiledStateGraph:
    """Build the Multi-Agent Collaboration StateGraph.

    Graph topology (DESIGN.md section 3.13.1):

        controller → task_router ↔ implementer → result_collector
                         ↓ (all tasks done)
                   spec_reviewer → code_quality_reviewer
                         ↓ (fail)              ↓ (pass)
                   remediation → task_router   finalize → END

    Sub-agent organization:
    - Sequential chain (default): one implementer at a time
    - The DAG is encoded in TaskItem.dependencies, resolved by task_router

    Args:
        llm: LLM client (model selection by complexity tier).
        tool_registry: Tool registry for implementer sub-agents.
        tool_executor: Tool executor for implementer sub-agents.
        context_gatherer: Context assembler for implementer sub-agents.
        checkpointer: LangGraph checkpointer for state persistence.
        fan_out_implementers: If True, spawn parallel implementers for independent tasks.

    Returns:
        A compiled LangGraph StateGraph.
    """
    from harness.langgraph.nodes.multi_agent import (
        make_controller_node,
        make_task_router_node,
        make_implementer_node,
        make_result_collector_node,
        make_spec_reviewer_node,
        make_code_quality_reviewer_node,
        make_remediation_node,
        make_finalize_node,
    )

    workflow = StateGraph(MultiAgentState)

    # Create nodes via factories
    _controller = make_controller_node(llm)
    _task_router = make_task_router_node()
    _implementer = make_implementer_node(
        llm, tool_registry, tool_executor, context_gatherer,
        fan_out=fan_out_implementers,
    )
    _result_collector = make_result_collector_node()
    _spec_reviewer = make_spec_reviewer_node(llm, tool_executor)
    _code_quality_reviewer = make_code_quality_reviewer_node(llm, tool_executor)
    _remediation = make_remediation_node()
    _finalize = make_finalize_node()

    # Register nodes
    workflow.add_node("controller", _controller)
    workflow.add_node("task_router", _task_router)
    workflow.add_node("implementer", _implementer)
    workflow.add_node("result_collector", _result_collector)
    workflow.add_node("spec_reviewer", _spec_reviewer)
    workflow.add_node("code_quality_reviewer", _code_quality_reviewer)
    workflow.add_node("remediation", _remediation)
    workflow.add_node("finalize", _finalize)

    # Set entry
    workflow.set_entry_point("controller")

    # Edges
    workflow.add_edge("controller", "task_router")

    workflow.add_conditional_edges(
        "task_router",
        _route_next_task,
        {
            "implementer": "implementer",
            "spec_reviewer": "spec_reviewer",
            "finalize": "finalize",
        },
    )

    workflow.add_edge("implementer", "result_collector")
    workflow.add_edge("result_collector", "task_router")

    workflow.add_edge("spec_reviewer", "code_quality_reviewer")

    workflow.add_conditional_edges(
        "code_quality_reviewer",
        _route_after_quality_review,
        {
            "remediation": "remediation",
            "finalize": "finalize",
        },
    )

    workflow.add_edge("remediation", "task_router")
    workflow.add_edge("finalize", END)

    # Compile
    compile_kwargs: dict = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    logger.info(
        "Multi-agent graph built: fan_out=%s, checkpointer=%s",
        fan_out_implementers,
        type(checkpointer).__name__ if checkpointer else "none",
    )

    return workflow.compile(**compile_kwargs)
