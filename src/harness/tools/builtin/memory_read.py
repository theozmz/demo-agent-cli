"""Memory read tool — retrieves a memory fact from persistent storage."""

from __future__ import annotations

from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class MemoryReadTool(Tool):
    name = "memory_read"
    description = (
        "Read a memory fact from persistent storage. "
        "Memories are key-value pairs persisted across sessions. "
        "Use this to recall user preferences, project context, or decisions."
    )

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
                    "description": "The memory key to read (slug-like identifier)",
                },
            },
            "required": ["key"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.NEVER

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if self._store is None:
            return ToolOutput(content="Error: memory store not wired", is_error=True)

        key = params["key"]
        value = self._store.read(key)
        if value is None:
            return ToolOutput(content=f"(no memory found for key: {key})")
        return ToolOutput(content=value)
