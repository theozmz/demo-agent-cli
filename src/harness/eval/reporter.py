"""EvalReporter — formats evaluation results for console and file output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.eval.runner import EvalReport
from harness.eval.metrics import EvalResult


class EvalReporter:
    """Formats and writes evaluation reports."""

    @staticmethod
    def format_table(report: EvalReport) -> str:
        """Format results as a readable text table."""
        lines = [f"=== Memory Evaluation: {report.dimension} ===", ""]
        if report.summary:
            lines.append("Summary:")
            for k, v in report.summary.items():
                lines.append(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
            lines.append("")
        if report.results:
            lines.append("Results by metric:")
            by_metric: dict[str, list[float]] = {}
            for r in report.results:
                by_metric.setdefault(r.metric_name, []).append(r.value)
            for name, values in sorted(by_metric.items()):
                avg = sum(values) / len(values) if values else 0.0
                lines.append(f"  {name}: avg={avg:.4f} (n={len(values)})")
        if report.trace_url:
            lines.append(f"\nLangfuse trace: {report.trace_url}")
        return "\n".join(lines)

    @staticmethod
    def to_dict(report: EvalReport) -> dict[str, Any]:
        """Convert report to JSON-serializable dict."""
        return {
            "dimension": report.dimension,
            "summary": report.summary,
            "results": [
                {
                    "metric": r.metric_name,
                    "value": r.value,
                    "reason": r.reason,
                    "sample_id": r.sample_id,
                }
                for r in report.results
            ],
            "trace_url": report.trace_url,
        }

    @staticmethod
    def write_json(report: EvalReport, path: str | Path) -> None:
        """Write report to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = EvalReporter.to_dict(report)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
