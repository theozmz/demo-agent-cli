"""Tests for the tool system."""

import pytest

from harness.tools.tool import ToolContext
from harness.tools.registry import ToolRegistry
from harness.tools.executor import ToolExecutor
from harness.tools.builtin.file_read import FileReadTool
from harness.tools.builtin.file_write import FileWriteTool
from harness.tools.builtin.glob_search import GlobSearchTool
from harness.tools.permissions import ApprovalContext, PermissionPolicy, PermissionOutcome
from harness.tools.tool import ApprovalRequirement
from harness.core.errors import ToolNotFoundError


class TestToolRegistry:
    """Test tool registration and lookup."""

    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = FileReadTool()
        registry.register(tool)
        assert registry.get("file_read") is tool
        assert registry.get("nonexistent") is None

    def test_all_tools_sorted(self):
        registry = ToolRegistry()
        registry.register(FileWriteTool())   # file_write
        registry.register(FileReadTool())    # file_read
        registry.register(GlobSearchTool())  # glob_search
        names = [t.name for t in registry.all_tools()]
        assert names == ["file_read", "file_write", "glob_search"]

    def test_get_schemas(self, tool_registry):
        schemas = tool_registry.get_schemas()
        assert len(schemas) == 3
        for s in schemas:
            assert "name" in s
            assert "description" in s
            assert "input_schema" in s

    def test_schema_cache_stability(self, tool_registry):
        s1 = tool_registry.get_schemas()
        s2 = tool_registry.get_schemas()
        # Same session should return same object references
        for a, b in zip(s1, s2):
            assert a is b


class TestToolExecutor:
    """Test tool execution pipeline."""

    @pytest.mark.asyncio
    async def test_execute_file_read(self, tool_executor, tool_ctx, approval_auto, temp_dir):
        test_file = temp_dir / "test.txt"
        test_file.write_text("line 1\nline 2\nline 3")
        output = await tool_executor.execute(
            "file_read", {"file_path": str(test_file)}, tool_ctx, approval_auto,
        )
        assert "line 1" in output.content
        assert not output.is_error

    @pytest.mark.asyncio
    async def test_execute_file_not_found(self, tool_executor, tool_ctx, approval_auto):
        output = await tool_executor.execute(
            "file_read", {"file_path": "/nonexistent/path.txt"}, tool_ctx, approval_auto,
        )
        assert output.is_error

    @pytest.mark.asyncio
    async def test_execute_nonexistent_tool(self, tool_executor, tool_ctx, approval_auto):
        with pytest.raises(ToolNotFoundError):
            await tool_executor.execute("nonexistent", {}, tool_ctx, approval_auto)

    @pytest.mark.asyncio
    async def test_execute_file_write(self, tool_executor, tool_ctx, approval_auto, temp_dir):
        test_file = temp_dir / "output.txt"
        output = await tool_executor.execute(
            "file_write",
            {"file_path": str(test_file), "content": "hello world"},
            tool_ctx, approval_auto,
        )
        assert not output.is_error
        assert test_file.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_execute_glob_search(self, tool_executor, tool_ctx, approval_auto, temp_dir):
        (temp_dir / "a.py").write_text("x")
        (temp_dir / "b.py").write_text("y")
        (temp_dir / "c.txt").write_text("z")
        output = await tool_executor.execute(
            "glob_search", {"pattern": "*.py", "path": str(temp_dir)}, tool_ctx, approval_auto,
        )
        assert "a.py" in output.content
        assert "b.py" in output.content
        assert "c.txt" not in output.content


class TestPermissions:
    """Test permission policy decisions."""

    def test_never_always_allows(self):
        policy = PermissionPolicy()
        result = policy.authorize("file_read", ApprovalRequirement.NEVER, ApprovalContext.autonomous())
        assert result == PermissionOutcome.ALLOW

    def test_always_needs_approval(self):
        policy = PermissionPolicy()
        result = policy.authorize("bash_exec", ApprovalRequirement.ALWAYS, ApprovalContext.interactive())
        assert result == PermissionOutcome.NEEDS_APPROVAL

    def test_auto_approve_unless_auto(self):
        policy = PermissionPolicy()
        result = policy.authorize("file_write", ApprovalRequirement.UNLESS_AUTO, ApprovalContext.autonomous())
        assert result == PermissionOutcome.ALLOW

    def test_blocked_tool(self):
        policy = PermissionPolicy()
        ctx = ApprovalContext(allowed_tools={"file_read"})
        result = policy.authorize("file_write", ApprovalRequirement.NEVER, ctx)
        assert result == PermissionOutcome.DENY
