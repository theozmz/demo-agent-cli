"""NoopBackend — zero-overhead stub when observability is disabled."""

from __future__ import annotations

from typing import Any

from harness.observability.backend import (
    ObservabilityBackend,
    TraceContext,
    SpanContext,
    GenerationContext,
    ScoreConfig,
)


class NoopSpanContext(SpanContext):
    span_id: str = ""

    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        pass

    def event(self, name: str, input: Any = None, output: Any = None) -> None:
        pass


class NoopGenerationContext(GenerationContext):
    def end(self, output: Any = None, usage: dict | None = None,
            model: str = "") -> None:
        pass


class NoopTraceContext(TraceContext):
    trace_id: str = ""

    def span(self, name: str, input: Any = None, metadata: dict | None = None) -> SpanContext:
        return NoopSpanContext()

    def generation(self, name: str, model: str = "", input: Any = None,
                   model_parameters: dict | None = None) -> GenerationContext:
        return NoopGenerationContext()

    def event(self, name: str, input: Any = None, output: Any = None,
              level: str = "DEFAULT") -> None:
        pass

    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        pass


class NoopBackend(ObservabilityBackend):
    """Default backend — does nothing, zero overhead."""

    def create_trace(self, name: str, session_id: str = "", user_id: str = "",
                     tags: list[str] | None = None, metadata: dict | None = None,
                     input: Any = None) -> TraceContext:
        return NoopTraceContext()

    def get_current_trace_id(self) -> str | None:
        return None

    def log_score(self, score: ScoreConfig) -> None:
        pass

    def flush(self) -> None:
        pass

    async def aflush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass
