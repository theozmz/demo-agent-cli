"""Sandbox runtime ABC — abstracts sandbox backends behind a common interface."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class SandboxState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    TERMINATED = "terminated"
    FAILED = "failed"


@dataclass
class SandboxResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    duration_ms: float = 0.0


class SandboxRuntime(ABC):
    """Abstract sandbox backend.

    Implementations: DockerSandbox, (future: FirecrackerSandbox, KataSandbox).
    """

    @abstractmethod
    async def create(self, image: str = "python:3.12-slim") -> str:
        """Create and start a sandbox. Returns the container id."""
        ...

    @abstractmethod
    async def exec_cmd(
        self,
        container_id: str,
        command: str,
        *,
        timeout: int = 60,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Execute a command inside a running sandbox."""
        ...

    @abstractmethod
    async def destroy(self, container_id: str) -> None:
        """Stop and remove a sandbox."""
        ...

    @abstractmethod
    async def state(self, container_id: str) -> SandboxState:
        """Query sandbox state."""
        ...


class NoOpSandbox(SandboxRuntime):
    """Fallback sandbox that executes commands directly on the host.

    Used when Docker is unavailable.  **Not secure** — intended for
    development and testing only.
    """

    def __init__(self):
        self._cwd = os.getcwd()

    async def create(self, image: str = "python:3.12-slim") -> str:
        logger.warning("NoOpSandbox: commands will run on HOST (no isolation)")
        return "noop-1"

    async def exec_cmd(
        self,
        container_id: str,
        command: str,
        *,
        timeout: int = 60,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        import asyncio

        # Resolve working directory: explicit → host cwd
        workdir = cwd or self._cwd
        if not os.path.isdir(workdir):
            workdir = self._cwd

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return SandboxResult(
                stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                exit_code=proc.returncode or 0,
            )
        except asyncio.TimeoutError:
            if proc.returncode is None:
                proc.kill()
            return SandboxResult(stdout="", stderr="Command timed out.", exit_code=-1, timed_out=True)

    async def destroy(self, container_id: str) -> None:
        pass

    async def state(self, container_id: str) -> SandboxState:
        return SandboxState.RUNNING


def get_sandbox_runtime(config_runtime: str = "docker") -> SandboxRuntime:
    """Factory: return the configured sandbox runtime.

    Falls back to NoOpSandbox when Docker is unavailable.
    """
    if config_runtime == "docker":
        try:
            from harness.tools.sandbox.docker_sandbox import DockerSandbox

            return DockerSandbox()
        except (ImportError, RuntimeError) as exc:
            logger.warning(
                "Docker unavailable — falling back to NoOpSandbox (%s)", exc
            )
    return NoOpSandbox()
