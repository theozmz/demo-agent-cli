"""Shared test fixtures."""

import pytest
from pathlib import Path
import tempfile
import os

from harness.config.config import Config, LlmConfig, LoopConfig
from harness.tools.registry import ToolRegistry
from harness.tools.tool import ToolContext
from harness.tools.builtin.file_read import FileReadTool
from harness.tools.builtin.file_write import FileWriteTool
from harness.tools.builtin.glob_search import GlobSearchTool
from harness.tools.executor import ToolExecutor
from harness.tools.permissions import ApprovalContext, PermissionPolicy


@pytest.fixture
def temp_dir():
    """Create a temporary directory for file-based tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def default_config():
    """Return a default Config for testing."""
    return Config(
        llm=LlmConfig(model="claude-sonnet-4-6-20250514"),
        loop=LoopConfig(max_turns=5),
    )


@pytest.fixture
def tool_registry():
    """Create a registry with built-in tools."""
    r = ToolRegistry()
    r.register(FileReadTool())
    r.register(FileWriteTool())
    r.register(GlobSearchTool())
    return r


@pytest.fixture
def tool_executor(tool_registry):
    """Create a ToolExecutor with the test registry."""
    return ToolExecutor(registry=tool_registry)


@pytest.fixture
def tool_ctx():
    """Create a basic ToolContext."""
    return ToolContext(cwd=os.getcwd(), session_id="test", turn_id="test-1")


@pytest.fixture
def approval_auto():
    """Autonomous approval context."""
    return ApprovalContext.autonomous()
