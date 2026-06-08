"""Tests for the observability package."""

import pytest

from harness.config.config import ObservabilityConfig
from harness.observability import (
    get_backend,
    init_backend,
    shutdown_backend,
    NoopBackend,
    ScoreConfig,
    ScoreDataType,
)


# ===================================================================
# TestScoreConfig
# ===================================================================
class TestScoreConfig:
    def test_numeric_score(self):
        score = ScoreConfig(name="test", value=0.95)
        assert score.name == "test"
        assert score.value == 0.95
        assert score.data_type == ScoreDataType.NUMERIC

    def test_categorical_score(self):
        score = ScoreConfig(name="category", value=1.0, data_type=ScoreDataType.CATEGORICAL)
        assert score.data_type == ScoreDataType.CATEGORICAL

    def test_with_comment(self):
        score = ScoreConfig(name="m", value=0.5, comment="good", trace_id="t1")
        assert score.comment == "good"
        assert score.trace_id == "t1"


# ===================================================================
# TestObservabilityConfig
# ===================================================================
class TestObservabilityConfig:
    def test_defaults(self):
        cfg = ObservabilityConfig()
        assert cfg.backend == "none"
        assert cfg.langfuse_public_key == ""
        assert cfg.langfuse_secret_key == ""
        assert cfg.langfuse_host == ""
        assert cfg.eval_llm_model == "gpt-4o-mini"
        assert cfg.eval_llm_provider == "openai"

    def test_langfuse_fields(self):
        cfg = ObservabilityConfig(
            backend="langfuse",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
            langfuse_host="http://localhost:3000",
        )
        assert cfg.backend == "langfuse"
        assert cfg.langfuse_public_key == "pk-test"
        assert cfg.langfuse_secret_key == "sk-test"


# ===================================================================
# TestNoopBackend
# ===================================================================
class TestNoopBackend:
    @pytest.fixture
    def backend(self):
        return NoopBackend()

    def test_create_trace_returns_noop(self, backend):
        trace = backend.create_trace(name="test")
        assert trace.trace_id == ""

    def test_span_is_noop(self, backend):
        trace = backend.create_trace(name="test")
        span = trace.span(name="op")
        span.end(output={"x": 1})
        # No exceptions raised

    def test_generation_is_noop(self, backend):
        trace = backend.create_trace(name="test")
        gen = trace.generation(name="llm", model="gpt-4")
        gen.end(output={"text": "hi"}, usage={"input": 10, "output": 5})

    def test_log_score_is_noop(self, backend):
        backend.log_score(ScoreConfig(name="s", value=0.5))
        # No exceptions

    def test_flush_is_noop(self, backend):
        backend.flush()
        # No exceptions

    @pytest.mark.asyncio
    async def test_aflush_is_noop(self, backend):
        await backend.aflush()

    def test_current_trace_id_is_none(self, backend):
        assert backend.get_current_trace_id() is None

    def test_shutdown_is_noop(self, backend):
        backend.shutdown()


# ===================================================================
# TestBackendSingleton
# ===================================================================
class TestBackendSingleton:
    def teardown_method(self):
        # Restore to NoopBackend after each test
        init_backend(ObservabilityConfig())

    def test_default_is_noop(self):
        backend = get_backend()
        assert isinstance(backend, NoopBackend)

    def test_init_none_config_stays_noop(self):
        cfg = ObservabilityConfig(backend="none")
        backend = init_backend(cfg)
        assert isinstance(backend, NoopBackend)
        assert isinstance(get_backend(), NoopBackend)

    def test_init_langfuse_without_sdk_falls_back(self):
        cfg = ObservabilityConfig(backend="langfuse")
        # Without langfuse SDK installed, should fall back to NoopBackend
        backend = init_backend(cfg)
        assert isinstance(backend, NoopBackend)

    @pytest.mark.asyncio
    async def test_shutdown_backend(self):
        await shutdown_backend()
        assert isinstance(get_backend(), NoopBackend)
