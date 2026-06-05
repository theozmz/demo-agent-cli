"""Memory delete tool — removes a memory fact from persistent storage."""

from __future__ import annotations

from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class MemoryDeleteTool(Tool):
    name = "memory_delete"
    description = "Delete a memory fact by key. The key must exist."

    def __init__(self):
        self._store = None  # type: ignore

    def wire_store(self, store) -> None:
        self._store = store

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Memory key to delete",
                },
            },
            "required": ["key"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if self._store is None:
            return ToolOutput(content="Error: memory store not wired", is_error=True)

        key = params["key"]
        deleted = await self._store.delete(key)
        if deleted:
            return ToolOutput(content=f"Memory deleted: {key}")
        return ToolOutput(content=f"(no memory found for key: {key})")
