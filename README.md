# Harness

**AI coding agent CLI — secure, high-performance, local-first.**

Harness is an AI-powered coding agent that lives in your terminal. It reads, writes, and executes code inside a sandboxed environment, driven by any LLM provider through LiteLLM.

---

## Quickstart

```bash
# 1. Install
pip install -e .

# 2. Configure your LLM
cp harness.toml harness.local.toml
# Edit harness.local.toml — set provider, model, api_key, api_base

# 3. Run a task
harness run "create a Python script that prints the current time"
```

Or enter interactive mode (REPL):

```bash
harness
# > read the file src/harness/core/loop.py and summarize the agent loop
```

### Windows one-click setup

```cmd
git clone <repo> && cd python && setup.bat
```

Edit the generated `harness.local.toml` with your API key, then run `.venv\Scripts\harness.exe`.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Presentation                                   │
│  CLI (argparse) → REPL (prompt_toolkit)         │
│  TUI (Textual) → Rich Markdown output           │
├─────────────────────────────────────────────────┤
│  Application                                    │
│  AgenticLoop → ChatDelegate → CompactionEngine  │
│  ContextGatherer → RepoMap → SafetyLayer        │
├─────────────────────────────────────────────────┤
│  Domain                                         │
│  Tool ABC → ToolExecutor → PermissionPolicy     │
│  LlmClient ABC → SandboxRuntime ABC             │
│  MemoryStore → Session/Turn/Thread models       │
├─────────────────────────────────────────────────┤
│  Infrastructure                                 │
│  LiteLLM provider → Docker sandbox              │
│  tree-sitter → SQLite → prompt_toolkit          │
└─────────────────────────────────────────────────┘
```

### Agent Loop (Query → LLM → Tools → Observe → Repeat)

Harness supports **two agent loop engines** — the native async loop and LangGraph StateGraph.

```
User prompt
  │
  ▼
ContextGatherer ──► System prompt (tools + repomap + memory)
  │
  ▼
┌─ AgenticLoop (native) ───────────────────────────┐
│                                                 │
│  1. call_llm() ──► LLM response                 │
│  2. if tool_calls: execute tools, append results │
│  3. if text: return to user                      │
│  4. compaction check (MICRO / REACTIVE)          │
│  5. repeat (max 30 turns)                        │
│                                                 │
│  Real-time events: thinking → tool_call →       │
│  tool_result → retry → done                      │
└─────────────────────────────────────────────────┘
  │
  ▼
OR (engine = "langgraph"):
  │
  ▼
┌─ LangGraph StateGraph ──────────────────────────┐
│                                                 │
│  Pair Coding: coder → reviewer → human_approval │
│  Multi-Agent: controller → implementers → review│
│                                                 │
│  Built-in: checkpointing, retry, streaming      │
│  Human-in-the-loop: interrupt for CLI approval  │
└─────────────────────────────────────────────────┘
  │
  ▼
Rich Markdown output + JSONL session log
```

---

## Technical Details

### LLM Providers (LiteLLM)
Multi-provider support: Anthropic, OpenAI, DeepSeek, Groq, OpenRouter, Ollama. Configure via `harness.toml` `[llm]` section. Secrets in `harness.local.toml` (git-ignored). Env var overrides: `HARNESS_MODEL`, `HARNESS_PROVIDER`.

### Tool System (12 built-in tools)
| Category | Tools |
|----------|-------|
| File I/O | `file_read`, `file_write`, `file_edit` |
| Search | `glob_search`, `grep_search` (ripgrep) |
| Web | `web_fetch`, `web_search` |
| Execution | `bash_exec` (Docker sandbox or NoOp) |
| Memory | `memory_read`, `memory_write`, `memory_delete` |
| Agent | `agent` (sub-agent delegation) |

Every tool call goes through a 6-step pipeline: **lookup → validate (JSON Schema) → permission check → execute → safety scan → log**.

### Sandbox
- **Docker**: Containers with `read_only` rootfs, no network, no capabilities, 512MB memory limit, UID 1000.
- **NoOp fallback**: Runs on host when Docker unavailable (dev/testing only).

### Memory (SQLite)
Persistent key-value store at `~/.harness/memory.db`. WAL mode for multi-process safety. Agent tools allow read/write/delete. Planned: auto-inject relevant memories into system prompt.

### RepoMap (tree-sitter + PageRank)
Optional repository structure map injected into system prompt. Uses `tree-sitter-language-pack` to parse code into tags (classes, functions, methods), ranks files by PageRank on import graph, fits top files under token budget.

### LangGraph Multi-Agent Collaboration
Harness includes a full LangGraph-based multi-agent system with two collaboration modes:

**Pair Coding Mode** (`mode = "pair_coding"`):
- **Coder agent**: generates/revises code from task + review feedback
- **Reviewer agent**: structured JSON review (decision + severity + comments)
- **Human-in-the-Loop**: LangGraph `interrupt_before` pauses for CLI user approval
- **Conditional loop**: `APPROVED → done`, `CHANGES_REQUESTED → back to coder`
- Shared TypedDict state with review iteration capping

**Multi-Agent Collaboration Mode** (`mode = "multi_agent"`):
- **Controller agent**: decomposes plan into dependency-ordered task list with complexity tags; never writes code
- **Implementer agents**: execute individual tasks with write access and curated context (context isolation)
- **Spec Compliance Reviewer**: validates implementation against plan (always expensive model)
- **Code Quality Reviewer**: evaluates structure and quality (only after spec passes)
- **Remediation loop**: failed reviews create fix tasks, routed back to implementers
- **DAG scheduler**: topological sort via `TaskItem.dependencies` — sequential by default (avoids Git conflicts), parallel fan-out for independent research tasks

**Autonomous Complexity Assessment**:
- Two-pass heuristic: keyword scoring (simple/integration/architecture) with confidence estimation
- Model routing by complexity tier: simple→cheap (Haiku), integration→default (Sonnet), architecture/review→expensive (Opus)
- Controller tags each task; implementer model selected accordingly

**Sub-Agent Organization Patterns**:

| Pattern | Mechanism | Use Case |
|---------|-----------|----------|
| Sequential chain (default) | task_router → implementer → result_collector loop, one task at a time | Code changes (avoids Git conflicts) |
| Parallel fan-out | `asyncio.gather()` for independent tasks | Research, read-only exploration |
| Tree (nested) | Implementer's agent tool spawns child sub-agents (depth ≤ 2) | Complex subtasks needing research |
| DAG | `TaskItem.dependencies` resolved by topological sort | Interdependent tasks |

Configure via `harness.toml`:
```toml
[loop]
engine = "langgraph"        # "native" | "langgraph"
mode = "pair_coding"        # "standard" | "pair_coding" | "multi_agent"
human_approval = true
max_review_iterations = 5
```

Or via CLI:
```bash
harness run --mode pair_coding "write a fibonacci function"
harness run --mode multi_agent "plan and implement a TODO CLI app"
```

### Context Compaction
Two-tier strategy to prevent token overflow:
- **MICRO** (>80% tokens): stub old read-only tool results with `[stub: ... N chars]`
- **REACTIVE** (>90% tokens): drop all but last 5 turns, inject truncation notice
- **TruncationTracker**: stops after 3 consecutive compactions to prevent thrashing

### LLM Retry
3 retries with exponential backoff (1s → 3s → 7s). Retries on transient errors (timeout, connection, rate limit, 5xx). Fails fast on permanent errors (auth, bad request).

### Workspace Isolation
`-w / --workspace` flag restricts all file tool access to a directory. Attempts to read/write outside the workspace are blocked with an error. Path resolution: relative paths resolve against workspace root.

### Task Logging
Each session writes a structured JSONL log to `logs/<session_id>.jsonl`. Events: `task_start`, `context`, `llm_call`, `tool_call`, `memory_op`, `task_end`. Sensitive params (api_key, password, token) are redacted.

### Observability (Langfuse Tracing)
Optional integration with [Langfuse](https://langfuse.com) for real-time trace visualization:

```toml
# harness.local.toml
[observability]
backend = "langfuse"
langfuse_public_key = "pk-..."
langfuse_secret_key = "sk-..."
langfuse_host = "http://localhost:3000"
```

Each agent session creates a **Trace**. Tool calls and LLM calls are recorded as nested **Observations** (spans/generations). All events are viewable in the Langfuse dashboard. Falls back to zero-overhead no-op when langfuse is not installed or `backend = "none"`.

Install with: `uv pip install -e ".[observability]"`

### Memory Evaluation (Ragas + Langfuse)
Evaluate memory quality across three dimensions using [Ragas](https://docs.ragas.io) metrics:

| Dimension | Metrics | What it measures |
|-----------|---------|-----------------|
| Retrieval | `context_precision`, `context_recall` | Does `memory_read` find the right facts? |
| Storage | `faithfulness` | Is `memory_write` faithful to the source? |
| Impact | `answer_correctness`, `answer_relevancy` | Does memory improve agent responses? |

Each evaluation run creates a dedicated Langfuse trace with scores per metric. Results are traceable end-to-end: **trace → observation → score → dashboard**.

```bash
# Install with eval dependencies
uv pip install -e ".[eval]"

# Configure eval LLM in harness.local.toml
# [observability]
# eval_llm_api_key = "sk-..."

# Run evaluation
harness eval memory                          # all dimensions
harness eval memory --dimension retrieval    # retrieval only
harness eval memory --session abc123         # specific session
harness eval memory --output report.json     # JSON output
harness eval list-metrics                    # list available metrics
```

### Commands
| Command | Description |
|---------|-------------|
| `harness` | Interactive REPL (default) |
| `harness run "prompt"` | One-shot task |
| `harness repl` | Explicit REPL |
| `harness tui` | Full-screen Textual TUI |
| `harness doctor` | System health check |
| `harness eval memory` | Evaluate memory quality (ragas + langfuse) |
| `harness eval list-metrics` | List available evaluation metrics |

Global flags: `-c/--config PATH`, `-d/--debug`. Run flags: `-p/--provider`, `-m/--model`, `-n/--max-turns`, `-r/--repomap`, `-w/--workspace`.

---

## Highlights

- **Multi-provider**: One interface, any LLM. No vendor lock-in.
- **Docker sandbox**: Real container isolation for code execution.
- **Real-time progress**: See thinking, tool calls, and results as they happen.
- **Session logging**: Every LLM call and tool execution is recorded as structured JSONL.
- **Subprocess REPL**: Each task runs as a subprocess — crash isolation, never lose your session.
- **Workspace boundaries**: Restrict agent file access to a specific directory.
- **Compaction + Retry**: Handles long conversations and transient LLM failures gracefully.
- **Extensible tools**: Clean ABC-based tool system. Add new tools by subclassing `Tool`.
- **Config layering**: `harness.toml` (shared) + `harness.local.toml` (secrets, git-ignored).
- **LangGraph multi-agent**: Pair coding (coder + reviewer + human-in-the-loop) and multi-agent collaboration (controller + implementers + two-stage review). Autonomous complexity assessment with tiered model routing.
- **Sub-agent topologies**: Sequential chain, parallel fan-out, tree nesting, and DAG-based dependency scheduling — flexible orchestration patterns.
- **Two-stage review pipeline**: Spec compliance (functional correctness) then code quality (structure/style) — always uses strongest model for review.
- **Langfuse tracing**: Session-level traces with nested spans for tool calls and LLM generations. Graceful fallback when langfuse not installed.
- **Ragas memory evaluation**: Assess memory retrieval, storage, and impact quality with ragas metrics. End-to-end traceability from evaluation run to langfuse dashboard.
- **Optional dependency groups**: Core deps are lean — observability (`[observability]`) and evaluation (`[eval]`) are opt-in extras.

---

## Project Structure

```
python/
├── src/harness/
│   ├── cli/           # CLI entry, REPL, TUI, commands
│   ├── config/        # Pydantic config models
│   ├── core/          # Agent loop, compaction, context, session, subagent
│   ├── eval/          # Ragas memory evaluation (metrics, runner, reporter)
│   ├── langgraph/     # LangGraph multi-agent graphs, nodes, delegate
│   │   └── nodes/     # Pair coding + multi-agent collaboration nodes
│   ├── llm/           # LLM client ABC + LiteLLM provider
│   ├── logging/       # Structured JSONL task logger
│   ├── memory/        # SQLite MemoryStore
│   ├── observability/ # Langfuse tracing backend (ABC + NoOp + Langfuse)
│   ├── repomap/       # tree-sitter tag extraction + PageRank ranking
│   ├── safety/        # Output scanning + leak detection
│   └── tools/         # Tool ABC, executor, permissions, 12 built-ins
│       └── sandbox/   # Docker + NoOp runtimes
├── tests/             # pytest test suite
├── harness.toml        # Shared config (committed)
├── harness.local.toml  # Secrets (git-ignored)
└── pyproject.toml     # Build + dependency config
```

---

## License

MIT
