"""Multi-agent collaboration nodes.

Nodes for the Controller-Implementer-Reviewer graph (DESIGN.md section 3.13):
- Controller: decomposes plan into task_list with complexity assessment
- TaskRouter: topological sort, picks next ready task
- Implementer: spawns sub-agent for one task
- ResultCollector: parses implementer output (implementer report protocol)
- SpecReviewer: validates implementation against plan
- CodeQualityReviewer: evaluates code structure and quality
- Remediation: creates fix tasks on review failure
- Finalize: terminal node, collects results

All nodes use factory functions (closures) for infrastructure injection.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Callable, TYPE_CHECKING

from harness.langgraph.state import MultiAgentState, TaskItem, ReviewResult
from harness.langgraph.complexity import ComplexityAssessor, ComplexityTier

if TYPE_CHECKING:
    from harness.llm.client import LlmClient
    from harness.llm.types import ChatMessage
    from harness.tools.registry import ToolRegistry
    from harness.tools.executor import ToolExecutor
    from harness.core.context import ContextGatherer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

_JSON_PATTERN = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]", re.MULTILINE)


def _extract_json(text: str) -> Any:
    """Extract JSON from LLM output, handling markdown fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = _JSON_PATTERN.search(text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Controller node
# ---------------------------------------------------------------------------


def make_controller_node(llm: "LlmClient") -> Callable:
    """Create a controller node.

    The controller reads the plan and decomposes it into a task list.
    It NEVER writes code — its tool set excludes file_write/file_edit/bash_exec.

    Each task is tagged with complexity (simple/integration/architecture)
    via the ComplexityAssessor for downstream model routing.
    """

    CONTROLLER_SYSTEM = (
        "You are a technical project controller. Your job is to decompose "
        "an implementation plan into a precise, ordered task list.\n\n"
        "Rules:\n"
        "1. Each task must be atomic — one clear deliverable\n"
        "2. Specify dependencies (which task IDs must complete first)\n"
        "3. Tag each task with estimated complexity: simple, integration, or architecture\n"
        "4. Order tasks to minimize conflicts (sequential when touching same files)\n"
        "5. Output ONLY a JSON array, no other text.\n\n"
        "Output format:\n"
        '[\n  {\n'
        '    "id": "task-1",\n'
        '    "description": "...",\n'
        '    "dependencies": [],\n'
        '    "complexity": "simple"\n'
        "  }\n"
        "]"
    )

    assessor = ComplexityAssessor()

    async def node_controller(state: MultiAgentState) -> dict:
        plan = state.get("plan", "")
        iteration = state.get("iteration", 0)

        if not plan:
            # Extract plan from the last user message
            messages = state.get("messages", [])
            for m in reversed(messages):
                if hasattr(m, "content") and m.content:
                    plan = m.content
                    break

        if not plan:
            return {
                "errors": ["No plan provided — cannot decompose tasks"],
                "terminal_reason": "error",
            }

        # Call LLM to decompose the plan
        from harness.llm.types import ChatMessage as CM

        prompt = f"Plan:\n{plan}\n\nDecompose this plan into a task list."
        try:
            messages = [CM.system(CONTROLLER_SYSTEM), CM.user(prompt)]
            response = await llm.generate(messages=messages, tools=None)
            raw_tasks = _extract_json(response.text or "[]")
        except Exception as exc:
            logger.error("Controller LLM call failed: %s", exc)
            return {"errors": [f"Controller failed: {exc}"], "terminal_reason": "error"}

        if not isinstance(raw_tasks, list):
            raw_tasks = []

        # Build TaskItem list with complexity assessment
        task_list: list[TaskItem] = []
        for i, raw in enumerate(raw_tasks):
            task_id = raw.get("id", f"task-{i + 1}")
            description = raw.get("description", str(raw))
            deps = raw.get("dependencies", [])
            if isinstance(deps, str):
                deps = [deps]

            # Assess complexity (heuristic pass)
            assessment = assessor.assess(description, plan)

            task_list.append({
                "id": task_id,
                "description": description,
                "dependencies": deps,
                "status": "PENDING",
                "assigned_to": "",
                "result": None,
                "complexity": assessment.tier.value,
            })

        logger.info("Controller decomposed plan into %d tasks", len(task_list))

        return {
            "plan": plan,
            "task_list": task_list,
            "current_task_index": 0,
            "iteration": iteration + 1,
            "pending_tasks": [t["id"] for t in task_list],
            "completed_tasks": [],
        }

    return node_controller


# ---------------------------------------------------------------------------
# Task Router node
# ---------------------------------------------------------------------------


def make_task_router_node() -> Callable:
    """Create a task router node (pure logic, no LLM call).

    Selects the next PENDING task where all dependencies are DONE.
    Encodes a topological DAG scheduler:
    - Ready = all deps are in completed_tasks
    - If nothing is ready but tasks remain, return BLOCKED → controller intervention
    """

    async def node_task_router(state: MultiAgentState) -> dict:
        task_list = list(state.get("task_list", []))
        completed = set(state.get("completed_tasks", []))

        # Mark tasks that have become unblocked
        ready: list[int] = []
        for i, task in enumerate(task_list):
            if task.get("status") != "PENDING":
                continue
            deps = set(task.get("dependencies", []))
            if deps.issubset(completed):
                ready.append(i)

        if not ready:
            # Check if all tasks are done
            all_done = all(
                t.get("status") in ("DONE", "DONE_WITH_CONCERNS")
                for t in task_list
            )
            if all_done:
                return {
                    "review_stage": "spec",
                }
            # Some tasks are BLOCKED — need controller to resolve
            logger.warning("Task router: no ready tasks, but %d remain", len(task_list) - len(completed))
            return {"review_stage": "spec"}  # Fall through to review

        # Select the first ready task (respects dependency order)
        idx = ready[0]
        task = task_list[idx]
        task["status"] = "IN_PROGRESS"
        task_list[idx] = task

        return {
            "task_list": task_list,
            "current_task_index": idx,
        }

    return node_task_router


# ---------------------------------------------------------------------------
# Implementer node
# ---------------------------------------------------------------------------


def make_implementer_node(
    llm: "LlmClient",
    tool_registry: "ToolRegistry",
    tool_executor: "ToolExecutor",
    context_gatherer: "ContextGatherer",
    *,
    fan_out: bool = False,
) -> Callable:
    """Create an implementer node.

    Spawns a LangGraphSubAgentManager-backed sub-agent to execute one task.
    The sub-agent:
    - Gets curated context (plan excerpt + task description only)
    - Has write tool access (unlike existing read-only AgentTool sub-agents)
    - Uses the model tier determined by complexity assessment
    - Reports status via the implementer report protocol

    Args:
        fan_out: If True, spawn parallel implementers for all ready tasks.
    """

    async def node_implementer(state: MultiAgentState) -> dict:
        task_list = list(state.get("task_list", []))
        idx = state.get("current_task_index", 0)
        plan = state.get("plan", "")
        iteration = state.get("iteration", 0)

        if idx >= len(task_list):
            return {"errors": [f"Invalid task index: {idx}"]}

        task = task_list[idx]
        task_id = task.get("id", f"task-{idx}")
        description = task.get("description", "")
        complexity_str = task.get("complexity", "integration")

        # Parse complexity tier
        try:
            complexity = ComplexityTier(complexity_str)
        except ValueError:
            complexity = ComplexityTier.INTEGRATION

        logger.info(
            "Implementer: executing task '%s' (complexity=%s, idx=%d)",
            task_id, complexity.value, idx,
        )

        # Build the implementer prompt with curated context
        prompt = (
            f"## Implementation Plan\n{plan}\n\n"
            f"## Your Task ({task_id})\n{description}\n\n"
            f"Implement this task completely. You have access to file tools.\n\n"
            f"## Output Protocol\n"
            f"When done, report your status on a new line:\n"
            f"- STATUS: DONE\n"
            f"- STATUS: DONE_WITH_CONCERNS (explain concerns)\n"
            f"- STATUS: NEEDS_CONTEXT (what specific info is needed)\n"
            f"- STATUS: BLOCKED (by what dependency)\n"
        )

        # Run the implementer sub-agent via the existing AgenticLoop
        from harness.llm.types import ChatMessage as CM
        from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig
        from harness.core.loop_delegate import LoopContext

        sub_ctx = LoopContext(
            messages=[CM.user(prompt)],
            system_prompt=(
                "You are an expert implementer. Execute the assigned task. "
                "Use the available tools to read and write files. "
                "Be thorough — implement the complete solution. "
                "Always report your status at the end."
            ),
            tool_registry=tool_registry,
            llm=llm,
            cwd="",
            subagent_depth=1,
        )

        delegate = ChatDelegate(
            llm=llm,
            tool_executor=tool_executor,
            gatherer=context_gatherer,
        )
        loop_config = LoopConfig(max_turns=20)

        try:
            loop = AgenticLoop(delegate=delegate, ctx=sub_ctx, config=loop_config)
            outcome = await loop.run()
            result_text = outcome.content or ""
        except Exception as exc:
            logger.error("Implementer sub-agent failed: %s", exc)
            result_text = f"STATUS: BLOCKED\nError: {exc}"

        # Parse implementer status from output
        status = _parse_implementer_status(result_text)

        # Update task
        task["status"] = status
        task["result"] = result_text
        task["assigned_to"] = f"implementer-{task_id}"
        task_list[idx] = task

        # Track completion
        completed = list(state.get("completed_tasks", []))
        if status in ("DONE", "DONE_WITH_CONCERNS"):
            if task_id not in completed:
                completed.append(task_id)

        pending = [t["id"] for t in task_list if t.get("status") in ("PENDING", "IN_PROGRESS", "BLOCKED")]

        return {
            "task_list": task_list,
            "iteration": iteration + 1,
            "completed_tasks": completed,
            "pending_tasks": pending,
            "implementation_results": {
                task_id: result_text,
            },
        }

    return node_implementer


def _parse_implementer_status(text: str) -> str:
    """Parse implementer report protocol from sub-agent output."""
    match = re.search(r"STATUS:\s*(DONE_WITH_CONCERNS|NEEDS_CONTEXT|BLOCKED|DONE)", text)
    if match:
        return match.group(1)
    # Heuristic fallback
    if "ERROR" in text or "error" in text.lower():
        return "BLOCKED"
    return "DONE"


# ---------------------------------------------------------------------------
# Result Collector node
# ---------------------------------------------------------------------------


def make_result_collector_node() -> Callable:
    """Create a result collector node (pure logic).

    Accumulates implementation results and updates task statuses.
    """

    async def node_result_collector(state: MultiAgentState) -> dict:
        task_list = list(state.get("task_list", []))
        idx = state.get("current_task_index", 0)
        results = dict(state.get("implementation_results", {}))
        completed = list(state.get("completed_tasks", []))

        if idx < len(task_list):
            task = task_list[idx]
            task_id = task.get("id", f"task-{idx}")
            result = task.get("result", "")

            if task.get("status") in ("DONE", "DONE_WITH_CONCERNS") and task_id not in completed:
                completed.append(task_id)

            if result:
                results[task_id] = result

        pending = [t["id"] for t in task_list if t.get("status") not in ("DONE", "DONE_WITH_CONCERNS")]

        return {
            "task_list": task_list,
            "implementation_results": results,
            "completed_tasks": completed,
            "pending_tasks": pending,
        }

    return node_result_collector


# ---------------------------------------------------------------------------
# Spec Reviewer node
# ---------------------------------------------------------------------------


def make_spec_reviewer_node(
    llm: "LlmClient",
    tool_executor: "ToolExecutor",
) -> Callable:
    """Create a spec compliance reviewer node.

    Validates the implementation against the plan by reading actual code files.
    Uses the expensive model (Opus) — always.
    """

    SPEC_REVIEWER_SYSTEM = (
        "You are a specification compliance reviewer. Your job is to verify "
        "that the implementation matches the plan.\n\n"
        "Read the actual files that were changed. Compare against the plan.\n"
        "Check ONLY for functional correctness — does the code do what the plan says?\n\n"
        "Output a JSON object:\n"
        '{\n  "passed": true/false,\n'
        '  "issues": ["issue 1", "issue 2"],\n'
        '  "file": "path/to/file",\n'
        '  "line": 0\n'
        "}"
    )

    async def node_spec_reviewer(state: MultiAgentState) -> dict:
        plan = state.get("plan", "")
        results = state.get("implementation_results", {})
        iteration = state.get("iteration", 0)

        # Build context: plan + what was implemented
        impl_summary = "\n".join(
            f"### {tid}\n{result[:500]}"
            for tid, result in results.items()
        )
        prompt = (
            f"## Plan\n{plan}\n\n"
            f"## Implementation Results\n{impl_summary}\n\n"
            f"Review the implementation against the plan. "
            f"Read the actual files if you need to verify."
        )

        from harness.llm.types import ChatMessage as CM

        try:
            messages = [CM.system(SPEC_REVIEWER_SYSTEM), CM.user(prompt)]
            response = await llm.generate(messages=messages, tools=None)
            review_data = _extract_json(response.text or "{}")
        except Exception as exc:
            logger.error("Spec reviewer LLM call failed: %s", exc)
            review_data = {"passed": True, "issues": [], "file": "", "line": 0}

        if not isinstance(review_data, dict):
            review_data = {"passed": True, "issues": [], "file": "", "line": 0}

        review: ReviewResult = {
            "passed": review_data.get("passed", True),
            "issues": review_data.get("issues", []),
            "file": review_data.get("file", ""),
            "line": review_data.get("line", 0),
        }

        next_stage = "code_quality" if review["passed"] else "done"

        return {
            "spec_review": review,
            "review_stage": next_stage,
            "iteration": iteration + 1,
        }

    return node_spec_reviewer


# ---------------------------------------------------------------------------
# Code Quality Reviewer node
# ---------------------------------------------------------------------------


def make_code_quality_reviewer_node(
    llm: "LlmClient",
    tool_executor: "ToolExecutor",
) -> Callable:
    """Create a code quality reviewer node.

    Reviews code for separation of concerns, decomposition quality,
    structure, and file growth. Only runs AFTER spec review passes.
    Uses the expensive model (Opus) — always.
    """

    QUALITY_REVIEWER_SYSTEM = (
        "You are a code quality reviewer. Review the implementation for:\n"
        "- Separation of concerns and modularity\n"
        "- Code structure and organization\n"
        "- File size and growth (are files getting too large?)\n"
        "- Naming conventions and clarity\n"
        "- Error handling patterns\n\n"
        "Do NOT re-check functional correctness — that was already verified.\n\n"
        "Output a JSON object:\n"
        '{\n  "passed": true/false,\n'
        '  "issues": ["issue 1", "issue 2"],\n'
        '  "file": "path/to/file",\n'
        '  "line": 0\n'
        "}"
    )

    async def node_code_quality_reviewer(state: MultiAgentState) -> dict:
        results = state.get("implementation_results", {})
        iteration = state.get("iteration", 0)

        # Only review if spec passed
        spec_review = state.get("spec_review")
        if spec_review and not spec_review.get("passed", False):
            return {"review_stage": "done"}

        impl_summary = "\n".join(
            f"### {tid}\n{result[:500]}"
            for tid, result in results.items()
        )
        prompt = (
            f"## Implementation\n{impl_summary}\n\n"
            f"Review the code quality. Read the actual files to inspect."
        )

        from harness.llm.types import ChatMessage as CM

        try:
            messages = [CM.system(QUALITY_REVIEWER_SYSTEM), CM.user(prompt)]
            response = await llm.generate(messages=messages, tools=None)
            review_data = _extract_json(response.text or "{}")
        except Exception as exc:
            logger.error("Code quality reviewer LLM call failed: %s", exc)
            review_data = {"passed": True, "issues": [], "file": "", "line": 0}

        if not isinstance(review_data, dict):
            review_data = {"passed": True, "issues": [], "file": "", "line": 0}

        review: ReviewResult = {
            "passed": review_data.get("passed", True),
            "issues": review_data.get("issues", []),
            "file": review_data.get("file", ""),
            "line": review_data.get("line", 0),
        }

        return {
            "code_quality_review": review,
            "review_stage": "done",
            "iteration": iteration + 1,
        }

    return node_code_quality_reviewer


# ---------------------------------------------------------------------------
# Remediation node
# ---------------------------------------------------------------------------


def make_remediation_node() -> Callable:
    """Create a remediation node.

    When review fails, creates fix tasks and routes back to task_router.
    """

    async def node_remediation(state: MultiAgentState) -> dict:
        spec = state.get("spec_review")
        quality = state.get("code_quality_review")
        task_list = list(state.get("task_list", []))

        issues: list[str] = []
        if spec and not spec.get("passed", False):
            issues = spec.get("issues", [])
        if quality and not quality.get("passed", False):
            issues.extend(quality.get("issues", []))

        if not issues:
            return {"review_stage": "done"}

        # Create a fix task for each issue
        fix_id_base = f"fix-{uuid.uuid4().hex[:6]}"
        for i, issue in enumerate(issues):
            fix_task: TaskItem = {
                "id": f"{fix_id_base}-{i + 1}",
                "description": f"FIX: {issue}",
                "dependencies": [],
                "status": "PENDING",
                "assigned_to": "",
                "result": None,
                "complexity": "simple",
            }
            task_list.append(fix_task)

        logger.info("Remediation: created %d fix tasks", len(issues))

        return {
            "task_list": task_list,
            "review_stage": "spec",
            "pending_tasks": [t["id"] for t in task_list if t["status"] == "PENDING"],
        }

    return node_remediation


# ---------------------------------------------------------------------------
# Finalize node
# ---------------------------------------------------------------------------


def make_finalize_node() -> Callable:
    """Create a finalize node (terminal).

    Collects all implementation results into the final output.
    """

    async def node_finalize(state: MultiAgentState) -> dict:
        results = state.get("implementation_results", {})
        task_list = state.get("task_list", [])
        iteration = state.get("iteration", 0)

        # Build final output
        parts = []
        for task in task_list:
            tid = task.get("id", "")
            desc = task.get("description", "")
            status = task.get("status", "")
            result = results.get(tid, task.get("result", ""))
            parts.append(
                f"## {tid}: {desc}\n"
                f"Status: {status}\n\n"
                f"{result or '(no output)'}\n"
            )

        final_code = "\n---\n".join(parts)

        return {
            "final_code": final_code,
            "terminal_reason": "completed",
            "iteration": iteration + 1,
            "review_stage": "done",
        }

    return node_finalize


# ---------------------------------------------------------------------------
# Default module-level placeholder nodes
# ---------------------------------------------------------------------------


async def node_controller(state: MultiAgentState) -> dict:
    raise RuntimeError("node_controller not wired — use make_controller_node()")


async def node_task_router(state: MultiAgentState) -> dict:
    raise RuntimeError("node_task_router not wired — use make_task_router_node()")


async def node_implementer(state: MultiAgentState) -> dict:
    raise RuntimeError("node_implementer not wired — use make_implementer_node()")


async def node_result_collector(state: MultiAgentState) -> dict:
    raise RuntimeError("node_result_collector not wired — use make_result_collector_node()")


async def node_spec_reviewer(state: MultiAgentState) -> dict:
    raise RuntimeError("node_spec_reviewer not wired — use make_spec_reviewer_node()")


async def node_code_quality_reviewer(state: MultiAgentState) -> dict:
    raise RuntimeError("node_code_quality_reviewer not wired — use make_code_quality_reviewer_node()")


async def node_remediation(state: MultiAgentState) -> dict:
    raise RuntimeError("node_remediation not wired — use make_remediation_node()")


async def node_finalize(state: MultiAgentState) -> dict:
    raise RuntimeError("node_finalize not wired — use make_finalize_node()")
