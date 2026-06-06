#!/usr/bin/env python3
"""Credit-attribution analysis script for Harness session logs.

Reads JSONL session logs from ``logs/`` and produces a credit-assignment
report per Li et al. (2026) — "Who Gets the Credit? Prompt, Structural,
and Memory Context Optimization for Agent Harnesses."

The report breaks down each session's events across:
- Context dimensions (P/S/M): prompt, structural, memory.
- Feedback granularity (G0–G3): outcome scalar, process text,
  component-attributed, cross-dimensional harness signal.

Usage::

    python scripts/analyze_attribution.py              # all sessions
    python scripts/analyze_attribution.py logs/abc.jsonl  # single session
    python scripts/analyze_attribution.py --summary-only  # just summaries
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class SessionReport:
    """Aggregated credit-assignment data for one session."""

    session_id: str = ""
    file_path: str = ""
    total_events: int = 0
    # P/S/M counts
    p_count: int = 0
    s_count: int = 0
    m_count: int = 0
    # G0–G3 counts
    g0_count: int = 0
    g1_count: int = 0
    g2_count: int = 0
    g3_count: int = 0
    # Derived
    outcome: str = ""
    turns: int = 0
    tokens_used: int = 0
    errors: list[str] = field(default_factory=list)
    compaction_events: list[dict] = field(default_factory=list)
    # Cross-tab: P/S/M × G
    cross_tab: dict = field(default_factory=lambda: defaultdict(Counter))
    # Tool failures by name
    tool_errors: Counter = field(default_factory=Counter)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_session(file_path: Path) -> SessionReport:
    """Parse a single JSONL session log into a SessionReport."""
    report = SessionReport(file_path=str(file_path))

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            event = rec.get("event", "")
            report.total_events += 1

            # ---- event_summary: fast path ----
            if event == "event_summary":
                report.p_count = rec.get("p_count", 0)
                report.s_count = rec.get("s_count", 0)
                report.m_count = rec.get("m_count", 0)
                report.g0_count = rec.get("g0_count", 0)
                report.g1_count = rec.get("g1_count", 0)
                report.g2_count = rec.get("g2_count", 0)
                report.g3_count = rec.get("g3_count", 0)
                continue

            # ---- attribution events ----
            if event == "attribution":
                dim = rec.get("dimension", "")
                gran = rec.get("granularity", "")
                _count_dim(report, dim)
                _count_gran(report, gran)
                report.cross_tab[dim][gran] += 1
                continue

            # ---- compaction events (always G3/M) ----
            if event == "compaction":
                report.m_count += 1
                report.g3_count += 1
                report.cross_tab["M"]["G3"] += 1
                report.compaction_events.append(rec)
                continue

            # ---- task_end (G0 outcome) — always counted ----
            if event == "task_end":
                report.outcome = rec.get("outcome", "")
                report.turns = rec.get("turns", 0)
                report.tokens_used = rec.get("tokens_used", 0)
                report.g0_count += 1
                report.cross_tab["S"]["G0"] += 1
                report.s_count += 1
                continue

            # ---- tool_call / error: fallback when no explicit ----
            # attribution events are present (pre-summary compat) ----
            if event == "tool_call":
                tool_name = rec.get("tool_name", "")
                is_error = rec.get("is_error", False)
                if is_error:
                    report.tool_errors[tool_name] += 1
                # Only count if no attribution events exist (avoid double-count)
                if report.p_count + report.s_count + report.m_count == 0:
                    if tool_name in ("file_write", "file_edit", "bash_exec", "memory_write", "memory_delete"):
                        report.g2_count += 1
                    else:
                        report.g1_count += 1
                    report.s_count += 1
                    report.cross_tab["S"]["G1" if tool_name not in ("file_write", "file_edit", "bash_exec", "memory_write", "memory_delete") else "G2"] += 1
                continue

            # ---- error events (fallback G2/S) ----
            if event == "error":
                msg = rec.get("message", "")
                if msg:
                    report.errors.append(msg)
                if report.p_count + report.s_count + report.m_count == 0:
                    report.g2_count += 1
                    report.s_count += 1
                    report.cross_tab["S"]["G2"] += 1
                continue

    # Derive session ID from filename
    report.session_id = file_path.stem

    return report


def _count_dim(report: SessionReport, dim: str) -> None:
    if dim == "P":
        report.p_count += 1
    elif dim == "S":
        report.s_count += 1
    elif dim == "M":
        report.m_count += 1


def _count_gran(report: SessionReport, gran: str) -> None:
    if gran == "G0":
        report.g0_count += 1
    elif gran == "G1":
        report.g1_count += 1
    elif gran == "G2":
        report.g2_count += 1
    elif gran == "G3":
        report.g3_count += 1


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_WIDTH = 68
_SEP = "─" * _WIDTH
_THIN = "·" * _WIDTH


def print_header(title: str) -> None:
    print(f"\n{_SEP}")
    print(f"  {title}")
    print(f"{_SEP}")


def print_report(report: SessionReport) -> None:
    """Pretty-print a single session's credit-assignment report."""
    print(f"\n{'─' * _WIDTH}")
    print(f"  Session: {report.session_id}")
    print(f"  File:   {report.file_path}")
    print(f"{'─' * _WIDTH}")

    # Outcome
    outcome_label = report.outcome or "(unknown)"
    print(f"\n  Outcome:    {outcome_label}")
    print(f"  Turns:      {report.turns}")
    print(f"  Tokens:     {report.tokens_used:,}")
    print(f"  Events:     {report.total_events}")

    # Dimension breakdown
    total_dim = report.p_count + report.s_count + report.m_count or 1
    print(f"\n  ┌─ Context Dimension (who gets the credit?) ─┐")
    _bar("P (Prompt)", report.p_count, total_dim)
    _bar("S (Structure)", report.s_count, total_dim)
    _bar("M (Memory)", report.m_count, total_dim)
    print(f"  └{'─' * 44}┘")

    # Granularity breakdown
    total_gran = report.g0_count + report.g1_count + report.g2_count + report.g3_count or 1
    print(f"\n  ┌─ Feedback Granularity ─────────────────────┐")
    _bar("G0 (Outcome scalar)", report.g0_count, total_gran)
    _bar("G1 (Process text)", report.g1_count, total_gran)
    _bar("G2 (Component-attrib)", report.g2_count, total_gran)
    _bar("G3 (Harness cross-dim)", report.g3_count, total_gran)
    print(f"  └{'─' * 44}┘")

    # Cross-tab: P/S/M × G
    print(f"\n  ┌─ Cross-tab: P/S/M × G ────────────────────┐")
    for dim in ("P", "S", "M"):
        row = [f"{dim}"]
        for gran in ("G0", "G1", "G2", "G3"):
            row.append(str(report.cross_tab.get(dim, {}).get(gran, 0)).rjust(5))
        print(f"  │  {'  '.join(row)}                          │")
    print(f"  └{'─' * 44}┘")

    # Tool errors
    if report.tool_errors:
        print(f"\n  Tool errors: {dict(report.tool_errors)}")

    # Compaction profile
    if report.compaction_events:
        print(f"\n  Compaction events: {len(report.compaction_events)}")
        for ce in report.compaction_events:
            print(f"    - {ce.get('strategy','?'):10s}  "
                  f"{ce.get('tokens_before',0):>6d} → {ce.get('tokens_after',0):>6d} tokens  "
                  f"({ce.get('truncated_count',0)} stubbed)")

    # Errors
    if report.errors:
        print(f"\n  Errors ({len(report.errors)}):")
        for err in report.errors[:5]:
            print(f"    - {err[:120]}")


def _bar(label: str, count: int, total: int) -> None:
    pct = count / total * 100 if total else 0
    bar_len = int(pct / 2.5)  # 40 chars max
    bar = "█" * bar_len + "░" * (40 - bar_len)
    print(f"  │ {label:<20s} {bar} {pct:5.1f}% ({count}) │")


def print_summary(reports: list[SessionReport]) -> None:
    """Aggregate summary across all sessions."""
    if not reports:
        print("No session logs found.")
        return

    print_header("CROSS-SESSION SUMMARY")
    print(f"  Sessions analyzed: {len(reports)}")

    total_p = sum(r.p_count for r in reports)
    total_s = sum(r.s_count for r in reports)
    total_m = sum(r.m_count for r in reports)
    total_dim = total_p + total_s + total_m or 1

    print(f"\n  ┌─ Aggregate Dimension Attribution ──────────┐")
    _bar("P (Prompt)", total_p, total_dim)
    _bar("S (Structure)", total_s, total_dim)
    _bar("M (Memory)", total_m, total_dim)
    print(f"  └{'─' * 44}┘")

    total_g0 = sum(r.g0_count for r in reports)
    total_g1 = sum(r.g1_count for r in reports)
    total_g2 = sum(r.g2_count for r in reports)
    total_g3 = sum(r.g3_count for r in reports)
    total_gran = total_g0 + total_g1 + total_g2 + total_g3 or 1

    print(f"\n  ┌─ Aggregate Granularity Distribution ───────┐")
    _bar("G0 (Outcome)", total_g0, total_gran)
    _bar("G1 (Process)", total_g1, total_gran)
    _bar("G2 (Component)", total_g2, total_gran)
    _bar("G3 (Harness)", total_g3, total_gran)
    print(f"  └{'─' * 44}┘")

    # Outcome statistics
    completed = sum(1 for r in reports if r.outcome == "completed")
    errored = sum(1 for r in reports if r.outcome == "error")
    stopped = sum(1 for r in reports if r.outcome == "stopped")
    maxturns = sum(1 for r in reports if r.outcome == "max_turns")
    total_tokens = sum(r.tokens_used for r in reports)

    print(f"\n  Outcomes: {completed} completed, {errored} errors, "
          f"{stopped} stopped, {maxturns} max-turns")
    print(f"  Total tokens across sessions: {total_tokens:,}")

    # Per-session quick table
    print(f"\n  {_THIN}")
    print(f"  {'Session':<10} {'Outcome':<12} {'Turns':>6} {'Tokens':>10} {'P':>5} {'S':>5} {'M':>5}")
    print(f"  {_THIN}")
    for r in reports:
        print(f"  {r.session_id:<10} {r.outcome:<12} {r.turns:>6} {r.tokens_used:>10,} "
              f"{r.p_count:>5} {r.s_count:>5} {r.m_count:>5}")
    print(f"  {_THIN}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Credit-attribution analysis for Harness session logs",
    )
    parser.add_argument(
        "path", nargs="?", default="logs",
        help="Path to a .jsonl file or a directory of session logs (default: logs/)",
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Show only the cross-session summary, not per-session reports",
    )
    parser.add_argument(
        "--json", dest="json_out", action="store_true",
        help="Output as JSON (machine-readable)",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"Error: path not found: {path}", file=sys.stderr)
        sys.exit(1)

    # Collect session files
    if path.is_file():
        files = [path]
    else:
        files = sorted(path.glob("*.jsonl"))
        if not files:
            print(f"No .jsonl files found in {path}", file=sys.stderr)
            sys.exit(1)

    # Parse
    reports = [parse_session(f) for f in files]

    # Output
    if args.json_out:
        json_reports = []
        for r in reports:
            json_reports.append({
                "session_id": r.session_id,
                "file_path": r.file_path,
                "total_events": r.total_events,
                "p_count": r.p_count,
                "s_count": r.s_count,
                "m_count": r.m_count,
                "g0_count": r.g0_count,
                "g1_count": r.g1_count,
                "g2_count": r.g2_count,
                "g3_count": r.g3_count,
                "outcome": r.outcome,
                "turns": r.turns,
                "tokens_used": r.tokens_used,
                "tool_errors": dict(r.tool_errors),
                "cross_tab": {d: dict(g) for d, g in r.cross_tab.items()},
            })
        print(json.dumps(json_reports, indent=2, ensure_ascii=False))
    else:
        if not args.summary_only:
            for r in reports:
                print_report(r)
        print_summary(reports)


if __name__ == "__main__":
    main()
