"""LangGraph multi-agent module for Harness CLI.

Provides StateGraph-based agent loops for:
- Standard agent loop (replaces AgenticLoop when engine="langgraph")
- Pair programming (coder + reviewer + human-in-the-loop)
- Multi-agent collaboration (controller + implementers + reviewers)
"""

from harness.langgraph.state import (
    BaseAgentState,
    PairCodingState,
    MultiAgentState,
    TaskItem,
    ReviewComment,
    ReviewResult,
)
from harness.langgraph.graphs import (
    build_pair_coding_graph,
    build_multi_agent_graph,
)
from harness.langgraph.delegate import LangGraphDelegate
from harness.langgraph.checkpointer import create_checkpointer
from harness.langgraph.complexity import ComplexityAssessor, ComplexityTier
from harness.langgraph.router import LangGraphModelRouter
from harness.langgraph.gate import ComplexityGate, ModeSelection, create_complexity_gate

__all__ = [
    # State
    "BaseAgentState",
    "PairCodingState",
    "MultiAgentState",
    "TaskItem",
    "ReviewComment",
    "ReviewResult",
    # Graphs
    "build_pair_coding_graph",
    "build_multi_agent_graph",
    # Delegate
    "LangGraphDelegate",
    # Infrastructure
    "create_checkpointer",
    "ComplexityAssessor",
    "ComplexityTier",
    "LangGraphModelRouter",
    # Autonomous gate
    "ComplexityGate",
    "ModeSelection",
    "create_complexity_gate",
]
