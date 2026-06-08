"""Tool registry — manages registration, lookup, and cache-stable pool assembly."""

from __future__ import annotations

import json
import hashlib
from typing import Literal

from harness.tools.tool import Tool


def _annotate_input_schema(input_schema: dict) -> dict:
    """Add [REQUIRED] prefix to each required property's description.

    This gives the LLM a direct textual signal about which parameters are
    required, independent of the JSON Schema ``required`` array. Critical
    for API compatibility layers that may not fully convey schema constraints.
    """
    required = input_schema.get("required", [])
    properties = input_schema.get("properties", {})
    if not required or not properties:
        return input_schema

    annotated_props = {}
    for name, prop in properties.items():
        prop = dict(prop)
        desc = prop.get("description", "")
        if name in required and not desc.startswith("[REQUIRED]"):
            prop["description"] = f"[REQUIRED] {desc}"
        annotated_props[name] = prop

    return {**input_schema, "properties": annotated_props}


class ToolRegistry:
    """
    Central registry for all tools.

    Built-in tools are registered first as a contiguous prefix
    for prompt-cache stability. MCP tools are appended after.
    Tools within each partition are sorted alphabetically.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._builtin_names: list[str] = []
        self._mcp_names: list[str] = []
        self._schema_cache: dict[str, dict] = {}
        self._sorted_cache: list[Tool] | None = None

    def register(self, tool: Tool, source: Literal["builtin", "mcp", "plugin"] = "builtin"):
        """Register a tool, validating its schema first."""
        if not tool.name:
            raise ValueError(f"Tool has empty name — all tools must set 'name'")
        if tool.name in self._tools:
            existing = self._tools[tool.name]
            if source == "builtin" and getattr(existing, "_source", "") == "mcp":
                pass  # builtins shadow MCP tools
            else:
                return  # already registered

        tool._source = source  # type: ignore[attr-defined]
        self._tools[tool.name] = tool
        if source == "builtin":
            self._builtin_names.append(tool.name)
        elif source == "mcp":
            self._mcp_names.append(tool.name)
        self._sorted_cache = None

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if the tool was removed."""
        if name not in self._tools:
            return False
        del self._tools[name]
        if name in self._builtin_names:
            self._builtin_names.remove(name)
        if name in self._mcp_names:
            self._mcp_names.remove(name)
        self._schema_cache.pop(name, None)
        self._sorted_cache = None
        return True

    def all_tools(self, enabled_only: bool = True) -> list[Tool]:
        """Return all tools in cache-stable order (builtins alpha → MCP alpha)."""
        if self._sorted_cache is not None:
            tools = self._sorted_cache
        else:
            builtins = sorted(
                [self._tools[n] for n in self._builtin_names if n in self._tools],
                key=lambda t: t.name,
            )
            mcps = sorted(
                [self._tools[n] for n in self._mcp_names if n in self._tools],
                key=lambda t: t.name,
            )
            self._sorted_cache = builtins + mcps
            tools = self._sorted_cache
        return [t for t in tools if not enabled_only or t.is_enabled()]

    def get_schemas(self) -> list[dict]:
        """Return API-ready tool schemas in Anthropic-native format.

        Anthropic uses ``input_schema`` as the key (not ``parameters``).
        LiteLLM handles translation to other providers automatically.

        Required properties are annotated with a ``[REQUIRED]`` prefix in
        their description so the LLM receives a redundant textual signal —
        critical for API compatibility layers that may strip JSON Schema
        constraints.
        """
        schemas = []
        for tool in self.all_tools():
            if tool.name not in self._schema_cache:
                raw_schema = tool.input_schema
                annotated = _annotate_input_schema(raw_schema)
                self._schema_cache[tool.name] = {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": annotated,
                }
            schemas.append(self._schema_cache[tool.name])
        return schemas

    def tools_hash(self) -> str:
        """Stable hash of all tool schemas — used for prompt cache fingerprinting."""
        schemas = self.get_schemas()
        canonical = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
        return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.all_tools()]
