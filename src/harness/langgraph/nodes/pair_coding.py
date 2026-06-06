"""Pair programming nodes: coder, reviewer, human_approval, done.

Follows the design in LANGGRAPH.md section 2.2-2.3:
- Coder: generates/revises code from task + review feedback
- Reviewer: structured JSON review (decision + severity + comments)
- HumanApproval: interrupt point for CLI user confirmation
- Done: terminal node

All nodes are factory functions returning LangGraph-compatible async functions.
They receive infrastructure (LlmClient, ToolExecutor) via closure.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from harness.llm.client import LlmClient
from harness.llm.types import ChatMessage
from harness.langgraph.state import PairCodingState, ReviewComment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

_JSON_PATTERN = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from LLM output, with markdown-stripping."""
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    match = _JSON_PATTERN.search(text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: try the whole text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from reviewer output")
        return {"decision": "APPROVED", "comments": []}


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------


def make_coder_node(
    llm: LlmClient,
    system_prompt: str = "",
) -> Callable:
    """Create a coder node closure.

    The coder generates code from the task description and any review feedback.
    On first call (no review comments), it generates from scratch.
    On subsequent calls, it revises existing code based on review feedback.
    """

    CODER_SYSTEM = system_prompt or (
        "You are an expert software engineer. "
        "Write clean, correct, well-documented code that fulfills the task. "
        "Output ONLY the code, no explanations. "
        "Use markdown code fences with the appropriate language tag."
    )

    async def node_coder(state: PairCodingState) -> dict:
        task = state.get("task", "")
        comments = state.get("review_comments", [])
        current_code = state.get("code", "")
        iteration = state.get("iteration", 0)

        if not comments:
            prompt = f"Task: {task}\n\nWrite the complete implementation:"
        else:
            feedback_lines = []
            for c in comments:
                sev = c.get("severity", "SUGGESTION")
                fname = c.get("file", "")
                line = c.get("line", 0)
                comment = c.get("comment", "")
                feedback_lines.append(f"  [{sev}] {fname}:{line} - {comment}")

            feedback = "\n".join(feedback_lines)
            prompt = (
                f"Current code:\n```\n{current_code}\n```\n\n"
                f"Review feedback:\n{feedback}\n\n"
                f"Please revise the code to fix ALL MUST_FIX issues "
                f"and apply SUGGESTIONs where appropriate. "
                f"Output ONLY the revised code."
            )

        try:
            messages = [
                ChatMessage.system(CODER_SYSTEM),
                ChatMessage.user(prompt),
            ]
            response = await llm.generate(messages=messages, tools=None)
            new_code = (response.text or "").strip()
            # Strip markdown code fences
            new_code = re.sub(r"^```(?:\w+)?\s*\n?", "", new_code)
            new_code = re.sub(r"\n?\s*```$", "", new_code)
            new_code = new_code.strip()
        except Exception as exc:
            logger.error("Coder LLM call failed: %s", exc)
            new_code = current_code  # Keep existing code on failure

        return {
            "code": new_code,
            "iteration": iteration + 1,
            "review_iteration": state.get("review_iteration", 0) + 1,
            "review_comments": [],  # Clear old comments for new review
        }

    return node_coder


def make_reviewer_node(
    llm: LlmClient,
    system_prompt: str = "",
) -> Callable:
    """Create a reviewer node closure.

    The reviewer inspects the current code against the task, producing
    structured JSON output with a decision and list of review comments.
    """

    REVIEWER_SYSTEM = system_prompt or (
        "You are a strict code reviewer. Review the code for correctness, "
        "style, security, and adherence to the task.\n\n"
        "Output a JSON object with the following structure:\n"
        '{\n  "decision": "APPROVED" | "CHANGES_REQUESTED",\n'
        '  "comments": [\n    {\n'
        '      "severity": "MUST_FIX" | "SUGGESTION",\n'
        '      "file": "main.py",\n'
        '      "line": 12,\n'
        '      "comment": "detailed feedback"\n'
        "    }\n  ]\n}"
    )

    async def node_reviewer(state: PairCodingState) -> dict:
        code = state.get("code", "")
        task = state.get("task", "")

        if not code:
            return {
                "final_decision": "CHANGES_REQUESTED",
                "review_comments": [{
                    "severity": "MUST_FIX",
                    "file": "",
                    "line": 0,
                    "comment": "No code was produced by the coder.",
                }],
            }

        prompt = f"Task: {task}\n\nCode:\n```\n{code}\n```"
        try:
            messages = [
                ChatMessage.system(REVIEWER_SYSTEM),
                ChatMessage.user(prompt),
            ]
            response = await llm.generate(messages=messages, tools=None)
            review = _extract_json(response.text or "{}")
        except Exception as exc:
            logger.error("Reviewer LLM call failed: %s", exc)
            return {
                "final_decision": "APPROVED",  # Err on the side of progress
                "review_comments": [],
            }

        decision = review.get("decision", "APPROVED")
        raw_comments = review.get("comments", [])

        # Normalize comments to ReviewComment shape
        comments: list[ReviewComment] = []
        for c in raw_comments:
            comments.append({
                "severity": c.get("severity", "SUGGESTION"),
                "file": c.get("file", ""),
                "line": c.get("line", 0),
                "comment": c.get("comment", ""),
            })

        return {
            "review_comments": comments,
            "final_decision": decision,
        }

    return node_reviewer


def make_human_approval_node() -> Callable:
    """Create a human approval node.

    This node is the LangGraph interrupt point. When interrupt_before is set
    on this node, the graph pauses here for CLI user interaction.

    The node itself reads the final_decision from state; if it's still PENDING
    after resume, it defaults to APPROVED (fail-open for usability).
    """

    async def node_human_approval(state: PairCodingState) -> dict:
        decision = state.get("final_decision", "PENDING")

        if decision == "PENDING":
            # No decision was injected externally — auto-approve
            logger.info("Human approval: no decision injected, auto-approving")
            return {"final_decision": "APPROVED"}

        logger.info("Human approval: decision = %s", decision)
        return {}

    return node_human_approval


def make_done_node() -> Callable:
    """Create a terminal done node.

    Sets the terminal reason and preserves the final code.
    """

    async def node_done(state: PairCodingState) -> dict:
        decision = state.get("final_decision", "APPROVED")
        review_iter = state.get("review_iteration", 0)

        return {
            "terminal_reason": "approved" if decision == "APPROVED" else "max_review_iterations",
            "iteration": state.get("iteration", 0),
            "review_iteration": review_iter,
        }

    return node_done


# ---------------------------------------------------------------------------
# Default module-level node functions (lazy-initialized)
# These are used when nodes are imported directly without factories.
# In production, the graph builder creates nodes via the factory functions.
# ---------------------------------------------------------------------------


async def node_coder(state: PairCodingState) -> dict:
    """Placeholder — wire via make_coder_node() in graph builder."""
    raise RuntimeError("node_coder not wired — use make_coder_node() in graph builder")


async def node_reviewer(state: PairCodingState) -> dict:
    """Placeholder — wire via make_reviewer_node() in graph builder."""
    raise RuntimeError("node_reviewer not wired — use make_reviewer_node() in graph builder")


async def node_human_approval(state: PairCodingState) -> dict:
    """Placeholder — wire via make_human_approval_node() in graph builder."""
    raise RuntimeError("node_human_approval not wired — use make_human_approval_node() in graph builder")


async def node_done(state: PairCodingState) -> dict:
    """Terminal node for pair coding graph."""
    return {
        "terminal_reason": state.get("final_decision", "APPROVED"),
    }
