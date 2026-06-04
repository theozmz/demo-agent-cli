"""Glob search tool — fast file pattern matching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class GlobSearchTool(Tool):
    name = "glob_search"
    description = "Fast file pattern matching. Supports glob patterns like '**/*.py' or 'src/**/*.ts'."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g., '**/*.py', 'src/**/*.ts')",
                },
                "path": {
                    "type": "string",
                    "description": "Search root directory (default: current working directory)",
                },
            },
            "required": ["pattern"],
        }

    @property
    def is_read_only(self) -> bool:
        return True

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.NEVER

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        root = Path(params.get("path", ctx.cwd or ".")).resolve()
        pattern = params["pattern"]

        if not root.exists():
            return ToolOutput(content=f"Error: Path not found: {root}", is_error=True)

        try:
            matches = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            lines = [str(m.relative_to(root)) for m in matches[:500]]
            return ToolOutput(content="\n".join(lines) if lines else "(no matches)")
        except Exception as e:
            return ToolOutput(content=f"Error during glob search: {e}", is_error=True)
