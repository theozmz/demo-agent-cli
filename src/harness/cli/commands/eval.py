"""'harness eval' subcommand — memory evaluation with ragas + langfuse."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from harness.cli.context import AppContext
from harness.eval.metrics import MemoryMetricSuite
from harness.eval.runner import EvalRunner, EvalReport
from harness.eval.reporter import EvalReporter

console = Console()
logger = logging.getLogger(__name__)


def handle_eval(
    ctx: AppContext,
    dimension: str,
    session_id: str,
    output_path: str,
    log_dir: str,
    list_metrics: bool,
) -> None:
    """Execute the 'eval' command — evaluate memory quality.

    All infrastructure is provided via *ctx* (already initialized).
    """
    if list_metrics:
        _list_metrics()
        return

    if not _check_ragas():
        return

    console.print("[bold]Running Memory Evaluation[/bold]\n")

    # Build metric suite with optional LLM
    llm = _create_eval_llm(ctx)
    suite = MemoryMetricSuite(llm=llm)
    runner = EvalRunner(suite)

    # Build samples from task log JSONL files
    logs_path = Path(log_dir) if log_dir else Path("logs")
    samples = _collect_samples(logs_path, session_id, dimension)

    # Run evaluation
    report = asyncio.run(_run_eval(runner, samples, dimension))

    # Print results
    console.print(EvalReporter.format_table(report))

    # Write to file if requested
    if output_path:
        EvalReporter.write_json(report, output_path)
        console.print(f"\n[green]Report written to {output_path}[/green]")


def _check_ragas() -> bool:
    """Check if ragas is installed."""
    try:
        import ragas  # noqa: F401
        return True
    except ImportError:
        console.print(
            "[yellow]ragas is not installed. Install with:[/yellow]\n"
            "  uv pip install -e '.[eval]'"
        )
        return False


def _list_metrics() -> None:
    """Display available memory evaluation metrics."""
    console.print("[bold]Available Memory Evaluation Metrics[/bold]\n")

    try:
        names = MemoryMetricSuite.list_metric_names()
    except Exception:
        names = {
            "retrieval": ["context_precision", "context_recall"],
            "storage": ["faithfulness"],
            "impact": ["answer_correctness", "answer_relevancy"],
        }

    for dim, metrics in names.items():
        console.print(f"[bold]{dim.upper()}[/bold]")
        for m in metrics:
            console.print(f"  - {m}")
        console.print("")


def _create_eval_llm(ctx: AppContext) -> Any:
    """Create a ragas-compatible LLM from eval config.

    Returns None if eval LLM is not configured, which means ragas metrics
    that don't require LLM can still run.
    """
    obs_cfg = ctx.config.observability
    if not obs_cfg.eval_llm_api_key:
        console.print("[yellow]No eval_llm_api_key configured — LLM-dependent metrics will be skipped[/yellow]")
        return None

    try:
        from ragas.llms import LangchainLLMWrapper
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=obs_cfg.eval_llm_model,
            api_key=obs_cfg.eval_llm_api_key,
            base_url=obs_cfg.eval_llm_api_base or None,
            temperature=0,
        )
        return LangchainLLMWrapper(llm)
    except ImportError:
        console.print("[yellow]langchain_openai not installed — LLM-dependent metrics will be skipped[/yellow]")
        return None


def _collect_samples(logs_path: Path, session_id: str, dimension: str) -> dict[str, list]:
    """Collect evaluation samples from task log JSONL files.

    Returns a dict keyed by dimension ("retrieval", "storage", "impact")
    mapping to lists of ragas SingleTurnSample objects.
    """
    samples: dict[str, list] = {"retrieval": [], "storage": [], "impact": []}

    if not logs_path.exists():
        logger.warning("Log directory not found: %s", logs_path)
        return samples

    pattern = f"{session_id}.jsonl" if session_id else "*.jsonl"
    jsonl_files = sorted(logs_path.glob(pattern))
    if not jsonl_files:
        logger.warning("No JSONL log files found in %s", logs_path)
        return samples

    try:
        from ragas.dataset_schema import SingleTurnSample
    except ImportError:
        return samples

    for jf in jsonl_files:
        try:
            _parse_log_file(jf, samples)
        except Exception as exc:
            logger.warning("Failed to parse log file %s: %s", jf, exc)

    # Filter by dimension if specified
    if dimension != "all":
        return {dimension: samples.get(dimension, [])}
    return samples


def _parse_log_file(filepath: Path, samples: dict[str, list]) -> None:
    """Parse a single JSONL log file into evaluation samples."""
    from ragas.dataset_schema import SingleTurnSample

    memory_reads: list[dict] = []
    memory_writes: list[dict] = []
    user_prompts: list[str] = []
    agent_responses: list[str] = []

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            event = record.get("event", "")

            if event == "task_start":
                user_prompts.append(record.get("user_prompt", ""))
            elif event == "task_end":
                pass  # outcome captured in impact samples
            elif event == "memory_op":
                op = record.get("operation", "")
                if op == "read":
                    memory_reads.append(record)
                elif op == "write":
                    memory_writes.append(record)
            elif event == "tool_call":
                pass  # general tool traces

    # Build retrieval samples from memory_read operations
    for mr in memory_reads:
        samples["retrieval"].append(SingleTurnSample(
            user_input=mr.get("key", ""),
            retrieved_contexts=[mr.get("value_summary", "")],
            reference=mr.get("value_summary", ""),
        ))

    # Build storage samples from memory_write operations
    for mw in memory_writes:
        samples["storage"].append(SingleTurnSample(
            user_input=mw.get("key", ""),
            response=mw.get("value_summary", ""),
            retrieved_contexts=[mw.get("value_summary", "")],
        ))

    # Build impact samples from user prompts and agent responses
    for i, prompt in enumerate(user_prompts):
        if i < len(agent_responses):
            samples["impact"].append(SingleTurnSample(
                user_input=prompt,
                response=agent_responses[i] if i < len(agent_responses) else "",
                reference=prompt,
            ))


async def _run_eval(runner: EvalRunner, samples: dict[str, list], dimension: str) -> EvalReport:
    """Run the appropriate evaluation based on dimension."""
    if dimension == "retrieval":
        results = await runner.run_retrieval_eval(samples.get("retrieval", []))
        return EvalReport(dimension="retrieval", results=results, summary=_summarize(results))
    elif dimension == "storage":
        results = await runner.run_storage_eval(samples.get("storage", []))
        return EvalReport(dimension="storage", results=results, summary=_summarize(results))
    elif dimension == "impact":
        results = await runner.run_impact_eval(samples.get("impact", []))
        return EvalReport(dimension="impact", results=results, summary=_summarize(results))
    else:
        return await runner.run_full(samples)


def _summarize(results: list) -> dict[str, float]:
    if not results:
        return {}
    values = [r.value for r in results]
    return {
        "count": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
    }


def add_eval_subparser(subparsers, shared_parent) -> None:
    """Add the 'eval' subcommand to an argparse subparsers group."""
    parser = subparsers.add_parser(
        "eval",
        parents=[shared_parent],
        help="Evaluate memory quality with ragas metrics",
    )
    parser.add_argument(
        "subcommand",
        nargs="?",
        default="memory",
        choices=["memory", "list-metrics"],
        help="Evaluation subcommand (default: memory)",
    )
    parser.add_argument(
        "--dimension",
        default="all",
        choices=["all", "retrieval", "storage", "impact"],
        help="Evaluation dimension to run (default: all)",
    )
    parser.add_argument(
        "--session",
        default="",
        dest="session_id",
        help="Evaluate a specific session by ID",
    )
    parser.add_argument(
        "-o", "--output",
        default="",
        dest="output_path",
        help="Write report to JSON file",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory containing task log JSONL files (default: logs)",
    )
    parser.add_argument(
        "--list-metrics",
        action="store_true",
        default=False,
        help="List available evaluation metrics",
    )
    parser.set_defaults(func=_eval_dispatch)


def _eval_dispatch(args: Any, ctx: AppContext) -> None:
    """Dispatch the eval command from argparse args."""
    handle_eval(
        ctx=ctx,
        dimension=args.dimension,
        session_id=args.session_id,
        output_path=args.output_path,
        log_dir=args.log_dir,
        list_metrics=args.list_metrics,
    )
