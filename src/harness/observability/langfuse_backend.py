"""LangfuseBackend — wraps the langfuse SDK for trace/span/score management."""

from __future__ import annotations

import logging
from typing import Any

from harness.observability.backend import (
    ObservabilityBackend,
    TraceContext,
    SpanContext,
    GenerationContext,
    ScoreConfig,
    ScoreDataType,
)

logger = logging.getLogger(__name__)


class _LangfuseSpanContext(SpanContext):
    """Thin wrapper around a langfuse Span."""

    def __init__(self, lf_span: Any):
        self._span = lf_span
        self.span_id: str = getattr(lf_span, 'id', '')

    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata is not None:
                kwargs["metadata"] = metadata
            self._span.end(**kwargs)
        except Exception:
            logger.debug("Failed to end langfuse span", exc_info=True)

    def event(self, name: str, input: Any = None, output: Any = None) -> None:
        try:
            self._span.event(name=name, input=input, output=output)
        except Exception:
            logger.debug("Failed to create langfuse span event", exc_info=True)


class _LangfuseGenerationContext(GenerationContext):
    """Thin wrapper around a langfuse Generation."""

    def __init__(self, lf_generation: Any):
        self._gen = lf_generation

    def end(self, output: Any = None, usage: dict | None = None,
            model: str = "") -> None:
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if usage is not None:
                kwargs["usage"] = usage
            if model:
                kwargs["model"] = model
            self._gen.end(**kwargs)
        except Exception:
            logger.debug("Failed to end langfuse generation", exc_info=True)


class _LangfuseTraceContext(TraceContext):
    """Thin wrapper around a langfuse Trace."""

    def __init__(self, lf_trace: Any):
        self._trace = lf_trace
        self.trace_id: str = getattr(lf_trace, 'id', '')

    def span(self, name: str, input: Any = None, metadata: dict | None = None) -> SpanContext:
        try:
            kwargs: dict[str, Any] = {"name": name}
            if input is not None:
                kwargs["input"] = input
            if metadata is not None:
                kwargs["metadata"] = metadata
            lf_span = self._trace.span(**kwargs)
            return _LangfuseSpanContext(lf_span)
        except Exception:
            logger.debug("Failed to create langfuse span", exc_info=True)
            from harness.observability.noop_backend import NoopSpanContext
            return NoopSpanContext()

    def generation(self, name: str, model: str = "", input: Any = None,
                   model_parameters: dict | None = None) -> GenerationContext:
        try:
            kwargs: dict[str, Any] = {"name": name}
            if model:
                kwargs["model"] = model
            if input is not None:
                kwargs["input"] = input
            if model_parameters is not None:
                kwargs["model_parameters"] = model_parameters
            lf_gen = self._trace.generation(**kwargs)
            return _LangfuseGenerationContext(lf_gen)
        except Exception:
            logger.debug("Failed to create langfuse generation", exc_info=True)
            from harness.observability.noop_backend import NoopGenerationContext
            return NoopGenerationContext()

    def event(self, name: str, input: Any = None, output: Any = None,
              level: str = "DEFAULT") -> None:
        try:
            self._trace.event(name=name, input=input, output=output, level=level)
        except Exception:
            logger.debug("Failed to create langfuse trace event", exc_info=True)

    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata is not None:
                kwargs["metadata"] = metadata
            self._trace.update(**kwargs)
        except Exception:
            logger.debug("Failed to update/end langfuse trace", exc_info=True)


class LangfuseBackend(ObservabilityBackend):
    """Langfuse observability backend.

    Wraps the langfuse Python SDK. When the SDK is not installed,
    ``init_backend()`` falls back to NoopBackend.
    """

    def __init__(self, public_key: str, secret_key: str, host: str,
                 release: str = "", environment: str = "development"):
        import langfuse
        self._client = langfuse.Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            release=release,
            environment=environment,
        )

    def create_trace(self, name: str, session_id: str = "", user_id: str = "",
                     tags: list[str] | None = None, metadata: dict | None = None,
                     input: Any = None) -> TraceContext:
        kwargs: dict[str, Any] = {"name": name}
        if session_id:
            kwargs["session_id"] = session_id
        if user_id:
            kwargs["user_id"] = user_id
        if tags:
            kwargs["tags"] = tags
        if metadata:
            kwargs["metadata"] = metadata
        if input is not None:
            kwargs["input"] = input
        lf_trace = self._client.trace(**kwargs)
        return _LangfuseTraceContext(lf_trace)

    def get_current_trace_id(self) -> str | None:
        try:
            return self._client.get_current_trace_id()
        except Exception:
            return None

    def log_score(self, score: ScoreConfig) -> None:
        try:
            kwargs: dict[str, Any] = {
                "name": score.name,
                "value": score.value,
                "data_type": score.data_type.value,
            }
            if score.comment:
                kwargs["comment"] = score.comment
            if score.trace_id:
                kwargs["trace_id"] = score.trace_id
            if score.observation_id:
                kwargs["observation_id"] = score.observation_id
            self._client.score(**kwargs)
        except Exception:
            logger.debug("Failed to log langfuse score", exc_info=True)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:
            logger.debug("Failed to flush langfuse", exc_info=True)

    async def aflush(self) -> None:
        self.flush()

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:
            logger.debug("Failed to shutdown langfuse", exc_info=True)
