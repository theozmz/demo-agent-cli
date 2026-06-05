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
from harness.safety.pipeline import SafetyLayer
from harness.core.context import ContextGatherer

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
        executor = ToolExecutor(registry=registry, safety=safety)

        # 6. Create context gatherer
        cwd = str(Path.cwd())
        gatherer = ContextGatherer(tool_registry=registry, cwd=cwd)

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
        registry.register(FileReadTool())
        registry.register(FileWriteTool())
        registry.register(GlobSearchTool())
        return registry
