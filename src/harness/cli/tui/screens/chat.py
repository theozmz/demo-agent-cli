"""ChatScreen — primary interactive chat interface for the Harness TUI."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import work
from textual.app import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Footer, Input

from harness.cli.tui.events import AgentEvent, AgentComplete, AgentError
from harness.cli.tui.widgets.chat_log import ChatLog
from harness.cli.tui.widgets.status_bar import StatusBar
from harness.core.loop import AgenticLoop, ChatDelegate, LoopConfig, LoopEvent
from harness.core.loop_delegate import LoopContext
from harness.llm.types import ChatMessage

if TYPE_CHECKING:
    from harness.cli.context import AppContext

_HISTORY_PATH = Path.home() / ".harness" / "tui_history"


class ChatScreen(Screen):
    """Primary screen: chat log, status bar, input.

    Uses reactive attributes to drive UI updates. The agent loop runs in
    a background async worker so the UI stays responsive.
    """

    BINDINGS = [
        Binding("ctrl+l", "clear_chat", "Clear", show=True),
        Binding("ctrl+r", "retry_last", "Retry", show=True),
        Binding("escape", "focus_input", "Input", show=False),
        Binding("ctrl+up", "scroll_up", "", show=False),
        Binding("ctrl+down", "scroll_down", "", show=False),
    ]

    current_instruction = reactive("idle")
    current_turn = reactive(0)
    token_count = reactive(0)
    token_limit = reactive(200_000)
    context_tokens = reactive(0)
    model_name = reactive("")
    is_processing = reactive(False, always_update=True)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._messages: list[ChatMessage] = []
        self._loop_cfg = LoopConfig(max_turns=ctx.config.loop.max_turns)
        self._pending_cards: dict[str, object] = {}
        self._pending_card_key: int = 0
        self._model = ctx.config.llm.model
        self._input_history: list[str] = []
        self._history_idx: int = -1
        self._thinking_widget = None
        self._load_history()

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self):
        yield ChatLog(id="chat-log")
        yield StatusBar(id="status-bar")
        yield Input(
            id="prompt-input",
            placeholder="Type a message... (Ctrl+C to quit)",
        )
        yield Footer()

    def on_mount(self) -> None:
        chat_log = self.query_one("#chat-log", ChatLog)
        session_id = str(uuid.uuid4())[:8]

        chat_log.add_divider()
        chat_log.add_rich([
            ("bold rgb(215,119,87)", "HARNESS"),
            ("dim", " — AI Coding Agent  v0.1.0"),
        ])
        chat_log.add_rich([
            ("dim", f"  workdir:   {self._ctx.cwd}"),
        ])
        chat_log.add_rich([
            ("dim", f"  provider:  {self._ctx.config.llm.provider} ({self._ctx.config.llm.model})"),
        ])
        chat_log.add_rich([
            ("dim", f"  sandbox:   {self._ctx.config.sandbox.runtime}"),  # noqa: RUF010
        ])
        chat_log.add_rich([
            ("dim", f"  session:   {session_id}"),
        ])
        chat_log.add_divider()
        chat_log.add_message("Harness ready.", role="system")
        self._refresh_status()
        self._update_status_periodic()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status_periodic(self) -> None:
        self._refresh_status()
        self.set_timer(1.0, self._update_status_periodic)

    def _refresh_status(self) -> None:
        status = self.query_one("#status-bar", StatusBar)
        subagent_tasks = getattr(self, "_subagent_tasks", {})
        status.refresh_(
            instruction=self.current_instruction,
            turn=self.current_turn,
            token_count=self.token_count,
            token_limit=self.token_limit,
            context_tokens=self.context_tokens,
            model=self._model,
            subagent_tasks=subagent_tasks,
        )

    def watch_current_instruction(self, _value: str) -> None:
        self._refresh_status()

    def watch_current_turn(self, _value: int) -> None:
        self._refresh_status()

    def watch_token_count(self, _value: int) -> None:
        self._refresh_status()

    def watch_is_processing(self, value: bool) -> None:
        try:
            inp = self.query_one("#prompt-input", Input)
            inp.disabled = value
            if not value:
                inp.focus()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Input submission
    # ------------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self.is_processing:
            return

        inp = self.query_one("#prompt-input", Input)
        inp.value = ""

        self._input_history.append(text)
        self._history_idx = len(self._input_history)
        self._save_history()

        chat_log = self.query_one("#chat-log", ChatLog)
        chat_log.add_message(text, role="user")

        self.current_instruction = "Thinking..."
        self.is_processing = True

        self._pending_cards = {}
        self._pending_card_key = 0
        self._run_agent(text)

    # ------------------------------------------------------------------
    # Agent worker (async background)
    # ------------------------------------------------------------------

    @work(exclusive=True, thread=False)
    async def _run_agent(self, text: str) -> None:
        """Background worker: runs AgenticLoop, posts events to main thread."""
        import asyncio
        import uuid

        self._messages.append(ChatMessage.user(text))

        from harness.logging.task_logger import TaskLogger

        name = " ".join(text.split()[:6]) if text.strip() else ""
        task_logger = TaskLogger(session_name=name)

        blocks = self._ctx.context_gatherer.gather()
        system_prompt = self._ctx.context_gatherer.to_system_prompt(blocks)

        task_logger.log_context(
            block_count=len(blocks),
            block_types=[b.kind.value for b in blocks],
            tool_count=len(self._ctx.tool_registry.get_schemas()),
            has_repomap=self._ctx.config.repomap.enabled,
            cwd=self._ctx.cwd,
        )
        task_logger.log_task_start(
            user_prompt=text,
            provider=self._ctx.config.llm.provider,
            model=self._ctx.config.llm.model,
            cwd=self._ctx.cwd,
            max_turns=self._ctx.config.loop.max_turns,
        )

        loop_ctx = LoopContext(
            messages=list(self._messages),
            system_prompt=system_prompt,
            tool_registry=self._ctx.tool_registry,
            llm=self._ctx.llm,
            cwd=self._ctx.cwd,
        )

        delegate = ChatDelegate(
            llm=self._ctx.llm,
            tool_executor=self._ctx.tool_executor,
            gatherer=self._ctx.context_gatherer,
            task_logger=task_logger,
        )
        delegate._session_id = task_logger._session_id
        delegate._workspace_root = str(Path(self._ctx.cwd).resolve())

        loop = AgenticLoop(delegate=delegate, ctx=loop_ctx, config=self._loop_cfg)

        def _on_event(ev: LoopEvent) -> None:
            self.post_message(AgentEvent(ev))

        try:
            outcome = await asyncio.wait_for(
                loop.run(on_event=_on_event),
                timeout=3600,
            )
            task_logger.log_task_end(
                outcome=outcome.kind,
                turns=outcome.turns,
                total_duration_ms=outcome.duration_ms,
                tokens_used=outcome.tokens_used,
                error=outcome.content if outcome.kind == "error" else "",
            )
            self.post_message(AgentComplete(outcome))
        except asyncio.TimeoutError:
            task_logger.log_task_end(outcome="timeout", error="Turn timed out")
            self.post_message(AgentError("Turn timed out after 1 hour."))
        except Exception as exc:
            task_logger.log_task_end(outcome="error", error=str(exc))
            self.post_message(AgentError(str(exc)))
        finally:
            task_logger.close()

    # ------------------------------------------------------------------
    # Agent event handlers
    # ------------------------------------------------------------------

    def _clear_thinking(self) -> None:
        if self._thinking_widget is not None:
            self._thinking_widget.remove()
            self._thinking_widget = None

    def on_agent_event(self, event: AgentEvent) -> None:
        ev = event.loop_event
        chat_log = self.query_one("#chat-log", ChatLog)

        if ev.kind == "thinking":
            self._clear_thinking()
            self.current_turn = ev.iteration
            self.current_instruction = "Thinking..."
            self._thinking_widget = chat_log.add_thinking()

        elif ev.kind == "tool_call":
            self._clear_thinking()
            inp = ev.tool_input or {}
            args_str = ", ".join(
                f"{k}={escape(str(v)[:30])}" for k, v in list(inp.items())[:2]
            )
            self.current_instruction = f"→ {ev.tool_name}({args_str})"
            card = chat_log.add_tool_call(ev.tool_name, args_str)
            key = f"{ev.tool_name}-{self._pending_card_key}"
            self._pending_card_key += 1
            self._pending_cards[key] = card

        elif ev.kind == "tool_result":
            self._clear_thinking()
            marker = "✗" if ev.tool_error else "←"
            self.current_instruction = f"  {marker} {ev.tool_name}"
            self._update_pending_card(ev.tool_name, ev.tool_output, ev.tool_error)

        elif ev.kind == "llm_tokens":
            if ev.content:
                chat_log.add_rich([("dim", ev.content)])

        elif ev.kind == "compact":
            self._clear_thinking()
            self.current_instruction = "Compacting..."
            chat_log.add_message("Compacting context...", role="compact")

        elif ev.kind == "done":
            self._clear_thinking()
            self.current_instruction = "idle"
            self.current_turn = 0

        elif ev.kind == "retry":
            self._clear_thinking()
            chat_log.add_rich([
                ("yellow", f"Retry {ev.retry_attempt}: "),
                ("", ev.retry_error[:100]),
            ])

    def on_agent_complete(self, event: AgentComplete) -> None:
        outcome = event.outcome
        chat_log = self.query_one("#chat-log", ChatLog)
        self._clear_thinking()

        if outcome.kind == "completed" and outcome.content:
            chat_log.add_markdown(outcome.content)
            self._messages.append(ChatMessage.assistant(outcome.content))
        elif outcome.kind == "error":
            chat_log.add_rich([("red", "Error: "), ("", outcome.content or "")])
        else:
            chat_log.add_rich([("dim", f"({outcome.kind})")])

        chat_log.add_divider()
        self.current_instruction = "idle"
        self.current_turn = 0
        self.is_processing = False

    def _update_pending_card(self, tool_name: str, output: str, is_error: bool) -> None:
        for key, card in list(self._pending_cards.items()):
            if key.startswith(tool_name):
                card.set_result(output, is_error)
                del self._pending_cards[key]
                return

    def on_agent_error(self, event: AgentError) -> None:
        chat_log = self.query_one("#chat-log", ChatLog)
        self._clear_thinking()
        chat_log.add_rich([("red", f"Error: {event.error}")])
        self.current_instruction = "idle"
        self.current_turn = 0
        self.is_processing = False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_clear_chat(self) -> None:
        log = self.query_one("#chat-log", ChatLog)
        log.remove_children()
        log.add_divider()
        log.add_message("Chat cleared.", role="system")

    def action_retry_last(self) -> None:
        if self.is_processing or not self._input_history:
            return
        last = self._input_history[-1]
        inp = self.query_one("#prompt-input", Input)
        inp.value = last
        inp.focus()
        inp.post_message(Input.Submitted(inp, last))

    def action_focus_input(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    # ------------------------------------------------------------------
    # Input history persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        try:
            if _HISTORY_PATH.exists():
                self._input_history = [
                    line.rstrip("\n")
                    for line in _HISTORY_PATH.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ][-200:]
                self._history_idx = len(self._input_history)
        except OSError:
            pass

    def _save_history(self) -> None:
        try:
            _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _HISTORY_PATH.write_text(
                "\n".join(self._input_history[-200:]), encoding="utf-8"
            )
        except OSError:
            pass
