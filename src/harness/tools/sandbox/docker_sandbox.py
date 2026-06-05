"""Docker sandbox — container lifecycle management via Docker SDK."""

from __future__ import annotations

import asyncio
import logging
import os
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

# Default timeout for sandbox operations (seconds)
_CREATE_TIMEOUT = 120  # image pull can be slow
_EXEC_TIMEOUT = 60


class DockerSandbox(SandboxRuntime):
    """Sandbox backed by a local Docker daemon.

    Containers are created with restricted capabilities and run as
    non-root (UID 1000) with a read-only root filesystem.
    """

    def __init__(self):
        if not _HAS_DOCKER:
            raise RuntimeError("docker Python SDK not installed. Run: pip install docker")
        try:
            self._client = docker.from_env()
        except docker_errors.DockerException as exc:
            raise RuntimeError(f"Cannot connect to Docker daemon: {exc}") from exc

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------
    async def create(self, image: str = "python:3.12-slim") -> str:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._create_sync, image),
                timeout=_CREATE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Docker sandbox creation timed out after {_CREATE_TIMEOUT}s "
                f"(image pull may be slow or network unavailable)"
            )

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

    # ------------------------------------------------------------------
    # exec_cmd
    # ------------------------------------------------------------------
    async def exec_cmd(
        self,
        container_id: str,
        command: str,
        *,
        timeout: int = _EXEC_TIMEOUT,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None, self._exec_sync, container_id, command, timeout, cwd, env
                ),
                timeout=timeout + 5,  # give a grace margin over the docker-internal timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Command timed out in container %s after %ds", container_id[:12], timeout
            )
            return SandboxResult(
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                timed_out=True,
            )

    def _exec_sync(
        self,
        container_id: str,
        command: str,
        timeout: int,
        cwd: str,
        env: dict[str, str] | None,
    ) -> SandboxResult:
        # Verify container exists and is running
        try:
            container = self._client.containers.get(container_id)
        except docker_errors.NotFound:
            return SandboxResult(
                stderr=f"Container not found: {container_id}", exit_code=-1
            )

        if container.status != "running":
            return SandboxResult(
                stderr=(
                    f"Container {container_id[:12]} is not running "
                    f"(status: {container.status})"
                ),
                exit_code=-1,
            )

        start = time.monotonic()
        try:
            exec_result = container.exec_run(
                command,
                environment=env or {},
                user="1000",
                workdir=cwd,
            )
            duration_ms = (time.monotonic() - start) * 1000

            exit_code = exec_result.exit_code
            output = exec_result.output
            text = output.decode("utf-8", errors="replace") if output else ""
            return SandboxResult(
                stdout=text,
                stderr="",
                exit_code=exit_code if exit_code is not None else 0,
                duration_ms=duration_ms,
            )
        except docker_errors.APIError as exc:
            logger.error("Docker exec failed in %s: %s", container_id[:12], exc)
            return SandboxResult(
                stderr=str(exc),
                exit_code=-1,
                duration_ms=(time.monotonic() - start) * 1000,
            )

    # ------------------------------------------------------------------
    # destroy
    # ------------------------------------------------------------------
    async def destroy(self, container_id: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._destroy_sync, container_id)

    def _destroy_sync(self, container_id: str) -> None:
        try:
            container = self._client.containers.get(container_id)
            container.stop(timeout=5)
            logger.info("Docker sandbox destroyed: %s", container_id[:12])
        except docker_errors.NotFound:
            logger.debug("Container already gone: %s", container_id[:12])
        except docker_errors.APIError as exc:
            logger.warning("Error destroying container %s: %s", container_id[:12], exc)

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
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
        except docker_errors.APIError as exc:
            logger.warning("Error querying container %s: %s", container_id[:12], exc)
            return SandboxState.FAILED
