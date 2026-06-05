"""File edit tool — exact string replacement in a file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class FileEditTool(Tool):
    name = "file_edit"
    description = (
        "Performs exact string replacement in a file. "
        "old_string must match exactly (including whitespace) and be unique. "
        "Set replace_all=true to replace every occurrence. "
        "Always read the file first to get exact content."
    )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to replace (must match uniquely unless replace_all)",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        try:
            file_path = self._resolve_path(params["file_path"], ctx)
        except ValueError as e:
            return ToolOutput(content=f"Error: {e}", is_error=True)
        old = params["old_string"]
        new = params["new_string"]
        replace_all = params.get("replace_all", False)

        if not file_path.exists():
            return ToolOutput(content=f"Error: File not found: {file_path}", is_error=True)

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            return ToolOutput(content=f"Error reading file: {e}", is_error=True)

        if not replace_all:
            count = content.count(old)
            if count == 0:
                return ToolOutput(content="Error: old_string not found in file", is_error=True)
            if count > 1:
                return ToolOutput(
                    content=f"Error: old_string found {count} times (not unique). Use replace_all=true or make it more specific.",
                    is_error=True,
                )

        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        if new_content == content and not replace_all:
            return ToolOutput(content="Error: no changes made", is_error=True)

        try:
            file_path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return ToolOutput(content=f"Error writing file: {e}", is_error=True)

        occurrences = content.count(old) if replace_all else 1
        return ToolOutput(content=f"File edited: {file_path}\nReplaced {occurrences} occurrence(s)")
