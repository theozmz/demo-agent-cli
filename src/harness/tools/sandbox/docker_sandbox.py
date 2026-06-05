"""Docker sandbox — container lifecycle management via Docker SDK."""

from __future__ import annotations

import asyncio
import logging
import time

from harness.tools.sandbox.runtime import (
    SandboxRuntime,
    SandboxResult,
    SandboxState,
)

logger = logging.getLogger(__name__)

try:
    import docker
    from docker import errors as docker_errors

    _HAS_DOCKER = True
except ImportError:
    _HAS_DOCKER = False


class DockerSandbox(SandboxRuntime):
    """Sandbox backed by a local Docker daemon.

    Containers are created with restricted capabilities and run as
    non-root (UID 1000) with a read-only root filesystem.
    """

    def __init__(self):
        if not _HAS_DOCKER:
            raise RuntimeError("docker Python SDK not installed. Run: pip install docker")
        self._client = docker.from_env()

    async def create(self, image: str = "python:3.12-slim") -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._create_sync, image)

    def _create_sync(self, image: str) -> str:
        try:
            self._client.images.get(image)
        except docker_errors.ImageNotFound:
            logger.info("Pulling Docker image: %s", image)
            self._client.images.pull(image)

        container = self._client.containers.run(
            image,
            command="sleep infinity",
            detach=True,
            remove=True,
            user="1000",
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=256M"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=4096,
            mem_limit="512m",
            nano_cpus=1_000_000_000,  # 1 CPU
            network_mode="none",
            working_dir="/workspace",
        )
        logger.info("Docker sandbox created: %s", container.id[:12])
        return container.id

    async def exec_cmd(
        self,
        container_id: str,
        command: str,
        *,
        timeout: int = 60,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._exec_sync, container_id, command, timeout, cwd, env)

    def _exec_sync(
        self,
        container_id: str,
        command: str,
        timeout: int,
        cwd: str,
        env: dict[str, str] | None,
    ) -> SandboxResult:
        try:
            container = self._client.containers.get(container_id)
        except docker_errors.NotFound:
            return SandboxResult(stderr=f"Container not found: {container_id}", exit_code=-1)

        start = time.monotonic()
        try:
            exit_code, output = container.exec_run(
                f"cd {cwd} && {command}",
                environment=env or {},
                user="1000",
                workdir=cwd,
            )
            duration_ms = (time.monotonic() - start) * 1000
            text = output.decode("utf-8", errors="replace") if output else ""
            return SandboxResult(
                stdout=text,
                stderr="",
                exit_code=exit_code or 0,
                duration_ms=duration_ms,
            )
        except Exception as e:
            return SandboxResult(stderr=str(e), exit_code=-1)

    async def destroy(self, container_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._destroy_sync, container_id)

    def _destroy_sync(self, container_id: str) -> None:
        try:
            container = self._client.containers.get(container_id)
            container.stop(timeout=5)
            logger.info("Docker sandbox destroyed: %s", container_id[:12])
        except docker_errors.NotFound:
            pass

    async def state(self, container_id: str) -> SandboxState:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._state_sync, container_id)

    def _state_sync(self, container_id: str) -> SandboxState:
        try:
            c = self._client.containers.get(container_id)
            status = c.status
            if status == "running":
                return SandboxState.RUNNING
            if status in ("paused",):
                return SandboxState.PAUSED
            if status in ("exited", "dead", "removing"):
                return SandboxState.TERMINATED
            return SandboxState.FAILED
        except docker_errors.NotFound:
            return SandboxState.TERMINATED
