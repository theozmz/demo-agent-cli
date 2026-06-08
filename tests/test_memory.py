"""Tests for the MemoryStore and memory tools."""

import pytest

from harness.memory.store import MemoryStore
from harness.tools.builtin.memory_read import MemoryReadTool
from harness.tools.builtin.memory_write import MemoryWriteTool
from harness.tools.builtin.memory_delete import MemoryDeleteTool
from harness.tools.tool import ToolContext


# ===================================================================
# TestMemoryStore — direct SQLite layer
# ===================================================================
class TestMemoryStore:
    @pytest.fixture
    def store(self, tmp_path):
        """Create a store with a temp database."""
        db = tmp_path / "test_memory.db"
        s = MemoryStore(db_path=str(db))
        yield s

    @pytest.mark.asyncio
    async def test_write_and_read(self, store):
        store.write("key1", "value1")
        result = store.read("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_read_missing_key(self, store):
        result = store.read("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, store):
        store.write("key2", "value2")
        deleted = store.delete("key2")
        assert deleted is True
        assert store.read("key2") is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, store):
        deleted = store.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_overwrite(self, store):
        store.write("key3", "v1")
        store.write("key3", "v2")
        assert store.read("key3") == "v2"

    @pytest.mark.asyncio
    async def test_list_keys(self, store):
        store.write("a", "1")
        store.write("b", "2")
        store.write("c", "3")
        keys = store.list_keys()
        assert len(keys) == 3
        assert "a" in keys
        assert "b" in keys
        assert "c" in keys


# ===================================================================
# TestMemoryTools — via Tool interface
# ===================================================================
class TestMemoryTools:
    @pytest.fixture
    def store(self, tmp_path):
        return MemoryStore(db_path=str(tmp_path / "mem.db"))

    @pytest.fixture
    def ctx(self):
        return ToolContext(cwd=".", session_id="test")

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_read_tool(self, store, ctx):
        store.write("greeting", "hello")
        tool = MemoryReadTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "greeting"}, ctx)
        assert not output.is_error
        assert "hello" in output.content

    @pytest.mark.asyncio
    async def test_read_tool_missing_key(self, store, ctx):
        tool = MemoryReadTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "no_such_key"}, ctx)
        # Should return not-found message, not an error
        assert "no memory found" in output.content.lower()

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_write_tool(self, store, ctx):
        tool = MemoryWriteTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "name", "value": "harness"}, ctx)
        assert not output.is_error
        stored = store.read("name")
        assert stored == "harness"

    @pytest.mark.asyncio
    async def test_write_tool_overwrite(self, store, ctx):
        store.write("count", "1")
        tool = MemoryWriteTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "count", "value": "2"}, ctx)
        assert not output.is_error
        assert store.read("count") == "2"

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_delete_tool(self, store, ctx):
        store.write("temp", "data")
        tool = MemoryDeleteTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "temp"}, ctx)
        assert not output.is_error
        assert store.read("temp") is None

    @pytest.mark.asyncio
    async def test_delete_tool_missing(self, store, ctx):
        tool = MemoryDeleteTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "ghost"}, ctx)
        assert "no memory found" in output.content.lower()

    # ------------------------------------------------------------------
    # unwired tools
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_read_tool_unwired(self, ctx):
        tool = MemoryReadTool()
        output = await tool.execute({"key": "x"}, ctx)
        assert output.is_error
        assert "not wired" in output.content.lower()

    @pytest.mark.asyncio
    async def test_write_tool_unwired(self, ctx):
        tool = MemoryWriteTool()
        output = await tool.execute({"key": "x", "value": "y"}, ctx)
        assert output.is_error

    @pytest.mark.asyncio
    async def test_delete_tool_unwired(self, ctx):
        tool = MemoryDeleteTool()
        output = await tool.execute({"key": "x"}, ctx)
        assert output.is_error


# ===================================================================
# TestMemoryWithTracing — verify memory tools work with NoopBackend
# ===================================================================
class TestMemoryWithTracing:
    @pytest.fixture
    def store(self, tmp_path):
        return MemoryStore(db_path=str(tmp_path / "mem.db"))

    @pytest.fixture
    def ctx(self):
        return ToolContext(cwd=".", session_id="test-tracing")

    @pytest.mark.asyncio
    async def test_read_works_with_noop_backend(self, store, ctx):
        """MemoryReadTool works correctly even when observability is noop."""
        store.write("greeting", "hello")
        tool = MemoryReadTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "greeting"}, ctx)
        assert not output.is_error
        assert "hello" in output.content

    @pytest.mark.asyncio
    async def test_write_works_with_noop_backend(self, store, ctx):
        """MemoryWriteTool works correctly even when observability is noop."""
        tool = MemoryWriteTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "tracing_test", "value": "v"}, ctx)
        assert not output.is_error
        stored = store.read("tracing_test")
        assert stored == "v"

    @pytest.mark.asyncio
    async def test_delete_works_with_noop_backend(self, store, ctx):
        """MemoryDeleteTool works correctly even when observability is noop."""
        store.write("tmp", "data")
        tool = MemoryDeleteTool()
        tool.wire_store(store)
        output = await tool.execute({"key": "tmp"}, ctx)
        assert not output.is_error
        assert store.read("tmp") is None
