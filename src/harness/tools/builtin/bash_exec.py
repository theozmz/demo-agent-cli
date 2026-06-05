"""Bash execution tool — runs commands inside a sandbox container."""

from __future__ import annotations

import logging
from typing import Any

from harness.tools.tool import Tool, ToolContext, ToolOutput, ApprovalRequirement, ToolDomain

logger = logging.getLogger(__name__)


class BashExecTool(Tool):
    name = "bash_exec"
    description = (
        "Execute a shell command in a sandboxed environment. "
        "Use for running tests, installing packages, git operations, "
        "build commands, and any code execution. "
        "Commands run with a 120s timeout and are isolated from the host."
    )
    domain = ToolDomain.CONTAINER

    def __init__(self):
        self._sandbox = None
        self._container_id: str | None = None

    def wire_sandbox(self, sandbox) -> None:
        """Inject the sandbox runtime (called during init)."""
        self._sandbox = sandbox

    async def cleanup(self) -> None:
        """Destroy the sandbox container if one was created.

        Call this when the session ends to prevent resource leaks.
        """
        if self._container_id is not None and self._sandbox is not None:
            try:
                await self._sandbox.destroy(self._container_id)
            except Exception as exc:
                logger.warning("Failed to destroy sandbox container: %s", exc)
            finally:
                self._container_id = None

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 300,
                    "description": "Timeout in seconds (default: 120)",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory inside the sandbox (default: /workspace)",
                },
            },
            "required": ["command"],
        }

    @property
    def is_read_only(self) -> bool:
        return False

    def requires_approval(self, params: dict[str, Any]) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        if self._sandbox is None:
            return ToolOutput(content="Error: sandbox not wired — check harness.toml [sandbox]", is_error=True)

        command = params["command"]
        timeout = params.get("timeout", 120)
        cwd = params.get("cwd", "/workspace")

        # Lazy-create container
        if self._container_id is None:
            try:
                self._container_id = await self._sandbox.create()
            except Exception as e:
                return ToolOutput(content=f"Error creating sandbox: {e}", is_error=True)

        # Verify container is still healthy (recreate if needed)
        try:
            state = await self._sandbox.state(self._container_id)
            if state.value in ("terminated", "failed"):
                logger.warning(
                    "Sandbox container %s is %s — recreating",
                    self._container_id[:12], state.value,
                )
                self._container_id = await self._sandbox.create()
        except Exception:
            # state query failed — try to recreate
            logger.warning("Sandbox state query failed — recreating container")
            self._container_id = await self._sandbox.create()

        try:
            result = await self._sandbox.exec_cmd(
                self._container_id,
                command,
                timeout=timeout,
                cwd=cwd,
            )
        except Exception as e:
            return ToolOutput(content=f"Error executing command: {e}", is_error=True)

        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.timed_out:
            output += f"\n[Command timed out after {timeout}s]"
        if result.exit_code != 0:
            output += f"\n[Exit code: {result.exit_code}]"

        return ToolOutput(
            content=output.strip() or "(no output)",
            is_error=(result.exit_code != 0),
        )
