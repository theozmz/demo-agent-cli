"""MCP (Model Context Protocol) client manager — external tool servers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement
from harness.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class McpServerConfig:
    name: str
    transport: str = "stdio"  # "stdio" | "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    auto_approve: list[str] = field(default_factory=list)
    timeout_ms: int = 30_000

    @classmethod
    def from_toml(cls, name: str, data: dict) -> "McpServerConfig":
        return cls(
            name=name,
            transport=data.get("transport", "stdio"),
            command=data.get("command"),
            args=data.get("args", []),
            url=data.get("url"),
            env=data.get("env", {}),
            auto_approve=data.get("auto_approve", []),
            timeout_ms=data.get("timeout_ms", 30_000),
        )


class McpToolWrapper(Tool):
    """Adapts an MCP server tool to the harness Tool ABC."""

    def __init__(self, server_name: str, tool_name: str, description: str, input_schema: dict, manager: "McpClientManager"):
        self._server = server_name
        self._tool_name = tool_name
        self._desc = description
        self._schema = input_schema
        self._mgr = manager

    @property
    def name(self) -> str:
        return f"mcp__{self._server}__{self._tool_name}"

    @property
    def description(self) -> str:
        return f"[MCP:{self._server}] {self._desc}"

    @property
    def input_schema(self) -> dict:
        return self._schema

    @property
    def is_read_only(self) -> bool:
        return False  # Conservative default

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        cfg = self._mgr.get_config(self._server)
        if cfg and self._tool_name in cfg.auto_approve:
            return ApprovalRequirement.NEVER
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        try:
            result = await self._mgr.call_tool(self._server, self._tool_name, params)
            return ToolOutput(content=result or "(no output)")
        except Exception as e:
            return ToolOutput(content=f"MCP tool error: {e}", is_error=True)


class McpClientManager:
    """Manages MCP server lifecycle: connect, discover, register, heartbeat.

    Currently supports a stub implementation.  Full stdio/SSE support
    requires the ``mcp`` Python SDK (``pip install mcp``).
    """

    def __init__(self):
        self._configs: dict[str, McpServerConfig] = {}
        self._connected: set[str] = set()
        self._tools: dict[str, McpToolWrapper] = {}

    def add_server(self, config: McpServerConfig) -> None:
        self._configs[config.name] = config

    def get_config(self, name: str) -> McpServerConfig | None:
        return self._configs.get(name)

    async def discover_and_register(self, registry: ToolRegistry) -> int:
        """Discover tools from all configured MCP servers and register them.

        Returns the number of tools registered.
        """
        count = 0
        for name, cfg in self._configs.items():
            try:
                tools = await self._discover(cfg)
                for tool in tools:
                    registry.register(tool)
                    self._tools[tool.name] = tool
                    count += 1
                self._connected.add(name)
                logger.info("MCP server '%s': %d tools registered", name, len(tools))
            except Exception as e:
                logger.warning("MCP server '%s' discovery failed: %s", name, e)
        return count

    async def _discover(self, cfg: McpServerConfig) -> list[McpToolWrapper]:
        """Discover tools from a single MCP server.

        Stub implementation — returns an empty list.  When the ``mcp``
        SDK is installed, this performs the full initialize →
        tools/list handshake.
        """
        # Stub: MCP SDK not yet integrated.  When installed, use:
        #   from mcp import ClientSession, StdioServerParameters
        #   ... full lifecycle ...
        logger.debug("MCP discovery skipped for '%s' (SDK not integrated)", cfg.name)
        return []

    async def call_tool(self, server: str, tool_name: str, params: dict) -> str:
        """Call an MCP tool.

        Stub implementation — returns an error message.
        """
        return f"MCP tool '{tool_name}' on server '{server}' called with params: {params}"

    async def shutdown(self) -> None:
        """Disconnect all MCP servers."""
        self._connected.clear()
        self._tools.clear()
