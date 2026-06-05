"""Grep search tool — regex content search across files."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement


class GrepSearchTool(Tool):
    name = "grep_search"
    description = (
        "Content search using ripgrep. Full regex syntax, "
        r"e.g. 'function\s+\w+'. Filters by glob pattern and file type. "
        "Prefer this over terminal grep/rg."
    )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for in file contents",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (default: current working directory)",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py', '*.{ts,tsx}')",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": "Output mode (default: files_with_matches)",
                },
                "-i": {
                    "type": "boolean",
                    "description": "Case insensitive search",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Limit output to first N lines (default: 50)",
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
        pattern = params["pattern"]
        root = Path(params.get("path", ctx.cwd or ".")).resolve()
        if not root.exists():
            return ToolOutput(content=f"Error: Path not found: {root}", is_error=True)

        cmd = ["rg", "--no-heading", "--color", "never", "--line-number"]
        if params.get("-i"):
            cmd.append("-i")

        output_mode = params.get("output_mode", "files_with_matches")
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")

        if glob_filter := params.get("glob"):
            cmd.extend(["--glob", glob_filter])

        cmd.append("--")
        cmd.append(pattern)
        cmd.append(str(root))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(root),
            )
        except FileNotFoundError:
            return ToolOutput(
                content="Error: ripgrep (rg) not found. Install it from https://github.com/BurntSushi/ripgrep",
                is_error=True,
            )
        except subprocess.TimeoutExpired:
            return ToolOutput(content="Error: grep search timed out after 30s", is_error=True)

        lines = result.stdout.strip().split("\n") if result.stdout else []
        if result.returncode > 1:
            return ToolOutput(content=f"Error: {result.stderr.strip()}", is_error=True)

        limit = params.get("head_limit", 50)
        trimmed = lines[:limit]
        if not trimmed:
            return ToolOutput(content="(no matches)")

        suffix = f"\n... ({len(lines) - limit} more)" if len(lines) > limit else ""
        return ToolOutput(content="\n".join(trimmed) + suffix)
