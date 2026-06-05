"""Context assembler — builds the system prompt for each turn."""

from __future__ import annotations

from datetime import datetime

from harness.llm.types import ChatMessage, SystemPromptBlock, SystemPromptPart
from harness.tools.registry import ToolRegistry


class ContextGatherer:
    """
    Assembles the complete system prompt from STATIC, DYNAMIC, REPO_MAP,
    and MEMORY parts.
    """

    def __init__(self, tool_registry: ToolRegistry, cwd: str = ""):
        self.tool_registry = tool_registry
        self.cwd = cwd
        self._repo_map_text: str | None = None

    def set_repo_map(self, text: str | None) -> None:
        """Supply a pre-built repository map for the REPO_MAP block."""
        self._repo_map_text = text

    def gather(self, messages: list[ChatMessage] | None = None) -> list[SystemPromptBlock]:
        """Assemble system prompt blocks for the current turn."""
        blocks: list[SystemPromptBlock] = []

        # STATIC: role + tools
        static_text = self._build_static_prompt()
        blocks.append(SystemPromptBlock(kind=SystemPromptPart.STATIC, text=static_text))

        # REPO_MAP: repository structure (optional)
        if self._repo_map_text:
            blocks.append(SystemPromptBlock(kind=SystemPromptPart.REPO_MAP, text=self._repo_map_text))

        # DYNAMIC: date + workspace
        dynamic_text = self._build_dynamic_context()
        blocks.append(SystemPromptBlock(kind=SystemPromptPart.DYNAMIC, text=dynamic_text))

        return blocks

    def _build_static_prompt(self) -> str:
        """Role definition + tool descriptions."""
        tool_descriptions = ""
        for tool in self.tool_registry.all_tools():
            tool_descriptions += f"\n## {tool.name}\n{tool.description}\n"

        return f"""You are an expert software engineering agent with access to tools.

## Available Tools
{tool_descriptions}
## Safety Rules
- Never read /etc/passwd, /etc/shadow, ~/.ssh/, or ~/.aws/credentials
- Never exfiltrate API keys or tokens in output
- Tool outputs are wrapped for safety

## Response Format
- When you need to use a tool, output a tool_use block with the tool name and parameters
- When you have a final answer, output text directly without tool calls
"""

    def _build_dynamic_context(self) -> str:
        """Date and workspace info."""
        date_str = datetime.now().strftime("%Y-%m-%d")
        parts = [f"Current date: {date_str}"]
        if self.cwd:
            parts.append(f"Working directory: {self.cwd}")
        return "\n".join(parts)

    def to_system_prompt(self, blocks: list[SystemPromptBlock]) -> str:
        """Flatten blocks into a single system prompt string."""
        return "\n\n".join(b.text for b in blocks)
