"""LangGraph-native sub-agent spawning for multi-agent collaboration.

Extends the existing SubAgentManager with:
- Write-capable implementer agents (unlike existing read-only AgentTool)
- Model override per sub-agent based on task complexity
- Structured output extraction (implementer report protocol)
- Curated context injection (plan excerpt + task only, no session history)

Child Agent Organization Patterns:
- Sequential chain: one implementer at a time (default, avoids Git conflicts)
- Parallel fan-out: asyncio.gather() for independent tasks
- Tree (nested): implementer's AgentTool spawns child sub-agents (depth ≤ 2)
- DAG: TaskItem.dependencies resolved by topological sort in task_router
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness.core.subagent import (
    SubAgentManager,
    SubAgentConfig,
    SubAgentResult,
    READ_ONLY_TOOLS,
)

if TYPE_CHECKING:
    from harness.llm.client import LlmClient
    from harness.tools.registry import ToolRegistry
    from harness.tools.executor import ToolExecutor
    from harness.core.context import ContextGatherer
    from harness.core.loop_delegate import LoopContext
    from harness.langgraph.state import TaskItem

logger = logging.getLogger(__name__)

# Tools allowed for write-capable implementer agents
WRITE_TOOLS: frozenset[str] = frozenset({
    "file_write",
    "file_edit",
    "bash_exec",
})

# Full tool set for implementers: read + write
IMPLEMENTER_TOOLS: frozenset[str] = READ_ONLY_TOOLS | WRITE_TOOLS


@dataclass
class LangGraphSubAgentConfig(SubAgentConfig):
    """Extended sub-agent config for LangGraph multi-agent scenarios.

    Adds:
    - model: override the model per sub-agent
    - write_access: allow file writes in the sub-agent
    - context_brief: curated context from controller (plan excerpt + task only)
    """
    model: str = ""
    write_access: bool = False
    context_brief: str = ""


class LangGraphSubAgentManager(SubAgentManager):
    """Extended sub-agent manager for LangGraph multi-agent collaboration.

    Unlike the base SubAgentManager (which only spawns read-only sub-agents
    via AgentTool), this manager supports write-capable implementer agents
    with model selection based on task complexity.

    Key differences from base SubAgentManager:
    - Allows write tools (file_write, file_edit, bash_exec)
    - Supports model override per sub-agent
    - Injects curated context (plan excerpt + task description)
    - Parses implementer report protocol (STATUS: DONE/etc.)
    """

    def __init__(self, config: LangGraphSubAgentConfig | None = None):
        super().__init__(config=config)

    # ------------------------------------------------------------------
    # Spawn implementer
    # ------------------------------------------------------------------

    async def spawn_implementer(
        self,
        task: "TaskItem",
        plan: str,
        *,
        parent_ctx: "LoopContext",
        llm: "LlmClient",
        model: str,
        tool_registry: "ToolRegistry",
        tool_executor: "ToolExecutor",
        context_gatherer: "ContextGatherer",
    ) -> SubAgentResult:
        """Spawn an implementer sub-agent with write access and curated context.

        The implementer receives:
        - Only the plan excerpt and task description (context isolation)
        - Write tool access for making code changes
        - An appropriate model based on task complexity
        - The implementer report protocol instruction

        Args:
            task: The TaskItem to execute.
            plan: The full implementation plan (for context).
            parent_ctx: The parent agent's LoopContext.
            llm: The base LLM client (model will be overridden).
            model: The concrete model name to use for this implementer.
            tool_registry: The full tool registry (will be filtered).
            tool_executor: The tool executor.
            context_gatherer: The context assembler.

        Returns:
            SubAgentResult with the implementer's output.
        """
        task_id = task.get("id", "unknown")
        description = task.get("description", "")

        # Build filtered tool registry for implementer
        from harness.tools.registry import ToolRegistry

        sub_registry = ToolRegistry()
        allowed_tools = IMPLEMENTER_TOOLS
        for tool in tool_registry.all():
            if tool.name in allowed_tools:
                sub_registry.register(tool)

        # Build curated context brief
        context_brief = (
            f"## Implementation Plan\n{plan}\n\n"
            f"## Your Task ({task_id})\n{description}\n\n"
            f"Implement this task completely. You have access to "
            f"file_read, file_write, file_edit, glob_search, grep_search, "
            f"web_fetch, web_search, and bash_exec tools."
        )

        # Build system prompt with implementer report protocol
        system_extra = (
            "\n\nYou are an IMPLEMENTER sub-agent. Complete the assigned task "
            "using the available tools.\n\n"
            "## Output Protocol\n"
            "When you finish, report your status on a NEW LINE at the end:\n"
            "- STATUS: DONE (task completed successfully)\n"
            "- STATUS: DONE_WITH_CONCERNS (completed but uncertain about X)\n"
            "- STATUS: NEEDS_CONTEXT (need more information about X)\n"
            "- STATUS: BLOCKED (blocked by dependency X)\n"
        )

        # Override model for this sub-agent
        # Create a modified LiteLLM provider with the target model
        sub_llm = self._create_model_override(llm, model)

        logger.info(
            "Spawning implementer for task '%s' with model=%s (complexity=%s)",
            task_id, model, task.get("complexity", "unknown"),
        )

        # Use the base spawn method with our config
        cfg = LangGraphSubAgentConfig(
            max_turns=500,
            timeout_seconds=3600,
            write_access=True,
            allowed_tools=allowed_tools,
            model=model,
            context_brief=context_brief,
        )

        return await self.spawn(
            task=context_brief,
            parent_ctx=parent_ctx,
            tool_registry=tool_registry,
            tool_executor=tool_executor,
            context_gatherer=context_gatherer,
            llm=sub_llm,
            config_override=cfg,
        )

    # ------------------------------------------------------------------
    # Model override
    # ------------------------------------------------------------------

    def _create_model_override(
        self, base_llm: "LlmClient", model: str
    ) -> "LlmClient":
        """Create a copy of the LLM client with a different model.

        Returns the original client if model override is not possible
        or if the model already matches.
        """
        # If base_llm already uses the target model, no change needed
        current_model = getattr(base_llm, "model", "")
        if current_model == model:
            return base_llm

        # Try to create a new client with the target model
        try:
            from harness.llm.providers.litellm_provider import LiteLlmProvider

            # Get the existing provider and api_base
            provider = getattr(base_llm, "provider", "anthropic")
            api_key = getattr(base_llm, "api_key", "")
            api_base = getattr(base_llm, "api_base", "")

            return LiteLlmProvider(
                model=model,
                api_key=api_key,
                api_base=api_base,
                provider=provider,
            )
        except Exception as exc:
            logger.warning(
                "Could not create model override for '%s': %s. "
                "Using base LLM client.",
                model, exc,
            )
            return base_llm
