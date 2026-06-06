"""Tests for credit-attribution framework (Li et al., 2026).

Covers:
- SignalGranularity and AttributionDimension enums
- LoopEvent with granularity/attribution tags
- TaskLogger attribution and compaction methods
- analyze_attribution script logic
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from harness.core.loop import (
    LoopEvent,
    SignalGranularity,
    AttributionDimension,
)
from harness.logging.task_logger import TaskLogger


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestSignalGranularity:
    """SignalGranularity enum maps to G0–G3 per paper taxonomy."""

    def test_g0_is_outcome_scalar(self):
        assert SignalGranularity.G0.value == "G0"

    def test_g1_is_process_text(self):
        assert SignalGranularity.G1.value == "G1"

    def test_g2_is_component_attributed(self):
        assert SignalGranularity.G2.value == "G2"

    def test_g3_is_harness_cross_dim(self):
        assert SignalGranularity.G3.value == "G3"

    def test_ordering(self):
        """G0 < G1 < G2 < G3 (outcome → harness)."""
        members = list(SignalGranularity)
        assert members == [
            SignalGranularity.G0,
            SignalGranularity.G1,
            SignalGranularity.G2,
            SignalGranularity.G3,
        ]


class TestAttributionDimension:
    """AttributionDimension maps to P/S/M context variables."""

    def test_p_is_prompt(self):
        assert AttributionDimension.PROMPT.value == "P"

    def test_s_is_structural(self):
        assert AttributionDimension.STRUCTURAL.value == "S"

    def test_m_is_memory(self):
        assert AttributionDimension.MEMORY.value == "M"


# ---------------------------------------------------------------------------
# LoopEvent tests
# ---------------------------------------------------------------------------


class TestLoopEventAttribution:
    """LoopEvent carries signal_granularity and attribution fields."""

    def test_defaults(self):
        ev = LoopEvent(kind="done")
        assert ev.signal_granularity == SignalGranularity.G0
        assert ev.attribution == AttributionDimension.STRUCTURAL

    def test_g1_thinking_event(self):
        ev = LoopEvent(
            kind="thinking",
            signal_granularity=SignalGranularity.G1,
            attribution=AttributionDimension.PROMPT,
            iteration=3,
        )
        assert ev.kind == "thinking"
        assert ev.signal_granularity == SignalGranularity.G1
        assert ev.attribution == AttributionDimension.PROMPT
        assert ev.iteration == 3

    def test_g2_tool_call_event(self):
        ev = LoopEvent(
            kind="tool_call",
            tool_name="file_write",
            tool_input={"path": "test.py", "content": "x=1"},
            signal_granularity=SignalGranularity.G2,
            attribution=AttributionDimension.STRUCTURAL,
        )
        assert ev.signal_granularity == SignalGranularity.G2
        assert ev.attribution == AttributionDimension.STRUCTURAL
        assert ev.tool_name == "file_write"

    def test_g3_retry_event(self):
        ev = LoopEvent(
            kind="retry",
            retry_attempt=2,
            retry_error="connection timeout",
            signal_granularity=SignalGranularity.G3,
            attribution=AttributionDimension.MEMORY,
        )
        assert ev.signal_granularity == SignalGranularity.G3
        assert ev.attribution == AttributionDimension.MEMORY
        assert ev.retry_attempt == 2

    def test_g0_outcome_event(self):
        ev = LoopEvent(
            kind="done",
            content="ok",
            signal_granularity=SignalGranularity.G0,
            attribution=AttributionDimension.PROMPT,
        )
        assert ev.signal_granularity == SignalGranularity.G0
        assert ev.attribution == AttributionDimension.PROMPT

    def test_compact_event_g3(self):
        ev = LoopEvent(
            kind="compact",
            content="micro",
            signal_granularity=SignalGranularity.G3,
            attribution=AttributionDimension.MEMORY,
        )
        assert ev.signal_granularity == SignalGranularity.G3
        assert ev.attribution == AttributionDimension.MEMORY
        assert ev.content == "micro"

    def test_field_independence(self):
        """Granularity and attribution are independent dimensions."""
        for gran in SignalGranularity:
            for attr in AttributionDimension:
                ev = LoopEvent(
                    kind="test",
                    signal_granularity=gran,
                    attribution=attr,
                )
                assert ev.signal_granularity == gran
                assert ev.attribution == attr


# ---------------------------------------------------------------------------
# TaskLogger attribution tests
# ---------------------------------------------------------------------------


class TestTaskLoggerAttribution:
    """TaskLogger emits structured attribution + compaction events."""

    @pytest.fixture
    def log_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    def test_log_attribution_emits_jsonl(self, log_path):
        tl = TaskLogger(session_id="test-attr-1", log_dir=log_path)
        tl.log_attribution(
            dimension="S", granularity="G2",
            event_kind="tool_result", tool_name="bash_exec",
            iteration=5, detail="Compilation failed",
        )
        tl.close()

        lines = (log_path / "test-attr-1.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "attribution"
        assert rec["dimension"] == "S"
        assert rec["granularity"] == "G2"
        assert rec["tool_name"] == "bash_exec"
        assert rec["iteration"] == 5

    def test_log_attribution_all_dimensions(self, log_path):
        tl = TaskLogger(session_id="test-all-dims", log_dir=log_path)
        tl.log_attribution(dimension="P", granularity="G1",
                           event_kind="thinking", detail="prompt assembled")
        tl.log_attribution(dimension="S", granularity="G2",
                           event_kind="tool_call", detail="tool invoked")
        tl.log_attribution(dimension="M", granularity="G3",
                           event_kind="retry", detail="compaction triggered")
        tl.close()

        lines = (log_path / "test-all-dims.jsonl").read_text().strip().split("\n")
        dims = [json.loads(l)["dimension"] for l in lines]
        assert dims == ["P", "S", "M"]

    def test_log_compaction_emits_g3(self, log_path):
        tl = TaskLogger(session_id="test-compact", log_dir=log_path)
        tl.log_compaction(
            strategy="micro",
            tokens_before=150000,
            tokens_after=120000,
            truncated_count=15,
            iteration=12,
        )
        tl.close()

        lines = (log_path / "test-compact.jsonl").read_text().strip().split("\n")
        rec = json.loads(lines[0])
        assert rec["event"] == "compaction"
        assert rec["strategy"] == "micro"
        assert rec["tokens_before"] == 150000
        assert rec["tokens_after"] == 120000
        assert rec["truncated_count"] == 15
        assert rec["dimension"] == "M"
        assert rec["granularity"] == "G3"

    def test_log_event_summary(self, log_path):
        tl = TaskLogger(session_id="test-summary", log_dir=log_path)
        tl.log_event_summary(
            p_count=10, s_count=45, m_count=5,
            g0_count=3, g1_count=30, g2_count=20, g3_count=7,
        )
        tl.close()

        lines = (log_path / "test-summary.jsonl").read_text().strip().split("\n")
        rec = json.loads(lines[0])
        assert rec["event"] == "event_summary"
        assert rec["p_count"] == 10
        assert rec["s_count"] == 45
        assert rec["m_count"] == 5
        assert rec["g0_count"] == 3
        assert rec["g1_count"] == 30
        assert rec["g2_count"] == 20
        assert rec["g3_count"] == 7

    def test_multiple_attributions_in_session(self, log_path):
        """Simulate a realistic session with mixed attribution events."""
        tl = TaskLogger(session_id="realistic", log_dir=log_path)

        # G0 — task start/end
        tl.log_task_start("write a fib function", provider="anthropic",
                          model="claude-sonnet", max_turns=10)
        tl.log_attribution(dimension="S", granularity="G0",
                           event_kind="task_start", detail="Session started")

        # G1 — thinking (prompt)
        tl.log_attribution(dimension="P", granularity="G1",
                           event_kind="thinking", iteration=1)

        # G2 — tool call (structural)
        tl.log_tool_call(tool_name="file_write", params={"path": "fib.py"},
                         is_error=False, result_summary="ok", duration_ms=12)
        tl.log_attribution(dimension="S", granularity="G2",
                           event_kind="tool_result", tool_name="file_write",
                           iteration=1)

        # G2 — another tool
        tl.log_tool_call(tool_name="bash_exec", params={"cmd": "python fib.py"},
                         is_error=False, result_summary="0 1 1 2 3 5", duration_ms=150)
        tl.log_attribution(dimension="S", granularity="G2",
                           event_kind="tool_result", tool_name="bash_exec",
                           iteration=2)

        # G3 — compaction
        tl.log_compaction(strategy="micro", tokens_before=85000,
                          tokens_after=70000, truncated_count=8, iteration=3)

        # Summary
        tl.log_event_summary(p_count=1, s_count=3, m_count=1,
                             g0_count=0, g1_count=1, g2_count=3, g3_count=1)
        tl.log_task_end(outcome="completed", turns=3,
                        total_duration_ms=1200, tokens_used=5000)
        tl.close()

        lines = (log_path / "realistic.jsonl").read_text().strip().split("\n")
        events = [json.loads(l)["event"] for l in lines]
        assert "attribution" in events
        assert "compaction" in events
        assert "event_summary" in events
        assert "task_start" in events
        assert "task_end" in events

    def test_idempotent_close(self, log_path):
        tl = TaskLogger(session_id="close-test", log_dir=log_path)
        tl.log_attribution(dimension="P", granularity="G1")
        tl.close()
        tl.close()  # should not raise
        # file should still have exactly 1 line
        lines = (log_path / "close-test.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# Analyze script: parse_session tests
# ---------------------------------------------------------------------------


class TestAnalyzeScript:
    """Verify the parse_session logic on synthetic JSONL files."""

    @pytest.fixture
    def synthetic_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "synth.jsonl"
            tl = TaskLogger(session_id="synth", log_dir=tmp)
            tl.log_task_start("build a TODO app", provider="anthropic",
                              model="claude-sonnet", max_turns=10)
            tl.log_attribution(dimension="P", granularity="G1",
                               event_kind="thinking", iteration=1)
            tl.log_tool_call(tool_name="file_write", params={"path": "todo.py"},
                             is_error=False, result_summary="ok")
            tl.log_attribution(dimension="S", granularity="G2",
                               event_kind="tool_result", tool_name="file_write")
            tl.log_tool_call(tool_name="file_read", params={"path": "todo.py"},
                             is_error=False, result_summary="code here")
            tl.log_attribution(dimension="S", granularity="G1",
                               event_kind="tool_result", tool_name="file_read")
            tl.log_tool_call(tool_name="bash_exec", params={"cmd": "pytest"},
                             is_error=True, result_summary="FAILED")
            tl.log_attribution(dimension="S", granularity="G2",
                               event_kind="tool_result", tool_name="bash_exec",
                               detail="Tests failed")
            tl.log_attribution(dimension="M", granularity="G3",
                               event_kind="retry", detail="LLM retry 1/3")
            tl.log_compaction(strategy="micro", tokens_before=80000,
                              tokens_after=65000, truncated_count=5)
            tl.log_event_summary(p_count=1, s_count=3, m_count=1,
                                 g0_count=0, g1_count=2, g2_count=2, g3_count=2)
            tl.log_task_end(outcome="completed", turns=5, tokens_used=6000)
            tl.close()
            yield log_path

    def test_parse_session_dimension_counts(self, synthetic_log):
        from scripts.analyze_attribution import parse_session
        report = parse_session(synthetic_log)
        # summary sets P=1, S=3, M=1; task_end adds +1 to S
        assert report.p_count == 1
        assert report.s_count == 4
        assert report.m_count == 1
        assert report.outcome == "completed"
        assert report.turns == 5

    def test_parse_session_granularity_counts(self, synthetic_log):
        from scripts.analyze_attribution import parse_session
        report = parse_session(synthetic_log)
        assert report.g1_count == 2
        assert report.g2_count == 2
        assert report.g3_count == 2  # 1 compaction + 1 retry attribution

    def test_parse_empty_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.jsonl"
            empty.write_text("")
            from scripts.analyze_attribution import parse_session
            report = parse_session(empty)
            assert report.total_events == 0

    def test_parse_log_with_malformed_lines(self, synthetic_log):
        """Should skip malformed JSON lines without crashing."""
        with open(synthetic_log, "a") as f:
            f.write("this is not json\n")
            f.write('{"event": "bad", missing quote}\n')
        from scripts.analyze_attribution import parse_session
        report = parse_session(synthetic_log)
        # Should still have the original valid events
        assert report.total_events > 0

    def test_cross_session_summary(self, synthetic_log):
        """Verify the summary function works with a single session."""
        from scripts.analyze_attribution import parse_session, print_summary
        import io
        report = parse_session(synthetic_log)
        # Redirect stdout to capture
        import sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            print_summary([report])
            output = sys.stdout.getvalue()
            assert "CROSS-SESSION SUMMARY" in output
            assert "synth" in output
        finally:
            sys.stdout = old_stdout
