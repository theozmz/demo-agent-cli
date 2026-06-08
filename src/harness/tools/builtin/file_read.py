"""File read tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class FileReadTool(Tool):
    name = "file_read"
    description = (
        "Read a file from the local filesystem. "
        "Params: file_path (required) — absolute path to the file. "
        "offset (optional) — starting line number, 0-indexed. "
        "limit (optional) — max lines to read, default 2000."
    )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
                "offset": {
                    "type": "integer", "minimum": 0,
                    "description": "Line number to start reading from (0-indexed)",
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 2000,
                    "description": "Maximum number of lines to read (default: 2000)",
                },
            },
            "required": ["file_path"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.NEVER

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        try:
            path = self._resolve_path(params["file_path"], ctx)
        except ValueError as e:
            return ToolOutput(content=f"Error: {e}", is_error=True)
        if not path.exists():
            return ToolOutput(content=f"Error: File not found: {path}", is_error=True)
        if path.is_dir():
            return ToolOutput(content=f"Error: Path is a directory: {path}", is_error=True)

        try:
            text = path.read_text(encoding="utf-8")
            lines = text.split("\n")
            offset = params.get("offset", 0)
            limit = params.get("limit", 2000)
            selected = lines[offset : offset + limit]
            return ToolOutput(content="\n".join(selected))
        except UnicodeDecodeError:
            return ToolOutput(content=f"(binary file, {path.stat().st_size} bytes)")
        except Exception as e:
            return ToolOutput(content=f"Error reading file: {e}", is_error=True)
