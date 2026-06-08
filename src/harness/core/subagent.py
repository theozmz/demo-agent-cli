"""Sub-agent dispatch — spawn restricted child agents for focused tasks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from harness.llm.client import LlmClient
from harness.llm.types import ChatMessage, LlmResponse
from harness.tools.tool import Tool, ToolContext, ToolOutput, ToolDomain
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
from harness.core.loop_delegate import LoopContext, LoopOutcome
from harness.core.context import ContextGatherer

if TYPE_CHECKING:
    from harness.cli.status import SessionStatus

logger = logging.getLogger(__name__)

# ---- sub-agent constraint defaults ----

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_TURNS = 50
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_MAX_PER_SESSION = 50

READ_ONLY_TOOLS = frozenset({
    "file_read",
    "glob_search",
    "grep_search",
    "web_fetch",
    "web_search",
})


@dataclass
class SubAgentConfig:
    max_depth: int = DEFAULT_MAX_DEPTH
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    allowed_tools: frozenset | None = None  # None → use READ_ONLY_TOOLS


@dataclass
class SubAgentResult:
    content: str
    turns: int = 0
    duration_ms: float = 0.0
    outcome_kind: str = "completed"


class SubAgentManager:
    """Manages sub-agent lifecycle: dispatch, isolation, result extraction."""

    def __init__(self, config: SubAgentConfig | None = None):
        self._cfg = config or SubAgentConfig()
        self._active: set[str] = set()
        self._total_spawned = 0
        self._status: "SessionStatus | None" = None

    @property
    def total_spawned(self) -> int:
        return self._total_spawned

    def can_spawn(self, parent_depth: int) -> bool:
        if parent_depth >= self._cfg.max_depth:
            logger.warning("Sub-agent denied: max depth %d reached", self._cfg.max_depth)
            return False
        if self._total_spawned >= DEFAULT_MAX_PER_SESSION:
            logger.warning("Sub-agent denied: max per session %d reached", DEFAULT_MAX_PER_SESSION)
            return False
        return True

    async def spawn(
        self,
        task: str,
        *,
        parent_ctx: LoopContext,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        context_gatherer: ContextGatherer,
        llm: LlmClient,
        config_override: SubAgentConfig | None = None,
    ) -> SubAgentResult:
        cfg = config_override or self._cfg
        depth = parent_ctx.subagent_depth + 1

        if not self.can_spawn(parent_ctx.subagent_depth):
            return SubAgentResult(content="Sub-agent spawn denied: depth or session limit reached.", outcome_kind="error")

        self._total_spawned += 1
        tag = f"sub-{self._total_spawned}"
        self._active.add(tag)

        if self._status:
            self._status.subagent_start(tag, task)

        try:
            # Build restricted tool registry (whitelist)
            allowed = cfg.allowed_tools or READ_ONLY_TOOLS
            sub_registry = ToolRegistry()
            for tool in tool_registry.all():
                if tool.name in allowed:
                    sub_registry.register(tool)

            sub_executor = ToolExecutor(
                registry=sub_registry,
                safety=tool_executor._safety if hasattr(tool_executor, "_safety") else None,
            )

            # Build sub-context
            blocks = context_gatherer.gather()
            system_prompt = (
                context_gatherer.to_system_prompt(blocks)
                + "\n\nYou are a SUB-AGENT with restricted capabilities. "
                + "Complete the assigned task and return a concise result. "
                + "Do NOT ask clarifying questions — do your best with available information."
            )

            sub_ctx = LoopContext(
                messages=[ChatMessage.user(task)],
                system_prompt=system_prompt,
                tool_registry=sub_registry,
                llm=llm,
                cwd=parent_ctx.cwd,
                subagent_depth=depth,
            )

            delegate = ChatDelegate(llm=llm, tool_executor=sub_executor, gatherer=context_gatherer)
            loop_config = LoopConfig(max_turns=cfg.max_turns)

            async def _run_sub():
                loop = AgenticLoop(delegate=delegate, ctx=sub_ctx, config=loop_config)
                return await loop.run()

            outcome = await asyncio.wait_for(_run_sub(), timeout=cfg.timeout_seconds)

            if self._status:
                self._status.subagent_end(tag, outcome.kind)

            return SubAgentResult(
                content=outcome.content or "",
                turns=outcome.turns,
                duration_ms=outcome.duration_ms,
                outcome_kind=outcome.kind,
            )

        except asyncio.TimeoutError:
            logger.warning("Sub-agent %s timed out after %ds", tag, cfg.timeout_seconds)
            if self._status:
                self._status.subagent_end(tag, "timeout")
            return SubAgentResult(content="Sub-agent timed out.", outcome_kind="error")
        finally:
            self._active.discard(tag)


# ---- AgentTool — the tool the main agent uses to spawn sub-agents ----


class AgentTool(Tool):
    """Tool that lets the main agent spawn a read-only sub-agent."""

    def __init__(self, manager: SubAgentManager | None = None):
        self._manager = manager or SubAgentManager()
        self._tool_registry: ToolRegistry | None = None
        self._tool_executor: ToolExecutor | None = None
        self._context_gatherer: ContextGatherer | None = None
        self._llm: LlmClient | None = None

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        return (
            "Launch a sub-agent to handle a focused, read-only task. "
            "The sub-agent has access to file_read, glob_search, grep_search, "
            "web_fetch, and web_search. Use this for research, code exploration, "
            "or gathering information before making changes."
        )

    @property
    def domain(self) -> ToolDomain:
        return ToolDomain.ORCHESTRATOR

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the sub-agent to perform. Be specific.",
                },
                "max_turns": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 30,
                    "description": "Maximum tool-calling turns (default: 10)",
                },
            },
            "required": ["task"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def wire(
        self,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        context_gatherer: ContextGatherer,
        llm: LlmClient,
    ) -> None:
        """Wire the infrastructure needed to spawn sub-agents."""
        self._tool_registry = tool_registry
        self._tool_executor = tool_executor
        self._context_gatherer = context_gatherer
        self._llm = llm

    async def execute(self, params: dict, context: ToolContext) -> ToolOutput:
        if not all([self._tool_registry, self._tool_executor, self._context_gatherer, self._llm]):
            return ToolOutput(content="Agent tool not wired — missing infrastructure.", is_error=True)

        task = params.get("task", "")
        max_turns = params.get("max_turns", 10)

        # Build a temporary LoopContext from the ToolContext to carry depth info
        parent_ctx = LoopContext(
            messages=[],
            system_prompt="",
            cwd=context.cwd,
            subagent_depth=context.subagent_depth,
        )

        cfg = SubAgentConfig(max_turns=max_turns)
        result = await self._manager.spawn(
            task=task,
            parent_ctx=parent_ctx,
            tool_registry=self._tool_registry,
            tool_executor=self._tool_executor,
            context_gatherer=self._context_gatherer,
            llm=self._llm,
            config_override=cfg,
        )

        return ToolOutput(
            content=result.content,
            is_error=(result.outcome_kind == "error"),
        )
