"""Tests for the evaluation package."""

import pytest

from harness.eval.metrics import MemoryMetricSuite, EvalResult, RAGAS_AVAILABLE
from harness.eval.runner import EvalRunner, EvalReport
from harness.eval.reporter import EvalReporter


# ===================================================================
# TestEvalResult
# ===================================================================
class TestEvalResult:
    def test_create(self):
        r = EvalResult(metric_name="faithfulness", value=0.85, reason="ok", sample_id="s1")
        assert r.metric_name == "faithfulness"
        assert r.value == 0.85
        assert r.reason == "ok"


# ===================================================================
# TestMemoryMetricSuite
# ===================================================================
class TestMemoryMetricSuite:
    def test_without_llm_unavailable(self):
        suite = MemoryMetricSuite(llm=None)
        assert not suite.is_available

    def test_list_metric_names(self):
        names = MemoryMetricSuite.list_metric_names()
        assert "retrieval" in names
        assert "storage" in names
        assert "impact" in names
        assert "context_precision" in names["retrieval"]
        assert "context_recall" in names["retrieval"]
        assert "faithfulness" in names["storage"]
        assert "answer_correctness" in names["impact"]

    def test_empty_metrics_when_unavailable(self):
        suite = MemoryMetricSuite(llm=None)
        assert suite.get_retrieval_metrics() == []
        assert suite.get_storage_metrics() == []
        assert suite.get_impact_metrics() == []
        assert suite.get_all() == []

    @pytest.mark.skipif(not RAGAS_AVAILABLE, reason="ragas not installed")
    def test_with_llm_available(self):
        # Only runs when ragas is installed
        from unittest.mock import MagicMock
        mock_llm = MagicMock()
        suite = MemoryMetricSuite(llm=mock_llm)
        assert suite.is_available
        assert len(suite.get_all()) == 5


# ===================================================================
# TestEvalRunner
# ===================================================================
class TestEvalRunner:
    @pytest.fixture
    def suite(self):
        return MemoryMetricSuite(llm=None)

    def test_run_with_no_samples(self, suite):
        runner = EvalRunner(suite)
        # Should not crash with empty samples
        assert not suite.is_available

    @pytest.mark.asyncio
    async def test_empty_retrieval_eval(self, suite):
        runner = EvalRunner(suite)
        results = await runner.run_retrieval_eval([])
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_storage_eval(self, suite):
        runner = EvalRunner(suite)
        results = await runner.run_storage_eval([])
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_impact_eval(self, suite):
        runner = EvalRunner(suite)
        results = await runner.run_impact_eval([])
        assert results == []


# ===================================================================
# TestEvalReporter
# ===================================================================
class TestEvalReporter:
    def test_format_table_empty(self):
        report = EvalReport(dimension="retrieval")
        text = EvalReporter.format_table(report)
        assert "retrieval" in text

    def test_format_table_with_results(self):
        results = [
            EvalResult(metric_name="faithfulness", value=0.9),
            EvalResult(metric_name="faithfulness", value=0.8),
        ]
        summary = {"count": 2, "mean": 0.85, "min": 0.8, "max": 0.9}
        report = EvalReport(dimension="storage", results=results, summary=summary)
        text = EvalReporter.format_table(report)
        assert "storage" in text
        assert "faithfulness" in text

    def test_to_dict(self):
        results = [EvalResult(metric_name="test", value=0.75, reason="ok", sample_id="s1")]
        report = EvalReport(
            dimension="impact",
            results=results,
            summary={"count": 1, "mean": 0.75},
            trace_url="http://langfuse/trace/1",
        )
        data = EvalReporter.to_dict(report)
        assert data["dimension"] == "impact"
        assert len(data["results"]) == 1
        assert data["results"][0]["metric"] == "test"
        assert data["trace_url"] == "http://langfuse/trace/1"

    def test_write_json(self, tmp_path):
        report = EvalReport(dimension="retrieval", summary={"count": 0})
        path = tmp_path / "report.json"
        EvalReporter.write_json(report, path)
        assert path.exists()
        data = __import__("json").loads(path.read_text())
        assert data["dimension"] == "retrieval"
