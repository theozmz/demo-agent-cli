"""Node function implementations for LangGraph agent graphs.

Each submodule contains the node functions for a specific graph topology:
- nodes/pair_coding.py: Coder, Reviewer, HumanApproval, Done nodes
- nodes/multi_agent.py: Controller, TaskRouter, Implementer, Reviewers, etc.
"""

from harness.langgraph.nodes.pair_coding import (
    node_coder,
    node_reviewer,
    node_human_approval,
    node_done,
)

from harness.langgraph.nodes.multi_agent import (
    node_controller,
    node_task_router,
    node_implementer,
    node_result_collector,
    node_spec_reviewer,
    node_code_quality_reviewer,
    node_remediation,
    node_finalize,
)

__all__ = [
    # Pair coding
    "node_coder",
    "node_reviewer",
    "node_human_approval",
    "node_done",
    # Multi-agent
    "node_controller",
    "node_task_router",
    "node_implementer",
    "node_result_collector",
    "node_spec_reviewer",
    "node_code_quality_reviewer",
    "node_remediation",
    "node_finalize",
]
