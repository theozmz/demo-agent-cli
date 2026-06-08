"""Memory write tool — persists a memory fact across sessions."""

from __future__ import annotations

from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class MemoryWriteTool(Tool):
    name = "memory_write"
    description = (
        "Write a memory fact to persistent storage. "
        "Params: key (required) — the memory key. "
        "value (required) — the text content to store. "
        "Creates or updates a key-value pair that persists across sessions."
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
                    "description": "Memory key (slug-like identifier, e.g., 'user-pref-editor')",
                },
                "value": {
                    "type": "string",
                    "description": "The content to store",
                },
            },
            "required": ["key", "value"],
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
        value = params["value"]
        self._store.write(key, value)
        return ToolOutput(content=f"Memory stored: {key}")
