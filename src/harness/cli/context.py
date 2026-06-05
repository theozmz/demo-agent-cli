"""AppContext — bundles all initialized infrastructure for a CLI invocation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from harness.config.config import Config
from harness.llm.client import LlmClient
from harness.llm.providers.litellm_provider import LiteLlmProvider
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.builtin.file_read import FileReadTool
from harness.tools.builtin.file_write import FileWriteTool
from harness.tools.builtin.glob_search import GlobSearchTool
from harness.tools.builtin.grep_search import GrepSearchTool
from harness.tools.builtin.web_fetch import WebFetchTool
from harness.tools.builtin.web_search import WebSearchTool
from harness.tools.builtin.bash_exec import BashExecTool
from harness.tools.builtin.file_edit import FileEditTool
from harness.tools.builtin.memory_read import MemoryReadTool
from harness.tools.builtin.memory_write import MemoryWriteTool
from harness.tools.builtin.memory_delete import MemoryDeleteTool
from harness.tools.sandbox.runtime import get_sandbox_runtime
from harness.tools.mcp.client_manager import McpClientManager
from harness.memory.store import MemoryStore
from harness.safety.pipeline import SafetyLayer
from harness.core.context import ContextGatherer
from harness.core.subagent import AgentTool, SubAgentManager
from harness.repomap.repomap import RepoMap

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Holds all initialized infrastructure for a harness CLI command.

    Created once during the initialization phase, then passed to the
    command handler which only deals with execution logic.
    """

    config: Config
    llm: LlmClient
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    safety: SafetyLayer
    context_gatherer: ContextGatherer
    cwd: str = field(default_factory=lambda: str(Path.cwd()))

    @classmethod
    def initialize(
        cls,
        config_path: str | None = None,
        model_override: str | None = None,
        provider_override: str | None = None,
        repomap_override: bool | None = None,
        debug: bool = False,
    ) -> "AppContext":
        """Initialize all infrastructure from config + CLI args.

        api_key and api_base are read exclusively from harness.toml.
        """
        # 1. Load config
        config = Config.load(config_path)

        # 2. Apply CLI overrides
        if provider_override:
            config.llm.provider = provider_override
        if model_override:
            config.llm.model = model_override

        # 3. Validate essential config
        if not config.llm.api_key and config.llm.provider != "ollama":
            logger.warning(
                "No api_key configured for provider '%s'. Add it to harness.toml [llm] section.",
                config.llm.provider,
            )

        # 4. Create LLM client — provider is passed so litellm can
        #    route models without an explicit prefix (e.g. "deepseek/")
        llm = LiteLlmProvider(
            model=config.llm.model,
            api_key=config.llm.api_key,
            api_base=config.llm.api_base,
            provider=config.llm.provider,
        )

        # 5. Build tool infrastructure
        registry = cls._build_tool_registry()
        safety = SafetyLayer()

        # Sandbox runtime (NoOpSandbox fallback when Docker unavailable)
        sandbox = get_sandbox_runtime(config.sandbox.runtime)
        bash_exec_tool = registry.get("bash_exec")
        if bash_exec_tool is not None:
            bash_exec_tool.wire_sandbox(sandbox)

        executor = ToolExecutor(
            registry=registry, safety=safety, sandbox=sandbox,
        )

        # MCP tool discovery (non-blocking — runs in background)
        mcp_mgr = McpClientManager()
        # Load MCP servers from config if present (future: [mcp.servers] in harness.toml)
        import asyncio
        try:
            asyncio.get_running_loop()
            asyncio.ensure_future(mcp_mgr.discover_and_register(registry))
        except RuntimeError:
            # No running loop — skip (discovery runs on first request)
            pass

        # 6. Create context gatherer (+ optional RepoMap)
        cwd = str(Path.cwd())
        gatherer = ContextGatherer(tool_registry=registry, cwd=cwd)

        if repomap_override is True or config.repomap.enabled:
            try:
                repomap = RepoMap(root=cwd, max_tokens=config.repomap.max_map_tokens)
                repo_map_text = repomap.build()
                gatherer.set_repo_map(repo_map_text)
                logger.debug("RepoMap built and injected (%d chars)", len(repo_map_text))
            except Exception as exc:
                logger.warning("RepoMap build failed (continuing without): %s", exc)

        # 7. Wire AgentTool with sub-agent infrastructure
        agent_tool = registry.get("agent")
        if agent_tool is not None:
            agent_tool.wire(
                tool_registry=registry,
                tool_executor=executor,
                context_gatherer=gatherer,
                llm=llm,
            )

        # 8. Wire memory tools with SQLite store
        memory_store = MemoryStore()
        for name in ("memory_read", "memory_write", "memory_delete"):
            mt = registry.get(name)
            if mt is not None:
                mt.wire_store(memory_store)

        logger.debug(
            "AppContext initialized — provider=%s model=%s base_url=%s",
            config.llm.provider,
            config.llm.model,
            config.llm.api_base or "(default)",
        )

        return cls(
            config=config,
            llm=llm,
            tool_registry=registry,
            tool_executor=executor,
            safety=safety,
            context_gatherer=gatherer,
            cwd=cwd,
        )

    @staticmethod
    def _build_tool_registry() -> ToolRegistry:
        """Create and populate the tool registry with built-in tools."""
        registry = ToolRegistry()
        # Read-only exploration tools
        registry.register(FileReadTool())
        registry.register(GlobSearchTool())
        registry.register(GrepSearchTool())
        registry.register(WebFetchTool())
        registry.register(WebSearchTool())
        # Write / edit tools
        registry.register(FileWriteTool())
        registry.register(FileEditTool())
        # Sandbox tool
        registry.register(BashExecTool())
        # Memory tools (wired later)
        registry.register(MemoryReadTool())
        registry.register(MemoryWriteTool())
        registry.register(MemoryDeleteTool())
        # Sub-agent tool (wired later)
        registry.register(AgentTool())
        return registry
