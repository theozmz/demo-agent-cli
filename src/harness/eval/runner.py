"""EvalRunner — orchestrates ragas evaluation runs with langfuse tracing.

Each evaluation run creates a dedicated langfuse trace. Each metric computation
is wrapped in a span, and results are logged as Scores for full traceability.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.eval.metrics import MemoryMetricSuite, EvalResult, RAGAS_AVAILABLE
from harness.observability import get_backend, ScoreConfig, ScoreDataType

logger = logging.getLogger(__name__)


@dataclass
class EvalReport:
    dimension: str
    results: list[EvalResult] = field(default_factory=list)
    summary: dict[str, float] = field(default_factory=dict)
    trace_url: str = ""


class EvalRunner:
    """Runs ragas evaluation on memory operations with langfuse tracing."""

    def __init__(self, metric_suite: MemoryMetricSuite):
        self._suite = metric_suite
        self._backend = get_backend()

    async def run_retrieval_eval(self, samples: list) -> list[EvalResult]:
        """Evaluate memory retrieval quality."""
        if not self._suite.is_available:
            logger.warning("Ragas not available — skipping retrieval eval")
            return []
        metrics = self._suite.get_retrieval_metrics()
        if not metrics:
            return []
        trace = self._backend.create_trace(
            name="eval_memory_retrieval",
            tags=["evaluation", "memory", "retrieval"],
        )
        results = await self._evaluate_samples(trace, samples, metrics)
        trace.end(output={"metric_count": len(results), "sample_count": len(samples)})
        return results

    async def run_storage_eval(self, samples: list) -> list[EvalResult]:
        """Evaluate memory storage quality (faithfulness)."""
        if not self._suite.is_available:
            logger.warning("Ragas not available — skipping storage eval")
            return []
        metrics = self._suite.get_storage_metrics()
        if not metrics:
            return []
        trace = self._backend.create_trace(
            name="eval_memory_storage",
            tags=["evaluation", "memory", "storage"],
        )
        results = await self._evaluate_samples(trace, samples, metrics)
        trace.end(output={"metric_count": len(results), "sample_count": len(samples)})
        return results

    async def run_impact_eval(self, samples: list) -> list[EvalResult]:
        """Evaluate memory impact on agent responses."""
        if not self._suite.is_available:
            logger.warning("Ragas not available — skipping impact eval")
            return []
        metrics = self._suite.get_impact_metrics()
        if not metrics:
            return []
        trace = self._backend.create_trace(
            name="eval_memory_impact",
            tags=["evaluation", "memory", "impact"],
        )
        results = await self._evaluate_samples(trace, samples, metrics)
        trace.end(output={"metric_count": len(results), "sample_count": len(samples)})
        return results

    async def run_full(self, samples: dict[str, list]) -> EvalReport:
        """Run all three evaluation dimensions.

        Args:
            samples: dict with keys "retrieval", "storage", "impact" mapping to sample lists.

        Returns:
            Combined EvalReport.
        """
        all_results: list[EvalResult] = []
        retrieval = await self.run_retrieval_eval(samples.get("retrieval", []))
        storage = await self.run_storage_eval(samples.get("storage", []))
        impact = await self.run_impact_eval(samples.get("impact", []))
        all_results = retrieval + storage + impact
        return self._build_report("full", all_results)

    async def _evaluate_samples(self, trace, samples: list, metrics: list) -> list[EvalResult]:
        """Evaluate a batch of samples against a set of metrics."""
        results: list[EvalResult] = []
        for sample in samples:
            sample_results = await self._evaluate_sample(trace, sample, metrics)
            results.extend(sample_results)
        return results

    async def _evaluate_sample(self, trace, sample, metrics: list) -> list[EvalResult]:
        """Evaluate a single sample against all metrics."""
        results: list[EvalResult] = []
        sample_id = str(hash(json.dumps(str(sample), sort_keys=True)))

        for metric in metrics:
            span = trace.span(
                name=f"metric.{getattr(metric, 'name', metric.__class__.__name__)}",
                input={"sample_id": sample_id},
            )
            start = time.monotonic()
            try:
                score = await metric.ascore(sample)
                duration_ms = (time.monotonic() - start) * 1000
                span.end(
                    output={"value": score.value},
                    metadata={"duration_ms": duration_ms},
                )
                metric_name = getattr(metric, 'name', metric.__class__.__name__)
                self._backend.log_score(ScoreConfig(
                    name=f"memory.{metric_name}",
                    value=float(score.value) if score.value is not None else 0.0,
                    data_type=ScoreDataType.NUMERIC,
                    trace_id=trace.trace_id,
                    comment=getattr(score, 'reason', '') or '',
                ))
                results.append(EvalResult(
                    metric_name=metric_name,
                    value=float(score.value) if score.value is not None else 0.0,
                    reason=getattr(score, 'reason', '') or '',
                    sample_id=sample_id,
                ))
            except Exception as exc:
                span.end(output={"error": str(exc)}, metadata={"status": "error"})
                logger.warning("Metric %s failed for sample %s: %s",
                               getattr(metric, 'name', metric.__class__.__name__),
                               sample_id, exc)

        return results

    def _build_report(self, dimension: str, results: list[EvalResult]) -> EvalReport:
        values = [r.value for r in results]
        summary: dict[str, float] = {}
        if values:
            summary = {
                "count": len(values),
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
            }
        return EvalReport(dimension=dimension, results=results, summary=summary)
