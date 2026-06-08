"""Abstract backend interface for observability providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScoreDataType(Enum):
    NUMERIC = "NUMERIC"
    CATEGORICAL = "CATEGORICAL"
    BOOLEAN = "BOOLEAN"
    TEXT = "TEXT"


@dataclass
class ScoreConfig:
    name: str
    value: float
    data_type: ScoreDataType = ScoreDataType.NUMERIC
    comment: str = ""
    trace_id: str = ""
    observation_id: str = ""


class TraceContext(ABC):
    """Context manager for a trace — created by ObservabilityBackend.create_trace()."""

    trace_id: str = ""

    @abstractmethod
    def span(self, name: str, input: Any = None, metadata: dict | None = None) -> "SpanContext":
        ...

    @abstractmethod
    def generation(self, name: str, model: str = "", input: Any = None,
                   model_parameters: dict | None = None) -> "GenerationContext":
        ...

    @abstractmethod
    def event(self, name: str, input: Any = None, output: Any = None,
              level: str = "DEFAULT") -> None:
        ...

    @abstractmethod
    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        ...


class SpanContext(ABC):
    """Context manager for a span (observation with duration)."""

    span_id: str = ""

    @abstractmethod
    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        ...

    @abstractmethod
    def event(self, name: str, input: Any = None, output: Any = None) -> None:
        ...


class GenerationContext(ABC):
    """Context manager for an LLM generation span."""

    @abstractmethod
    def end(self, output: Any = None, usage: dict | None = None,
            model: str = "") -> None:
        ...


class ObservabilityBackend(ABC):
    """Abstract interface for observability backends (langfuse, noop, future harness-native)."""

    @abstractmethod
    def create_trace(self, name: str, session_id: str = "", user_id: str = "",
                     tags: list[str] | None = None, metadata: dict | None = None,
                     input: Any = None) -> TraceContext:
        ...

    @abstractmethod
    def get_current_trace_id(self) -> str | None:
        ...

    @abstractmethod
    def log_score(self, score: ScoreConfig) -> None:
        ...

    @abstractmethod
    def flush(self) -> None:
        ...

    @abstractmethod
    async def aflush(self) -> None:
        ...

    @abstractmethod
    def shutdown(self) -> None:
        ...
