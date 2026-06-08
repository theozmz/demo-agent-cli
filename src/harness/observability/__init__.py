"""Observability package — Trace-Observation-Score model via pluggable backends.

Default backend is NoopBackend (zero overhead). Call ``init_backend(config)``
once during startup to activate the configured backend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from harness.observability.backend import (
    ObservabilityBackend,
    TraceContext,
    SpanContext,
    GenerationContext,
    ScoreConfig,
    ScoreDataType,
)
from harness.observability.noop_backend import NoopBackend

if TYPE_CHECKING:
    from harness.config.config import ObservabilityConfig

logger = logging.getLogger(__name__)

__all__ = [
    "get_backend",
    "init_backend",
    "shutdown_backend",
    "observe",
    "ObservabilityBackend",
    "NoopBackend",
    "TraceContext",
    "SpanContext",
    "GenerationContext",
    "ScoreConfig",
    "ScoreDataType",
]

_backend: ObservabilityBackend = NoopBackend()


def get_backend() -> ObservabilityBackend:
    """Return the global observability backend singleton."""
    return _backend


def init_backend(config: "ObservabilityConfig") -> ObservabilityBackend:
    """Initialize the global backend from config.

    Called once during AppContext.initialize(). When backend="langfuse" but
    the SDK is not installed, falls back to NoopBackend gracefully.
    """
    global _backend
    if config.backend == "langfuse":
        try:
            from harness.observability.langfuse_backend import LangfuseBackend
            _backend = LangfuseBackend(
                public_key=config.langfuse_public_key,
                secret_key=config.langfuse_secret_key,
                host=config.langfuse_host,
            )
            logger.info("Langfuse observability backend initialized — host=%s", config.langfuse_host)
        except ImportError:
            logger.warning(
                "langfuse package not installed; observability disabled. "
                "Install with: uv pip install -e '.[observability]'"
            )
            _backend = NoopBackend()
        except Exception as exc:
            logger.warning("Failed to initialize Langfuse backend: %s", exc)
            _backend = NoopBackend()
    else:
        _backend = NoopBackend()
    return _backend


async def shutdown_backend() -> None:
    """Flush and shutdown the global backend."""
    global _backend
    try:
        await _backend.aflush()
    except Exception:
        logger.debug("Error during backend flush", exc_info=True)
    try:
        _backend.shutdown()
    except Exception:
        logger.debug("Error during backend shutdown", exc_info=True)
    _backend = NoopBackend()


def observe(name: str = "", as_type: str = "span"):
    """Tier 2 decorator — wraps a function as a trace or span.

    When the backend is NoopBackend, runs the function with zero overhead.

    Usage::

        @observe(name="memory_retrieval")
        async def retrieve_memories(query: str) -> list[str]:
            ...
    """
    import functools

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            backend = _backend
            if isinstance(backend, NoopBackend):
                return await func(*args, **kwargs)
            span_name = name or func.__name__
            trace = backend.create_trace(name=span_name)
            span = trace.span(name=span_name, input=_safe_repr(args, kwargs))
            try:
                result = await func(*args, **kwargs)
                span.end(output={"result": _truncate(str(result), 500)})
                trace.end(output={"status": "success"})
                return result
            except Exception as e:
                span.end(output={"error": str(e)})
                trace.end(output={"status": "error"})
                raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            backend = _backend
            if isinstance(backend, NoopBackend):
                return func(*args, **kwargs)
            import asyncio
            return asyncio.run(async_wrapper(*args, **kwargs))

        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _safe_repr(args: tuple, kwargs: dict) -> dict:
    return {
        "args": _truncate(str(args), 500),
        "kwargs": _truncate(str(kwargs), 500),
    }


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"...<truncated {len(text) - max_len} chars>"
