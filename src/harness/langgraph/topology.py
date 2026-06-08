"""Agent topology — visualizes LangGraph multi-agent orchestration at runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentNode:
    """Runtime state for a single agent in the multi-agent topology."""

    name: str
    role: str
    icon: str
    status: str = "pending"  # pending | running | done | failed
    current_task: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


def _fmt_tok(n: int) -> str:
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


# Topology definitions per mode
PAIR_CODING_TOPOLOGY = [
    AgentNode("coder", "Coder", "💻"),
    AgentNode("reviewer", "Reviewer", "🔍"),
    AgentNode("human_approval", "Human Approval", "👤"),
]

MULTI_AGENT_TOPOLOGY = [
    AgentNode("controller", "Controller", "📋"),
    AgentNode("implementer", "Implementer", "💻"),
    AgentNode("spec_reviewer", "Spec Reviewer", "✅"),
    AgentNode("code_quality_reviewer", "Code Reviewer", "🔍"),
    AgentNode("remediation", "Remediation", "🔧"),
    AgentNode("finalize", "Finalize", "🏁"),
]

STANDARD_TOPOLOGY = [
    AgentNode("agent", "Agent", "🤖"),
    AgentNode("reviewer", "Reviewer", "🔍"),
]


class AgentTopology:
    """Tracks and renders multi-agent orchestration state for CLI display.

    Usage in langgraph path::

        topo = AgentTopology.for_mode("multi_agent")
        topo.update("controller", status="running", task="planning...")
        console.print(topo.render())
        topo.add_tokens("controller", in_tok=2100, out_tok=500)
        topo.update("controller", status="done")
        console.print(topo.render())
    """

    def __init__(self, nodes: list[AgentNode], mode: str = ""):
        self._nodes: dict[str, AgentNode] = {n.name: n for n in nodes}
        self._order = [n.name for n in nodes]
        self._mode = mode

    @classmethod
    def for_mode(cls, mode: str) -> "AgentTopology":
        if mode == "multi_agent":
            return cls(MULTI_AGENT_TOPOLOGY, mode)
        elif mode == "pair_coding":
            return cls(PAIR_CODING_TOPOLOGY, mode)
        else:
            return cls(STANDARD_TOPOLOGY, mode)

    def update(self, name: str, **kwargs: Any) -> None:
        """Update an agent node's runtime state."""
        node = self._nodes.get(name)
        if node is None:
            return
        for k, v in kwargs.items():
            if hasattr(node, k):
                setattr(node, k, v)

    def add_tokens(self, name: str, in_tok: int = 0, out_tok: int = 0) -> None:
        """Accumulate token counts for a node."""
        node = self._nodes.get(name)
        if node:
            node.tokens_in += in_tok
            node.tokens_out += out_tok

    def render(self) -> str:
        """Render the current topology state as a string."""
        lines = []
        if self._mode:
            lines.append(f"[bold]Agent Topology ({self._mode})[/bold]")

        for name in self._order:
            node = self._nodes[name]
            status_icon = {
                "pending": "⏳",
                "running": "🔄",
                "done": "✅",
                "failed": "❌",
            }.get(node.status, "  ")

            parts = [f"  {status_icon} {node.icon} {node.role}"]
            if node.current_task:
                parts.append(f"· {node.current_task}")
            if node.status == "running":
                parts.append("[running]")
            elif node.status == "done":
                parts.append("[done]")
            elif node.status == "failed":
                parts.append("[failed]")

            lines.append(" ".join(parts))

            if node.tokens_total > 0:
                lines.append(
                    f"       📊 {_fmt_tok(node.tokens_in)} in / "
                    f"{_fmt_tok(node.tokens_out)} out"
                )

        return "\n".join(lines)

    def render_quick(self) -> str:
        """Compact one-line summary of active agents."""
        active = [n for n in self._nodes.values() if n.status == "running"]
        if not active:
            return ""
        parts = []
        for n in active:
            task = f" ({n.current_task[:30]}...)" if n.current_task else ""
            parts.append(f"{n.icon} {n.role}{task}")
        return " | ".join(parts)
