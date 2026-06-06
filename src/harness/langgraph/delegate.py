"""LangGraphDelegate — wraps compiled graphs as LoopDelegate.

When engine="langgraph", this delegate replaces ChatDelegate and
the compiled graph replaces AgenticLoop. LangGraph handles its own
loops, retries, checkpointing — the delegate just provides the
LoopDelegate interface for CLI compatibility.

Graph mode selection from config.loop.mode:
- "standard": basic agent loop (LLM → tools → observe → repeat)
- "pair_coding": coder → reviewer → human_approval loop
- "multi_agent": controller → implementers → two-stage review
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, TYPE_CHECKING

from harness.core.loop_delegate import (
    LoopDelegate,
    LoopContext,
    LoopOutcome,
    LoopSignal,
    TextAction,
)
from harness.core.loop import LoopEvent

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
    from harness.llm.client import LlmClient
    from harness.llm.types import ChatMessage, LlmResponse, ToolCall
    from harness.tools.executor import ToolExecutor
    from harness.core.context import ContextGatherer
    from harness.config.config import Config
    from harness.logging.task_logger import TaskLogger

logger = logging.getLogger(__name__)


class LangGraphDelegate(LoopDelegate):
    """Wraps a LangGraph compiled graph as a LoopDelegate.

    This is the bridge between the existing LoopDelegate interface
    and the new LangGraph-based agent graphs.

    Two modes of operation:
    1. Full graph mode (pair_coding, multi_agent): The graph IS the agent.
       call_llm() runs the full graph to completion and returns final output.
    2. Standard mode: The graph handles one turn at a time, compatible
       with AgenticLoop's per-iteration calling pattern.
    """

    def __init__(
        self,
        graph: "CompiledStateGraph",
        *,
        mode: str = "standard",
        llm: "LlmClient | None" = None,
        tool_executor: "ToolExecutor | None" = None,
        gatherer: "ContextGatherer | None" = None,
        thread_id: str = "",
        session_id: str = "",
        task_logger: "TaskLogger | None" = None,
    ):
        self._graph = graph
        self._mode = mode
        self._llm = llm
        self._tools = tool_executor
        self._gatherer = gatherer
        self._thread_id = thread_id or str(uuid.uuid4())
        self._session_id = session_id or str(uuid.uuid4())
        self._task_logger = task_logger
        self._signal: LoopSignal = LoopSignal.NONE
        self._config: dict = {
            "configurable": {"thread_id": self._thread_id},
            "recursion_limit": 100,
        }
        self._final_state: dict = {}
        self._graph_started = False
        self._on_event: Callable[[LoopEvent], None] | None = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def thread_id(self) -> str:
        return self._thread_id

    # ------------------------------------------------------------------
    # LoopDelegate interface
    # ------------------------------------------------------------------

    async def check_signals(self) -> LoopSignal:
        return self._signal

    async def before_llm_call(
        self, ctx: LoopContext, iteration: int
    ) -> LoopOutcome | None:
        """Initialize the graph on first call.

        For full-graph modes (pair_coding, multi_agent), we run the entire
        graph in call_llm() and return the outcome. The before_llm_call
        hook is used to prepare the initial state.
        """
        return None

    async def call_llm(
        self, ctx: LoopContext, iteration: int
    ) -> "LlmResponse":  # type: ignore[override]
        """Run the LangGraph graph.

        For full-graph modes, this runs the entire graph to completion.
        For standard mode, this runs one graph step per iteration.

        Interrupt detection: LangGraph's interrupt_before pauses the graph
        without raising an exception. We detect pauses by checking
        ``graph.get_state(config).next`` after astream_events completes.
        If ``.next`` is non-empty, the graph is waiting at an interrupt point.
        """
        from harness.llm.types import LlmResponse

        initial_state = self._build_initial_state(ctx, iteration)

        try:
            async for event in self._graph.astream_events(
                initial_state,
                config=self._config,
                version="v2",
            ):
                self._handle_graph_event(event)
        except Exception as exc:
            # astream_events may raise GraphInterrupt internally in some
            # langgraph versions — handle it as a pause, not a crash.
            if "GraphInterrupt" in type(exc).__name__:
                logger.info("Graph interrupted for human approval (via exception)")
                return LlmResponse(
                    text="⏸ Graph paused for human approval. Use `harness continue` to proceed.",
                    tool_calls=None,
                    usage=None,
                )
            logger.error("LangGraph graph execution failed: %s", exc)
            raise

        # Check for interrupt via checkpointer state
        try:
            state = self._graph.get_state(self._config)
            if state and state.next:
                # Graph is paused at an interrupt point
                logger.info("Graph paused at: %s", state.next)
                if state.values:
                    self._final_state = state.values
                return LlmResponse(
                    text="⏸ Graph paused for human approval. Use `harness continue` to proceed.",
                    tool_calls=None,
                    usage=None,
                )
            if state and state.values:
                self._final_state = state.values
        except ValueError:
            logger.debug("No checkpointer configured — cannot detect interrupts")

        # Graph completed — extract final text
        final_text = self._extract_final_text()
        return LlmResponse(
            text=final_text,
            tool_calls=None,
            usage=None,
        )

    async def handle_text_response(
        self, text: str, ctx: LoopContext
    ) -> TextAction:
        return TextAction.RETURN

    async def execute_tool_calls(
        self, tool_calls: list, ctx: LoopContext
    ) -> LoopOutcome | None:
        """Tool execution is handled inside the LangGraph graph."""
        return None

    async def after_iteration(self, iteration: int, ctx: LoopContext):
        pass

    # ------------------------------------------------------------------
    # Human-in-the-loop support
    # ------------------------------------------------------------------

    async def resume_with_approval(self, decision: str) -> dict:
        """Resume a paused graph after human approval.

        Updates state with the user's decision at the interrupt point,
        then continues graph execution from the checkpoint.

        Args:
            decision: "APPROVED" or "CHANGES_REQUESTED"

        Returns:
            The final graph state after completion, or current state
            if the graph pauses again.
        """
        # Inject the user's decision at the interrupt point
        self._graph.update_state(
            self._config,
            {"final_decision": decision},
        )

        # Continue execution from checkpoint
        final_state: dict = {}
        try:
            async for event in self._graph.astream_events(
                None,  # None = continue from checkpoint
                config=self._config,
                version="v2",
            ):
                self._handle_graph_event(event)
        except Exception as exc:
            if "GraphInterrupt" in type(exc).__name__:
                logger.info("Graph paused again during resume")
            else:
                raise

        # Retrieve final state
        try:
            state = self._graph.get_state(self._config)
            if state and state.values:
                final_state = state.values
        except ValueError:
            logger.debug("No checkpointer — cannot retrieve resumed state")

        self._final_state = final_state
        return final_state

    def get_final_state(self) -> dict:
        """Get the most recent graph state."""
        return self._final_state

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _handle_graph_event(self, event: dict) -> None:
        """Map LangGraph astream_events to LoopEvent callbacks."""
        if not self._on_event:
            return

        kind = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})

        match (kind):
            case "on_chat_model_start":
                self._on_event(LoopEvent(kind="thinking"))
            case "on_chat_model_stream":
                chunk = data.get("chunk", {})
                if hasattr(chunk, "content") and chunk.content:
                    self._on_event(LoopEvent(
                        kind="text", content=str(chunk.content),
                    ))
            case "on_tool_start":
                self._on_event(LoopEvent(
                    kind="tool_call",
                    tool_name=name,
                    tool_input=data.get("input", {}),
                ))
            case "on_tool_end":
                output = str(data.get("output", ""))
                is_error = "error" in output.lower()[:100]
                self._on_event(LoopEvent(
                    kind="tool_result",
                    tool_name=name,
                    tool_output=output,
                    tool_error=is_error,
                ))
            case "on_chain_end":
                pass  # Final event handled in call_llm()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_initial_state(
        self, ctx: LoopContext, iteration: int
    ) -> dict:
        """Build the initial graph state from LoopContext."""
        from langchain_core.messages import HumanMessage

        # Extract the last user message content
        user_content = ""
        if ctx.messages:
            last_msg = ctx.messages[-1]
            if hasattr(last_msg, "content"):
                user_content = last_msg.content or ""
            elif isinstance(last_msg, dict):
                user_content = last_msg.get("content", "")

        base = {
            "messages": [HumanMessage(content=user_content)],
            "iteration": iteration,
            "max_iterations": 30,
            "terminal_reason": None,
            "errors": [],
            "session_id": self._session_id,
            "thread_id": self._thread_id,
        }

        # Add mode-specific fields
        if self._mode == "pair_coding":
            base.update({
                "task": user_content,
                "code": "",
                "review_comments": [],
                "review_iteration": 0,
                "max_review_iterations": 5,
                "final_decision": None,
                "human_approval_required": True,
            })
        elif self._mode == "multi_agent":
            base.update({
                "plan": user_content,
                "task_list": [],
                "current_task_index": 0,
                "implementation_results": {},
                "spec_review": None,
                "code_quality_review": None,
                "review_stage": "spec",
                "review_iteration": 0,
                "max_review_iterations": 3,
                "final_code": "",
                "pending_tasks": [],
                "completed_tasks": [],
            })

        return base

    def _extract_final_text(self) -> str:
        """Extract the final text output from the graph state."""
        state = self._final_state

        if self._mode == "pair_coding":
            code = state.get("code", "")
            decision = state.get("final_decision", "APPROVED")
            if code:
                return (
                    f"## Pair Coding Result\n\n"
                    f"**Decision**: {decision}\n\n"
                    f"```\n{code}\n```"
                )
            return f"Pair coding completed with decision: {decision}"

        if self._mode == "multi_agent":
            final = state.get("final_code", "")
            if final:
                return final
            results = state.get("implementation_results", {})
            if results:
                return "\n\n".join(
                    f"### {tid}\n{result}"
                    for tid, result in results.items()
                )
            return "Multi-agent collaboration completed."

        # Standard mode: extract from messages
        messages = state.get("messages", [])
        for m in reversed(messages):
            if hasattr(m, "content") and m.content:
                return m.content

        return "Agent loop completed."
