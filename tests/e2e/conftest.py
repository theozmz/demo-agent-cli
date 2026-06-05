"""E2E test fixtures."""

import pytest

from harness.config.config import Config, LlmConfig, LoopConfig
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.builtin.file_read import FileReadTool
from harness.tools.builtin.file_write import FileWriteTool
from harness.tools.builtin.glob_search import GlobSearchTool
from harness.tools.builtin.grep_search import GrepSearchTool
from harness.safety.pipeline import SafetyLayer
from harness.core.context import ContextGatherer


class TestRig:
    """Assembles a minimal harness environment for testing."""

    def __init__(self, tmp_path, model="stub-model"):
        self.config = Config(
            llm=LlmConfig(model=model, provider="test"),
            loop=LoopConfig(max_turns=5),
        )
        self.registry = ToolRegistry()
        self.registry.register(FileReadTool())
        self.registry.register(FileWriteTool())
        self.registry.register(GlobSearchTool())
        self.registry.register(GrepSearchTool())
        self.safety = SafetyLayer()
        self.executor = ToolExecutor(registry=self.registry, safety=self.safety)
        self.gatherer = ContextGatherer(tool_registry=self.registry, cwd=str(tmp_path))
        self.cwd = str(tmp_path)


@pytest.fixture
def test_rig(tmp_path):
    return TestRig(tmp_path)
