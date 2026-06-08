"""Tool executor — the 6-step execution pipeline."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, TYPE_CHECKING

from harness.tools.tool import Tool, ToolContext, ToolOutput, ToolDomain
from harness.tools.registry import ToolRegistry
from harness.tools.permissions import PermissionPolicy, ApprovalContext, PermissionOutcome
from harness.core.errors import ToolNotFoundError, InvalidParametersError, NotAuthorizedError, ToolError

if TYPE_CHECKING:
    from harness.logging.task_logger import TaskLogger

logger = logging.getLogger(__name__)


def _truncate_for_log(text: str, max_len: int = 300) -> str:
    """Truncate a string for logging."""
    text = text or ""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...<truncated {len(text) - max_len} chars>"


class ToolExecutor:
    """
    Tool execution pipeline — all tool calls go through this path.

    Pipeline: lookup → validate → approve → execute → safety scan → return
    """

    def __init__(
        self,
        registry: ToolRegistry,
        safety: "SafetyLayer | None" = None,
        policy: PermissionPolicy | None = None,
        sandbox: "SandboxRuntime | None" = None,
    ):
        self.registry = registry
        self.safety = safety
        self.policy = policy or PermissionPolicy()
        self.sandbox = sandbox

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        ctx: ToolContext | None = None,
        approval_ctx: ApprovalContext | None = None,
    ) -> ToolOutput:
        """Execute a tool through the full pipeline."""
        ctx = ctx or ToolContext()
        approval_ctx = approval_ctx or ApprovalContext.autonomous()

        # Step 1: Lookup
        tool = self.registry.get(tool_name)
        if not tool:
            raise ToolNotFoundError(f"Tool '{tool_name}' not found", tool_name=tool_name)

        # Step 2: Validate params
        self._validate_params(tool, params)

        # Step 3: Permission check
        requirement = tool.requires_approval(params)
        outcome = self.policy.authorize(tool_name, requirement, approval_ctx)
        if outcome == PermissionOutcome.DENY:
            raise NotAuthorizedError(f"Tool '{tool_name}' denied: {outcome}", tool_name=tool_name)

        # Step 4: Execute (with tracing span)
        from harness.observability import get_backend, NoopBackend

        backend = get_backend()
        span = None
        if not isinstance(backend, NoopBackend):
            tool_sensitive = getattr(tool, "sensitive_params", set())
            safe_input = {k: v for k, v in params.items() if k not in tool_sensitive}
            span = backend.create_trace(name=f"tool.{tool_name}").span(
                name=f"tool.{tool_name}",
                input={"params": _truncate_for_log(str(safe_input), 500)},
                metadata={"tool_domain": str(tool.domain) if hasattr(tool, 'domain') else ""},
            )

        start = time.monotonic()
        try:
            output = await tool.execute(params, ctx)
        except Exception as e:
            if span:
                span.end(output={"error": str(e)}, metadata={"status": "error"})
            if isinstance(e, ToolError):
                raise
            raise ToolError(str(e), tool_name=tool_name) from e

        duration_ms = (time.monotonic() - start) * 1000
        output.duration_ms = duration_ms

        if span:
            span.end(
                output={"result_summary": _truncate_for_log(output.content)},
                metadata={
                    "duration_ms": duration_ms,
                    "is_error": output.is_error,
                    "exit_code": getattr(output, "exit_code", 0),
                },
            )

        # Step 5: Safety scan
        if self.safety and output.content:
            result = self.safety.scan_output(output.content, tool_name)
            if result.blocked:
                raise NotAuthorizedError(f"Tool output blocked by safety: {result.reason}", tool_name=tool_name)
            if result.redacted:
                output.content = result.content

        # Step 6: Log and return
        sensitive = getattr(tool, "sensitive_params", set())
        safe_params = {k: v for k, v in params.items() if k not in sensitive}
        logger.debug(f"Tool '{tool_name}' completed in {duration_ms:.0f}ms")

        if ctx.task_logger:
            ctx.task_logger.log_tool_call(
                tool_name=tool_name,
                params=safe_params,
                is_error=output.is_error,
                exit_code=getattr(output, "exit_code", 0),
                result_summary=_truncate_for_log(output.content),
                duration_ms=duration_ms,
            )

        return output

    def _validate_params(self, tool: Tool, params: dict[str, Any]):
        """Validate params against the tool's JSON Schema."""
        import jsonschema
        try:
            jsonschema.validate(params, tool.input_schema)
        except jsonschema.ValidationError as e:
            raise InvalidParametersError(str(e), tool_name=tool.name) from e
