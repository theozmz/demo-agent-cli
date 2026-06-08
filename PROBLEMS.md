# PROBLEMS.md — Harness Codebase Audit

Systematic audit of bugs, design flaws, security issues, and inconsistencies in the Harness AI coding agent codebase.

---

## Critical

### 1. `tool_registry.all()` does not exist — sub-agent spawn always crashes

**Files:** `src/harness/core/subagent.py:106`, `src/harness/langgraph/subagent.py:126`

```python
for tool in tool_registry.all():  # AttributeError
```

`ToolRegistry` defines the method as `all_tools()` (`registry.py:49`), not `all()`. Every sub-agent spawn attempt raises `AttributeError: 'ToolRegistry' object has no attribute 'all'` at runtime. This means sub-agent delegation has never been exercised end-to-end in production code (tests likely mock the registry).

### 2. Compaction permanently disabled after 3 compactions

**File:** `src/harness/core/loop.py:411-416, 440-451`

The `TruncationTracker` prevents thrashing by capping consecutive compactions at 3. The reset path is unreachable:
- `_compaction_needed()` returns `False` when `exhausted` is `True` (line 411).
- `_auto_compact()` is only called when `_compaction_needed()` returns `True` (line 622).
- The tracker is only reset inside `_auto_compact()` when `result.strategy.value == "none"` (line 451).
- But `_auto_compact()` is never invoked when `exhausted` is `True`.

**Result:** After 3 compactions, no more compactions ever happen for the rest of the session. Context-window overflow is guaranteed in long-running sessions (default: 500 turns).

### 3. Broken subprocess argument construction — `-c` flag silently ignored

**File:** `src/harness/cli/commands/repl.py:119-120, 405-414`

```python
def _config_arg(ctx: AppContext) -> str:
    cfg_path = os.environ.get("HARNESS_CONFIG", "")
    if cfg_path:
        return f"-c {cfg_path}"     # returns single string
    return ""

cmd = [harness_exe, config_arg, "run", text]
# cmd = ["harness.exe", "-c /path/to/config.toml", "run", "the prompt"]
```

`asyncio.create_subprocess_exec(*cmd)` passes each list element as a separate OS argument. The subprocess receives the literal string `-c /path/to/config.toml` as argv[1] instead of `-c` as argv[1] and `/path/to/config.toml` as argv[2]. argparse ignores the combined argument and the config path is silently lost. Fix: split into separate list elements.

### 4. `DockerSandbox` exec timeout never enforced — hung commands leak threads

**File:** `src/harness/tools/sandbox/docker_sandbox.py:143-150`

```python
def _exec_sync(self, container, command, timeout=120):
    exit_code, output = container.exec_run(command, ...)
    # timeout parameter never passed to exec_run
```

`container.exec_run()` has no built-in timeout. When `asyncio.wait_for` fires, it cancels the asyncio wait but the thread-pool thread running `_exec_sync` remains blocked on `exec_run` forever. The container process and thread both leak.

### 5. MemoryStore methods are `async` but perform only synchronous SQLite I/O

**File:** `src/harness/memory/store.py:33, 37, 46, 51`

All `read`, `write`, `delete`, `list_keys` are `async def` with zero `await` calls. Every operation blocks the event loop during disk I/O. Callers expecting cooperative scheduling will stall all concurrent work.

---

## High

### 6. Two conflicting `LoopConfig` classes — engine field lost

**Files:** `src/harness/config/config.py:26` (Pydantic BaseModel) vs `src/harness/core/loop.py:120` (plain dataclass)

The Pydantic model has `engine`, `mode`, `human_approval`, etc. The dataclass has only `max_turns`, `compaction_threshold`, `enable_tool_intent_nudge`. All consumer code imports the dataclass version. When `getattr(self.config, 'engine', 'native')` runs (loop.py:494), it always returns `'native'` because the dataclass has no `engine` field. Observability traces misreport the engine as `"native"` even when LangGraph is active.

### 7. Broken encapsulation — private attribute injection by AgenticLoop

**File:** `src/harness/core/loop.py:473-484`

```python
if hasattr(self.delegate, '_on_event'):
    self.delegate._on_event = on_event
if hasattr(self.delegate, 'token_counter'):
    self.delegate.token_counter = _counter
if self._status:
    self._status.token_counter = _counter
```

`AgenticLoop.run()` reaches into delegates and sets private attributes via `hasattr` guards. `LangGraphDelegate` has `_on_event` but NOT `token_counter` or `_status`, so token tracking and status bar are silently broken for the LangGraph path. The `LoopDelegate` ABC has no wiring method — every delegate must implicitly know which duck-type attributes the loop expects.

### 8. Monolithic retry logic with inline tracing bypasses error taxonomy

**File:** `src/harness/core/loop.py:159-277`

`ChatDelegate.call_llm()` is 118 lines mixing retry logic, observability instrumentation, task logging, and the LLM call. The retry detection uses string matching on `str(exc).lower()` against `_TRANSIENT_PATTERNS` (line 229-232), completely bypassing the carefully designed error hierarchy in `errors.py` (`RateLimitError.retryable=True`, `ServerError.retryable=True`, etc.). If the LLM provider raises a typed `RateLimitError`, retry works only by coincidence (the string "rate limit" happens to be in the pattern list).

### 9. `ContextOverflowError` is defined but never raised

**File:** `src/harness/core/errors.py:36-39`

`ContextOverflowError(recoverable=True)` exists but is never instantiated anywhere. If the LLM returns HTTP 413 (context too large), the error is either retried (wasting retries) or propagated as an opaque exception. There is no code path that detects and surfaces context overflow specifically.

### 10. Silent fallback to native when LangGraph build fails

**File:** `src/harness/cli/commands/run.py:97-121`

When `ComplexityGate` auto-selects `langgraph` but `_build_langgraph_on_demand` fails, execution silently degrades to native mode with no user notification. Additionally, `ctx.langgraph_delegate` is never assigned, so every subsequent call repeats the failed build.

### 11. Outcome display logic uses truthiness of `content` instead of `outcome.kind`

**File:** `src/harness/cli/tui/app.py:139-145`

```python
if outcome.content:
    log.write(f"[bold green]Agent:[/] {outcome.content}")
else:
    log.write(f"[red]Error: {outcome.content}[/red]")
```

An error outcome with `content=""` (empty string, falsy) routes to the else branch, printing `[red]Error: [/red]` with no message. A success with `content=""` is indistinguishable from an error with `content=""`. Should check `outcome.kind`.

### 12. `permissions.py` — `ALWAYS` + `auto_approve=True` returns `NEEDS_APPROVAL`

**File:** `src/harness/tools/permissions.py:63-66`

```python
case ApprovalRequirement.ALWAYS:
    if ctx.auto_approve or ctx.is_interactive:
        return PermissionOutcome.NEEDS_APPROVAL  # never ALLOW
```

In autonomous mode (`auto_approve=True`), a tool with `ALWAYS` approval still returns `NEEDS_APPROVAL`. With no human to approve, the tool call hangs or is treated as denied. There is no code path where an `ALWAYS` tool can succeed.

### 13. `is_read_only` defaults to `False` — safety footgun

**File:** `src/harness/tools/tool.py:110-111`

```python
@property
def is_read_only(self) -> bool:
    return False
```

A subclass that forgets to override `is_read_only` silently requires approval even if conceptually read-only. The default should be `True` (fail-closed for safety) or an abstract property that must be explicitly overridden.

### 14. `NoOpSandbox` violates Liskov Substitution Principle on `cwd` parameter

**File:** `src/harness/tools/sandbox/runtime.py:46-48, 86`

ABC signature: `cwd: str = "/workspace"`. `NoOpSandbox` override: `cwd: str | None = None`. The default changes from `"/workspace"` (container path) to `os.getcwd()` (host path). Any code calling `.exec_cmd()` without a `cwd` argument gets silently different behavior depending on the runtime.

### 15. `DockerSandbox` stderr always empty — output multiplexing bug

**File:** `src/harness/tools/sandbox/docker_sandbox.py:157`

```python
stdout=text, stderr=""
```

The Docker SDK's `exec_run` defaults `demux=False`, multiplexing stdout and stderr into a single output stream. All output (including actual stderr) is labeled as `stdout`. Callers relying on `SandboxResult.stderr` for error detection will never see error output.

### 16. Timeout value and error message mismatch

**File:** `src/harness/cli/commands/repl.py:148, 153`

```python
timeout=3600,  # 1-hour hard cap per task
# ...
console.print("[yellow]Task timed out after 5 minutes.[/yellow]")
```

The timeout is 3600 seconds (60 minutes), but the error message says "5 minutes". If 5 minutes is intended, the timeout should be 300.

### 17. `asyncio.gather` without `return_exceptions=True`

**File:** `src/harness/cli/commands/repl.py:143-149`

If either stdout or stderr reader raises, `asyncio.gather` cancels the other reader immediately and propagates the exception. This crashes the outer try/except with an exception that may not represent the actual failure.

### 18. TUI only catches `asyncio.TimeoutError` — crashes on all other exceptions

**File:** `src/harness/cli/tui/app.py:134-149`

Any `LlmError`, `ToolError`, `ValueError`, etc. from `loop.run()` propagates uncaught and crashes the Textual app with a raw traceback.

---

## Medium

### 19. Three independent LangGraph build paths

**Files:** `src/harness/cli/context.py:231-269`, `src/harness/cli/commands/run.py:251-267, 521-559`

The same graph-building logic appears in three locations with subtle differences. `_build_langgraph_on_demand` hardcodes `interrupt_on_approval=False` and `fan_out_implementers=True`, while `_init_langgraph` reads from config. Adding a new mode requires updating all three.

### 20. Four copies of ChatDelegate + LoopContext + AgenticLoop wiring

**Files:** `src/harness/cli/commands/run.py:127-169`, `src/harness/cli/commands/repl.py:287-314`, `src/harness/cli/tui/app.py:42-130`, `src/harness/cli/context.py:153-161`

Each UI has its own copy of the agent assembly pattern with slight variations (REPL passes `task_logger`, TUI does not). No factory or builder pattern.

### 21. `_estimate_tokens()` called 2-3 times per iteration

**File:** `src/harness/core/loop.py:552-558, 622, 419`

Called during LLM response, during compaction check, and again during compaction execution. A single call with result reuse would suffice.

### 22. Cost estimation uses only the last model, not per-model tracking

**File:** `src/harness/core/token_counter.py:72-73`

```python
self._model = model  # overwritten on every add() call
```

If the session switches from Opus ($15/$75) to Haiku ($0.80/$4), all accumulated tokens are priced at Haiku rates (or vice versa).

### 23. Dead field: `total_cost_est` on `TokenCounter`

**File:** `src/harness/core/token_counter.py:61`

`total_cost_est: float = 0.0` is declared but never updated by `add()`. The `cost_est` property recomputes from scratch on every access. `total_cost_est` serves no purpose.

### 24. `CompactionEngine` uses hardcoded 200K context window

**File:** `src/harness/core/compaction.py:76`

The context window is hardcoded to 200,000 regardless of the actual model (GPT-4: 8,192, GPT-4o: 128,000, Gemini: 1,000,000). Model-specific sizes already exist in `status.py:_KNOWN_CONTEXT_WINDOWS` but are never wired into the compaction engine. Models with larger windows get unnecessary compactions; models with smaller windows may overflow before compaction triggers.

### 25. `SessionStatus` context-window prefix matching is order-dependent

**File:** `src/harness/cli/status.py:42-47`

For `gpt-4-32k`, prefix `gpt-4` (line 32) matches before `gpt-4-turbo` (line 31), returning 8,192 instead of the closest match. Should use longest-prefix matching.

### 26. Langfuse span/generation creation failures silently discard data

**File:** `src/harness/observability/langfuse_backend.py:82-85, 99-102`

When span/generation creation raises, the code returns `NoopContext`. Callers proceed normally but all subsequent `.end()` calls are silently discarded. No warning-level log — only `logger.debug`.

### 27. `LangfuseBackend.aflush()` calls blocking `self.flush()` directly

**File:** `src/harness/observability/langfuse_backend.py:188`

```python
async def aflush(self) -> None:
    self.flush()  # synchronous HTTP I/O, blocks event loop
```

### 28. `LiteLlmProvider.stream()` never yields final response with usage/stop_reason

**File:** `src/harness/llm/providers/litellm_provider.py:106-110`

Only yields `LlmResponse(text=text)` per chunk. Never yields final response carrying `usage`, `stop_reason`, or `duration_ms`. Streaming callers always receive zero-value defaults for these fields.

### 29. `LiteLlmProvider.stream()` does not pass `**kwargs` through

Unlike `generate()` (line 57: `**kwargs`), the stream method ignores caller-supplied overrides like `temperature` and `max_tokens`.

### 30. `LiteLlmProvider.stream()` has no try/except

**File:** `src/harness/llm/providers/litellm_provider.py:106-110`

If `acompletion()` raises during streaming, the exception propagates unlogged. `generate()` catches and logs (lines 75-79); `stream()` does not.

### 31. `LiteLlmProvider.stream()` chunk access assumes `delta` always present

**File:** `src/harness/llm/providers/litellm_provider.py:107`

```python
text = chunk.choices[0].delta.content if chunk.choices else ""
```

Some providers emit usage-only chunks or `[DONE]` signals without a `delta` field. This raises `AttributeError`.

### 32. Unused `debug` parameter in `_handle_repl_inprocess`

**File:** `src/harness/cli/commands/repl.py:196`

The `debug` parameter is never referenced in the function body. The in-process REPL always behaves as if `debug=False`.

### 33. TUI `debug` flag ignored

**File:** `src/harness/cli/commands/tui.py:23-24`

`args.debug` is never forwarded to `run_tui()`. TUI always runs with default logging.

### 34. TUI has no LangGraph support

**File:** `src/harness/cli/tui/app.py:42-46`

Hardcodes `ChatDelegate`. If `config.loop.engine == "langgraph"`, the TUI silently ignores it and runs in native mode.

### 35. TUI ChatDelegate created without `task_logger`

**File:** `src/harness/cli/tui/app.py:42-46`

TUI sessions produce no structured task logs. No record of tool calls, LLM calls, or outcomes for debugging.

### 36. TUI no message truncation — unbounded growth

**File:** `src/harness/cli/tui/app.py:126`

Unlike `repl.py` (which prunes to last 40 messages) and the loop's compaction engine, the TUI never prunes `self._messages`. Over a long session, context window overflow is guaranteed.

### 37. Inconsistent tool output truncation lengths across UIs

| UI | Truncation |
|----|-----------|
| `tui/app.py:87` | 300 chars |
| `run.py:24` | 500 chars |
| `repl.py:267` | 400 chars |

No shared constant or configuration.

### 38. Inconsistent tool param display across UIs

| UI | Params shown | Truncation |
|----|-------------|-----------|
| `tui/app.py:82` | 2 | 30 chars |
| `repl.py:261` | 3 | 50 chars |
| `run.py:183` | all | 60 chars |

### 39. Hardcoded tool name strings in `AppContext.initialize()`

**File:** `src/harness/cli/context.py:103-104, 146`

```python
bash_exec_tool = registry.get("bash_exec")
for name in ("memory_read", "memory_write", "memory_delete"):
```

If a tool's class attribute `name` is ever changed, the wiring silently fails (`.get()` returns `None`). Should reference `BashExecTool.name` etc.

### 40. MCP discovery may never execute in async context

**File:** `src/harness/cli/context.py:114-119`

```python
try:
    asyncio.get_running_loop()
    asyncio.ensure_future(mcp_mgr.discover_and_register(registry))
except RuntimeError:
    pass
```

If called from within an existing event loop (pytest-asyncio test, async entry point), `get_running_loop()` succeeds, `ensure_future()` schedules a coroutine that is never awaited, and MCP discovery silently never runs.

### 41. `AppContext.initialize()` is a God method at 113 lines

**File:** `src/harness/cli/context.py:68-181`

Does config loading, observability setup, CLI overrides, LLM creation, tool registry building, sandbox wiring, executor creation, MCP discovery, context gathering, RepoMap building, agent tool wiring, memory store wiring, and LangGraph init. No individual step is independently testable.

### 42. API key warning logic is Ollama-specific

**File:** `src/harness/cli/context.py:81-85`

```python
if not config.llm.api_key and config.llm.provider != "ollama":
```

Hardcodes Ollama as the only exempt provider. Self-hosted vLLM or LocalAI endpoints get spurious warnings.

### 43. `SubAgentManager` not a singleton but carries session-level state

**File:** `src/harness/core/subagent.py:58-63, 168`

Each `AgentTool` creates its own `SubAgentManager()`. Two `AgentTool` instances have independent spawn counters, defeating the per-session limit.

### 44. Sub-agent `max_turns` limits disagree across three locations

| Location | Value |
|----------|-------|
| `SubAgentConfig.max_turns` default | 50 |
| `AgentTool.input_schema` maximum | 30 |
| `AgentTool.execute()` default | 10 |

The tool tells the model "max 30," but the config allows 50. The execute default is 10. Three different numbers with no explanation.

### 45. Sub-agent has no access to memory

**File:** `src/harness/core/subagent.py:31-37`

`READ_ONLY_TOOLS` omits `memory_read`, which is also read-only. Sub-agents cannot access the session's memory store.

### 46. Sub-agent timeout leaks coroutines

**File:** `src/harness/core/subagent.py:140, 152-158`

When `asyncio.wait_for` raises `TimeoutError`, the underlying `_run_sub()` coroutine may still be running (e.g., LLM HTTP request in flight). The abandoned coroutine leaks until garbage collected.

### 47. Fragile private-attribute access across modules

**Files:** `src/harness/core/subagent.py:112`, `src/harness/cli/commands/repl.py:212-214`, `src/harness/cli/tui/app.py:50-52`

```python
safety=tool_executor._safety if hasattr(tool_executor, "_safety") else None
agent_tool._manager._status = session_status
```

Reaching across objects via `_private` attributes. If any attribute is renamed, these silently break with no error.

### 48. `ChatDelegate._session_id` and `._workspace_root` set externally as "private" attributes

**Files:** `src/harness/cli/commands/run.py:133-134`, `src/harness/cli/commands/repl.py:293-294`

These fields are set after construction via direct attribute access instead of constructor parameters. If the initialization order changes, all callers break.

### 49. Orphaned `_status` attribute on `AgenticLoop`

**File:** `src/harness/core/loop.py:475`

`self._status` is assigned in `run()` but accessed in `_auto_compact()` (line 440) and various code paths in `run()`. No `__init__` declaration. If `_auto_compact()` is ever called before `run()` sets `_status`, an `AttributeError` results.

### 50. Duplicate `continue` — dead code

**File:** `src/harness/cli/commands/repl.py:175`

```python
            continue
            continue       # dead
```

### 51. `BaseAgentTool.name` defaults to `""` — no enforcement

**File:** `src/harness/tools/tool.py:56-57`

```python
name: str = ""
description: str = ""
```

The docstring says "Subclasses must provide" but the ABC provides default `""`. A subclass that forgets to override `name` registers with an empty string, causing all subsequent lookups and schema generation to fail cryptically.

### 52. No `unregister()` on `ToolRegistry`

**File:** `src/harness/tools/registry.py`

Once registered, a tool cannot be removed without directly manipulating `self._tools` and `self._builtin_names`/`self._mcp_names`. MCP tools discovered at startup cannot be removed if the MCP server disconnects.

### 53. `Tool` ABC has no lifecycle/wiring interface

**File:** `src/harness/tools/tool.py`

Every tool invents its own wiring protocol:
- `BashExecTool.wire_sandbox(sandbox)`
- `AgentTool.wire(tool_registry, tool_executor, context_gatherer, llm)`
- `MemoryReadTool.wire_store(memory_store)`

No `wire()` or `setup()` on the ABC. `AppContext` must know every tool's specific wiring method.

### 54. `safety/pipeline.py` — blocked input still returns unsanitized content

**File:** `src/harness/safety/pipeline.py:43-48`

```python
return SafetyResult(passed=False, blocked=True, reason=..., content=text)
```

When injection is detected, `content` still holds the original dangerous input. A code path that checks only `passed` or unconditionally uses `content` processes the malicious text.

### 55. `safety/pipeline.py` — No try/except around scanners

**Files:** `src/harness/safety/pipeline.py:39, 52`

If a regex in `LeakDetector` triggers catastrophic backtracking on crafted input, the safety pipeline hangs.

### 56. `observability` span creation failure propagates to tool execution

**File:** `src/harness/tools/executor.py:76-85`

If `backend.create_trace()` raises (network error, internal state corruption), the entire tool execution crashes with no try/except.

### 57. `LeakDetector` missing modern key formats

**File:** `src/harness/safety/leak_detector.py:26`

Does not detect: `-----BEGIN OPENSSH PRIVATE KEY-----` (default since OpenSSH 7.8), Google API keys (`AIza...`), Slack tokens (`xoxb-...`), GitLab tokens (`glpat-...`).

### 58. `SafetyLayer.scan_input` and `scan_output` have asymmetric semantics

**File:** `src/harness/safety/pipeline.py`

`scan_input` only checks injection. `scan_output` only checks leaks. But injection patterns can appear in tool output, and secrets can appear in user input. Both scanners should apply to both directions, or naming should reflect the true asymmetry.

### 59. `ChatMessage.tool_result()` prepends `"Error: "` to content — fragile

**File:** `src/harness/llm/types.py:46-47`

The error indicator is a string prefix. If tool outputs contain structured data (JSON, multi-line), the prefix is ambiguous — a consumer cannot distinguish an error from literal text starting with "Error: ". No separate `is_error` field on `ChatMessage`.

### 60. `SubAgentManager.spawn()` has no error handling for `context_gatherer.gather()` or `register()` failures

**File:** `src/harness/core/subagent.py:116-122, 108`

Exceptions from `gather()`, `to_system_prompt()`, or `register()` propagate uncaught through `asyncio.wait_for` (which only catches `TimeoutError`).

---

## Low

### 61. `gather()` accepts but ignores `messages` parameter

**File:** `src/harness/core/context.py:26`

```python
def gather(self, messages: list[ChatMessage] | None = None):
```

The parameter is never used. If intended for memory retrieval, it was never implemented.

### 62. Hardcoded system prompt — no template system

**File:** `src/harness/core/context.py:44-62`

The agent's personality, safety rules, and response format are hardcoded in a Python string. No i18n, no config-driven customization, no per-project override.

### 63. `to_system_prompt()` ignores block `kind` ordering

**File:** `src/harness/core/context.py:72-74`

Blocks are joined in list order. The `SystemPromptPart` enum (STATIC, REPO_MAP, DYNAMIC, MEMORY) is not used to enforce or verify ordering.

### 64. `Session`/`Thread`/`Turn` are pure dataclasses with no serialization

**File:** `src/harness/core/session.py`

No `to_dict()`, `from_dict()`, or `to_json()`. `SessionStatus` in `cli/status.py` serves a different purpose, creating confusion between similarly-named classes.

### 65. Untyped containers in `LoopContext` and `Turn`

**Files:** `src/harness/core/loop_delegate.py:36`, `src/harness/core/session.py:32-33`

`messages: list`, `tool_calls: list` should be `list[ChatMessage]`, `list[ToolCall]`.

### 66. `Turn.started_at` uses object construction time, not execution start time

**File:** `src/harness/core/session.py:36`

```python
started_at: datetime = field(default_factory=datetime.now)
```

If a `Turn` object is queued before execution, the start time is wrong.

### 67. `TokenCounter` hardcoded pricing — no config override

**File:** `src/harness/core/token_counter.py:12-21`

`_MODEL_PRICING` is a module-level constant. Prices change; there is no config or env var override.

### 68. `RateLimitError.retry_after` declared but never populated

**File:** `src/harness/core/errors.py:29`

```python
retry_after: float | None = None
```

No code parses the `Retry-After` header from LLM API responses. Retry backoff uses hardcoded values `[1.0, 3.0, 7.0]`.

### 69. No error type for tool output too large for context

No typed error for when a tool returns output exceeding the context window. Compaction attempts to mitigate this, but if it fails, there is no specific error signal.

### 70. `permissions.py` `match` statement has no default case

**File:** `src/harness/tools/permissions.py:60-70`

A new `ApprovalRequirement` enum value causes unhandled `MatchError`.

### 71. No `CancelledError` handling anywhere in the loop family

**Files:** `src/harness/core/loop.py`, `src/harness/core/subagent.py`

Neither `AgenticLoop.run()`, `SubAgentManager.spawn()`, nor `ChatDelegate.call_llm()` handles `asyncio.CancelledError`. Keyboard interrupt or task cancellation leaves traces open, status bars stale, and sub-agent coroutines dangling.

### 72. No top-level try/except in `main()`

**File:** `src/harness/cli/main.py:124-158`

Any error produces a raw Python traceback. No user-friendly error formatting.

### 73. Config silently drops unrecognized TOML keys

**File:** `src/harness/config/config.py:109`

```python
config = cls(**{k: v for k, v in data.items() if k in cls.model_fields})
```

A typo like `[llm] modle = "..."` silently uses the default model with no warning.

### 74. No try/except for malformed TOML on config load

**Files:** `src/harness/config/config.py:100, 106`

`tomllib.load(f)` has no error handling. A TOML syntax error produces an unhandled `TOMLDecodeError`.

### 75. `fallback_model` and `expensive_model` never referenced by provider

**File:** `src/harness/config/config.py:18-19`, `src/harness/llm/providers/litellm_provider.py`

Config defines these fields, but `LiteLlmProvider` never reads them. Dead data in config.

### 76. `_truncate` function duplicated

**Files:** `src/harness/logging/task_logger.py:35`, `src/harness/observability/__init__.py:148`

Same utility function implemented identically in two modules.

### 77. `MemoryStore` connection never closed — no `close()` method

**File:** `src/harness/memory/store.py:26`

Connection opened in `__init__` but never explicitly closed. WAL file grows during long sessions.

### 78. `MemoryStore` uses deprecated `datetime.utcnow()`

**File:** `src/harness/memory/store.py:39`

```python
datetime.datetime.utcnow()  # deprecated since Python 3.12
```

The project requires Python 3.12+. Should use `datetime.datetime.now(datetime.timezone.utc)`.

### 79. `MemoryStore` single connection with `check_same_thread=False` — no locking

**File:** `src/harness/memory/store.py:26`

If two tasks write concurrently, one could corrupt the database. WAL mode helps with readers, but writers still need serialization.

### 80. `MemoryStore` memory values stored in plain text

**File:** `src/harness/memory/store.py`

If the agent stores API keys, tokens, or credentials, they reside on disk unencrypted at `~/.harness/memory.db`.

### 81. `logging/task_logger.py` — stale closed file handle after `_open` failure

**File:** `src/harness/logging/task_logger.py:68-72`

If `open()` raises after `close()` on the old file, `self._file` points to a closed file (not `None`). Next `_emit` writes to a closed file, raising `ValueError`.

### 82. `logging/task_logger.py` — No log rotation or size limits

**File:** `src/harness/logging/task_logger.py`

A long-running session produces an unbounded JSONL file.

### 83. `DockerSandbox._exec_sync` uses deprecated `datetime.utcnow()`

**File:** `src/harness/tools/sandbox/docker_sandbox.py:139`

Same deprecation as MemoryStore #78.

### 84. `jsonschema.validate` called synchronously in async executor pipeline

**File:** `src/harness/tools/executor.py:137-139`

Can block the event loop for large schemas or deeply nested parameters.

### 85. `ToolExecutor` — `jsonschema.SchemaError` not caught

**File:** `src/harness/tools/executor.py:137-141`

Only `ValidationError` is caught. A malformed schema raises unhandled `SchemaError`.

### 86. `ToolRegistry` monkeypatches `_source` onto `Tool` objects

**File:** `src/harness/tools/registry.py:37`

```python
tool._source = source
```

Sets a private attribute on objects it doesn't own. If a tool needs source metadata, it should be in the base class or stored in the registry's own map.

### 87. `ToolRegistry` schema cache has no invalidation

**File:** `src/harness/tools/registry.py:66-77`

Once cached, a tool's schema is frozen. If a tool changes its schema at runtime, the cache is stale.

### 88. `ToolRegistry` sorted cache only invalidated on `register()` — not on `is_enabled()` change

**File:** `src/harness/tools/registry.py:43`

If a tool's `is_enabled()` state changes dynamically, the sorted cache is stale.

### 89. Prompt-cache stability broken by runtime enable/disable filter

**File:** `src/harness/tools/registry.py:64`

`all_tools()` filters by `is_enabled()` after sorting. If a tool is disabled at runtime, the schema list changes, breaking prompt cache stability.

### 90. `observability` — `trace_id`/`span_id` defined as class attributes with defaults

**File:** `src/harness/observability/backend.py:31, 55`

Class-level attribute defaults shadowed by instance-level assignment in subclasses. Non-idiomatic and confusing.

### 91. `generation()` method declared with `metadata` parameter but it's never passed through

**File:** `src/harness/observability/backend.py:44-49`

`BaseTraceContext.generation()` accepts `metadata` but the `_LangfuseGenerationContext.__init__` receives it. OK in implementation, but the ABC signature is misleading for other implementations.

### 92. `lazy_init` method on `_LangfuseBackendClient` is dead infrastructure

**File:** `src/harness/observability/langfuse_backend.py:36-43`

The `lazy_init` class and `_client` lazy init pattern exists but `_client` is always set in `__init__` (line 124). The lazy initialization infrastructure is unused, adding dead weight to every method that checks `if self._client is None`.

### 93. `McpClientManager.call_tool` is a non-functional stub

**File:** `src/harness/tools/mcp/client_manager.py:131-136`

Returns a string representation of the call. No actual MCP tool invocation. Every call "succeeds" with a dummy message, masking that MCP is not implemented.

### 94. `McpClientManager` — no heartbeat/health-check for connected MCP servers

**File:** `src/harness/tools/mcp/client_manager.py`

No mechanism to detect when a previously-connected MCP server goes down.

### 95. `StreamingDelegate` in `langgraph/streaming.py` has no error handling

The streaming layer has no retry, no backpressure, no error recovery. If a stream fails mid-token, the agent loop is unaware.

### 96. `Repomap` build failure is silently downgraded to no repo map

**File:** `src/harness/cli/context.py:125-132`

Only a debug-level log. No runtime flag distinguishes "disabled" from "failed," so downstream code cannot surface the failure.

### 97. Duplicate argument definitions in CLI parser

**File:** `src/harness/cli/main.py:103-104`

Top-level parser and `_shared_flags()` both define `-c`/`--config` and `-d`/`--debug`. Interaction between the two parsers is fragile and depends on argparse's internal resolution order.

### 98. Interactive-mode detection hardcodes command names

**File:** `src/harness/cli/main.py:146-147`

```python
is_interactive = args.command in (None, "repl", "tui")
```

A new interactive command (e.g., `webui`) requires updating this tuple.

### 99. `LoopContext` is a mutable grab-bag

**File:** `src/harness/core/loop_delegate.py:32-44`

Mixes input (messages, system_prompt, tool_registry, llm, cwd) with runtime state (iteration, subagent_depth, force_text). Messages list is mutated in-place by delegates. No read-only guard. Any delegate can mutate any field at any time.

### 100. `SystemPromptBlock` defined but unused in the audited modules

**File:** `src/harness/llm/types.py:83-88`

The class exists but no code in the 15 audited files constructs or references it. May be dead code or used only in remote paths.
