"""Complete error taxonomy for the Harness system."""

from __future__ import annotations


class HarnessError(Exception):
    """Base class for all Harness errors."""

    code: str = "HARNESS_ERROR"
    recoverable: bool = False
    retryable: bool = False

    def __init__(self, message: str = ""):
        super().__init__(message)
        self.message = message


# ─── LLM Errors ───────────────────────────────────────

class LlmError(HarnessError):
    """LLM API call error."""
    code = "LLM_ERROR"


class RateLimitError(LlmError):
    """429 — Rate limited."""
    code = "LLM_RATE_LIMITED"
    retryable = True
    retry_after: float | None = None

    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ContextOverflowError(LlmError):
    """413 — Context window exceeded."""
    code = "LLM_CONTEXT_OVERFLOW"
    recoverable = True


class AuthError(LlmError):
    """401 — Authentication failed."""
    code = "LLM_AUTH_ERROR"
    recoverable = True


class ServerError(LlmError):
    """5xx — Server error."""
    code = "LLM_SERVER_ERROR"
    retryable = True


# ─── Tool Errors ──────────────────────────────────────

class ToolError(HarnessError):
    """Tool execution error."""

    def __init__(self, message: str = "", tool_name: str = ""):
        super().__init__(message)
        self.tool_name = tool_name


class ToolNotFoundError(ToolError):
    code = "TOOL_NOT_FOUND"


class InvalidParametersError(ToolError):
    code = "TOOL_INVALID_PARAMS"


class NotAuthorizedError(ToolError):
    code = "TOOL_NOT_AUTHORIZED"


# ─── Safety Errors ────────────────────────────────────

class SafetyError(HarnessError):
    """Safety-related error."""
    code = "SAFETY_ERROR"


class PromptInjectionDetectedError(SafetyError):
    code = "SAFETY_PROMPT_INJECTION"


class LeakDetectedError(SafetyError):
    code = "SAFETY_LEAK_DETECTED"


# ─── Config Errors ────────────────────────────────────

class ConfigError(HarnessError):
    """Configuration error."""
    code = "CONFIG_ERROR"


class ConfigValidationError(ConfigError):
    code = "CONFIG_VALIDATION_ERROR"


# ─── Sandbox Errors ───────────────────────────────────

class SandboxError(HarnessError):
    """Sandbox execution error."""
    code = "SANDBOX_ERROR"


# ─── Loop Errors ──────────────────────────────────────

class LoopError(HarnessError):
    """Agentic loop error."""
    code = "LOOP_ERROR"


class MaxTurnsReachedError(LoopError):
    code = "LOOP_MAX_TURNS"


class CircuitBreakerTrippedError(LoopError):
    code = "LOOP_CIRCUIT_BREAK"
