"""File write tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class FileWriteTool(Tool):
    name = "file_write"
    description = "Write or overwrite a file. Creates parent directories automatically."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["file_path", "content"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        try:
            path = self._resolve_path(params["file_path"], ctx)
        except ValueError as e:
            return ToolOutput(content=f"Error: {e}", is_error=True)
        content = params["content"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolOutput(content=f"File written: {path} ({len(content)} bytes)")
        except Exception as e:
            return ToolOutput(content=f"Error writing file: {e}", is_error=True)
