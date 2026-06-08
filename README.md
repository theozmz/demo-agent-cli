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

Every tool call goes through a 6-step pipeline: **lookup → validate (JSON Schema) → permission check → execute → safety scan → log**.

#### File I/O

| Tool | Parameters | Approval | Read-only | Description |
|------|-----------|----------|-----------|-------------|
| `file_read` | `file_path` *(required)*, `offset` (int), `limit` (int, max 2000) | NEVER | yes | Read a file with optional line offset and limit. Returns line-numbered content. Binary files detected automatically. |
| `file_write` | `file_path` *(required)*, `content` *(required)* | UNLESS_AUTO | no | Write or overwrite a file. Creates parent directories automatically. |
| `file_edit` | `file_path` *(required)*, `old_string` *(required)*, `new_string` *(required)*, `replace_all` (bool) | UNLESS_AUTO | no | Exact string replacement. `old_string` must be unique in the file unless `replace_all=true`. Always read the file first. |

#### Search

| Tool | Parameters | Approval | Read-only | Description |
|------|-----------|----------|-----------|-------------|
| `glob_search` | `pattern` *(required)*, `path` (dir, default: cwd) | NEVER | yes | Fast file pattern matching (e.g. `**/*.py`, `src/**/*.ts`). Results sorted by modification time, limited to 500 matches. |
| `grep_search` | `pattern` *(required)*, `path` (dir), `glob` (file filter), `output_mode` (`content`/`files_with_matches`/`count`), `-i` (bool), `head_limit` (int, default 50) | NEVER | yes | Regex content search via ripgrep. Supports full regex syntax, case-insensitive mode, and glob filtering. Requires `rg` installed. |

#### Web

| Tool | Parameters | Approval | Read-only | Description |
|------|-----------|----------|-----------|-------------|
| `web_fetch` | `url` *(required)* | UNLESS_AUTO | yes | Fetch a URL and return content as markdown. HTTP auto-upgraded to HTTPS. Local/private IPs blocked. 15s timeout. Results truncated at 50k chars. |
| `web_search` | `query` *(required, min 2 chars)* | UNLESS_AUTO | yes | Search the web via DuckDuckGo HTML (no API key needed). Returns up to 20 results with title, snippet, and URL. |

#### Execution

| Tool | Parameters | Approval | Read-only | Description |
|------|-----------|----------|-----------|-------------|
| `bash_exec` | `command` *(required)*, `timeout` (int, 5–300s, default 120), `cwd` (dir, default `/workspace`) | UNLESS_AUTO | no | Execute a shell command inside a sandboxed container. Docker: read-only rootfs, no network, 512MB limit. Lazy container creation with health check and auto-recreate on failure. |

#### Memory

| Tool | Parameters | Approval | Read-only | Description |
|------|-----------|----------|-----------|-------------|
| `memory_read` | `key` *(required)* | NEVER | yes | Read a persistent memory fact by key. Returns stored value or "no memory found". |
| `memory_write` | `key` *(required)*, `value` *(required)* | UNLESS_AUTO | no | Create or update a persistent key-value pair. Survives session restarts. |
| `memory_delete` | `key` *(required)* | UNLESS_AUTO | no | Delete a memory fact by key. Returns confirmation or "no memory found". |

Memory is stored in SQLite at `~/.harness/memory.db` (WAL mode). Keys use slug-like identifiers (e.g. `user-pref-editor`, `project-stack`).

#### Orchestration

| Tool | Parameters | Approval | Read-only | Description |
|------|-----------|----------|-----------|-------------|
| `agent` | `description` *(required)*, `prompt` *(required)*, `subagent_type` (`claude`/`explore`/`general-purpose`/`plan`), `max_turns` (int, default 50) | UNLESS_AUTO | yes | Launch a read-only sub-agent for research, code exploration, or information gathering. Sub-agents have access to `file_read`, `glob_search`, `grep_search`, `web_fetch`, `web_search`. Max depth: 2. Max 50 sub-agents per session. |

#### Approval Levels

| Level | Behavior |
|-------|----------|
| `NEVER` | Always allowed — no prompt. Used for read-only tools (`file_read`, `glob_search`, `grep_search`, `memory_read`). |
| `UNLESS_AUTO` | Allowed without prompt in autonomous/non-interactive mode. Prompts for confirmation in interactive mode. Used for write/exec tools. |

### Sandbox
- **Docker**: Containers with `read_only` rootfs, no network, no capabilities, 512MB memory limit, UID 1000.
- **NoOp fallback**: Runs on host when Docker unavailable (dev/testing only).

### Memory (SQLite)
Persistent key-value store at `~/.harness/memory.db`. WAL mode for multi-process safety. Agent tools allow read/write/delete. Planned: auto-inject relevant memories into system prompt.

### RepoMap (tree-sitter + PageRank)
Optional repository structure map injected into system prompt. Uses `tree-sitter-language-pack` to parse code into tags (classes, functions, methods), ranks files by PageRank on import graph, fits top files under token budget.

### Multi-Agent System — Four Collaboration Modes

Harness has four execution modes, ranging from single-agent to full multi-agent orchestration. The **ComplexityGate** auto-selects the right mode based on task complexity, or you can force a specific mode via CLI.

| # | Mode | Engine | Agents | Best for |
|---|------|--------|--------|----------|
| 1 | **Native Standard** | `native` | 1 agent (ChatDelegate) | Simple: rename, typo, single function |
| 2 | **LangGraph Standard** | `langgraph` | 1 agent + reviewer | Basic tasks with built-in review |
| 3 | **Pair Coding** | `langgraph` | Coder + Reviewer + Human | Cross-module: API, refactor, migrate |
| 4 | **Multi-Agent** | `langgraph` | Controller + Implementer(s) + 2× Reviewer + Remediation | Architecture: design, security, auth |

All LangGraph modes share a **single TypedDict state object** per graph execution. Agents communicate by reading/writing typed state fields — no direct agent-to-agent messaging.

---

#### Mode 1: Native Standard

```
User → AgenticLoop → ChatDelegate → LLM → tools → observe → repeat
```

**Trigger:** Default mode. Used when `engine = "native"` or ComplexityGate classifies task as SIMPLE.

**Communication:** The `ChatDelegate` calls `llm.generate()` with the full `LoopContext` (messages + system prompt + tool schemas). Tool results are appended to `ctx.messages` as `ChatMessage.tool_result()`. No shared state beyond the message list.

**Termination:**
- LLM returns text (no tool calls) → `outcome.kind = "completed"`
- `max_turns` exceeded → `outcome.kind = "max_turns"`
- Exception in LLM → `outcome.kind = "error"`
- `LoopSignal.STOP` from delegate → `outcome.kind = "stopped"`

---

#### Mode 2: LangGraph Standard

```
ENTRY → agent → reviewer → END
```

**Trigger:** When `engine = "langgraph"` and `mode = "standard"`. Built as a `pair_coding_graph` with `interrupt_on_approval=False` and `max_review_iterations=1`.

**Communication:** A single shared `BaseAgentState` with `messages` (auto-accumulating via LangGraph's `add_messages` reducer), `iteration`, `max_iterations`, and `terminal_reason`.

**Termination:** Agent produces output → reviewer validates → `terminal_reason = "completed"`. On error: `terminal_reason = "max_turns"` or `"error"`.

---

#### Mode 3: Pair Coding

```
ENTRY → coder → reviewer → human_approval ─┬─[APPROVED]──→ done → END
                                    ▲       │
                                    │       └─[CHANGES_REQUESTED]→ coder (loop)
                                    │
                                    └── conditional edge: _route_after_approval
```

**Trigger:** ComplexityGate classifies task as INTEGRATION, or user forces `--mode pair_coding`. Configurable via `harness.toml`:
```toml
[loop]
engine = "langgraph"
mode = "pair_coding"
human_approval = true          # pause for CLI user confirmation
max_review_iterations = 5      # max coder→reviewer cycles
```

**Communication via shared `PairCodingState` fields:**

| Step | Writer → Reader | Field | Content |
|------|----------------|-------|---------|
| 1 | User → Coder | `task` | Original task description |
| 2 | Coder → Reviewer | `code` | Generated/revised code |
| 3 | Reviewer → Human | `review_comments` | List of `{severity, file, line, comment}` |
| 3 | Reviewer → Router | `final_decision` | `"APPROVED"` or `"CHANGES_REQUESTED"` |

On loop-back (`CHANGES_REQUESTED`): Coder reads `review_comments` + current `code`, generates revision, clears `review_comments` for fresh review.

**Termination:**
| Condition | Route | `terminal_reason` |
|-----------|-------|-------------------|
| `final_decision = "APPROVED"` | human_approval → done | `"approved"` |
| `review_iteration >= max_review_iterations` (5) | human_approval → done | `"max_review_iterations"` |

**Human-in-the-Loop:** The graph is compiled with `interrupt_before=["human_approval"]`. After the reviewer produces a verdict, the graph **pauses**. The CLI shows the review comments and asks the user to approve or request changes. `LangGraphDelegate.resume_with_approval(decision)` injects `{"final_decision": decision}` into the state at the interrupt point and resumes execution.

**Error handling:** LLM failure in coder preserves old code and increments counters (reviewer can reject stale code). LLM failure in reviewer defaults to `APPROVED` (fail-progress).

---

#### Mode 4: Multi-Agent (Controller + Team)

```
ENTRY → controller → task_router ─┬─→ implementer → result_collector ───┐
                                  │                                      │
                                  │◄─────────────────────────────────────┘
                                  │  (loop: pick next ready task)
                                  │
                                  ├─[all done, review=spec]→ spec_reviewer → code_quality_reviewer
                                  │                                                    │
                                  │                              ┌─[failed]→ remediation ─┐
                                  │                              │                       │
                                  │                              └──→ task_router ◄──────┘
                                  │                                   (injects fix tasks)
                                  │
                                  └─[blocked/error] → finalize → END
```

**Trigger:** ComplexityGate classifies task as ARCHITECTURE, or user forces `--mode multi_agent`. Config:
```toml
[loop]
engine = "langgraph"
mode = "multi_agent"
max_review_iterations = 3       # max remediation cycles
```

**8 agent nodes:**

| Node | Role | Model | Has tools? |
|------|------|-------|------------|
| `controller` | 📋 Decompose plan into `task_list` with dependency DAG | default (Sonnet) | No — text-only |
| `task_router` | 🔀 Topological sort, pick next ready task | Pure logic | No — no LLM |
| `implementer` | 💻 Execute one task — spawns `AgenticLoop` sub-agent with write access | per-task tier | Yes — all file/edit/exec tools |
| `result_collector` | 📥 Collect results, update `completed_tasks` | Pure logic | No |
| `spec_reviewer` | ✅ Validate implementation against original plan | expensive (Opus) | Yes — file_read for verification |
| `code_quality_reviewer` | 🔍 Evaluate structure, modularity, naming | expensive (Opus) | Yes — file_read |
| `remediation` | 🔧 Create fix `TaskItem`s for failed reviews | Pure logic | No |
| `finalize` | 🏁 Assemble `final_code` from all results | Pure logic | No |

**DAG scheduling (task_router):** Tasks carry a `dependencies: list[task_id]` field. A task is "ready" when all its dependency IDs are in `completed_tasks`. The router picks the first ready task, marks it `IN_PROGRESS`. When all tasks are `DONE`, it sets `review_stage = "spec"` to trigger the review phase.

**Fan-out:** When `fan_out=True`, all independent tasks (all deps satisfied) run in parallel via `asyncio.gather()`.

**Communication via shared `MultiAgentState` fields:**

| Step | Writer → Reader | Field |
|------|----------------|-------|
| Controller → Router | `plan`, `task_list` | |
| Router → Implementer | `task_list[].status` (`IN_PROGRESS`), `current_task_index` | |
| Implementer → Collector | `task_list[].result`, `implementation_results` | |
| Collector → Router | `completed_tasks`, `pending_tasks` | |
| Router → Reviewers | `plan`, `implementation_results` | |
| Reviewers → Remediation | `spec_review`, `code_quality_review` | |
| Remediation → Router | New `TaskItem`s appended to `task_list` | |

**Two-stage review pipeline:**
1. **Spec Reviewer** reads the plan + `implementation_results`, reads actual files via tool_executor, validates functional correctness. Returns `ReviewResult{passed, issues}`.
2. **Code Quality Reviewer** runs only after spec passes. Evaluates separation of concerns, file growth, naming, error handling. Explicitly does NOT re-check functional correctness.
3. Both reviewers always use the **expensive model** (Opus).

**Termination:**

| Condition | Route | `terminal_reason` |
|-----------|-------|-------------------|
| All tasks done + both reviews pass | code_quality_reviewer → finalize | `"completed"` |
| DAG blocked (unresolvable deps) | task_router → finalize | `"blocked"` |
| Controller error | controller → finalize | `"error"` |
| 3 remediation cycles exhausted | remediation → finalize | `"max_review_iterations"` |

**Remediation loop:** If either review fails, `remediation` creates one `TaskItem` per issue (complexity=`"simple"`, no deps) and appends them to `task_list`. Sets `review_stage = "spec"`, increments `review_iteration`. task_router picks up the fix tasks immediately. Capped at 3 cycles.

**Sub-agent implementation:** Each `implementer` task spawns an `AgenticLoop` with limited context (plan + task description only, not full conversation history), full write access, `max_turns=10`, and `subagent_depth=1`. This provides context isolation — implementers see only what they need.

**Human-in-the-loop:** Not configured. The multi-agent graph runs fully autonomously from controller to finalize.

---

#### Auto-Mode: Task-Driven Agent Selection

When `auto_mode = true`, the **ComplexityGate** analyzes your prompt and automatically picks the right engine + mode — no manual configuration needed.

| Tier | Triggers (EN) | Triggers (中文) | Engine | Mode | Example prompts |
|------|----------|----------|--------|------|-----------------|
| **SIMPLE** | rename, fix typo, add comment, single function, basic CRUD | 重命名, 修正, 拼写, 格式化, 加注释, 单个函数, 增删改查 | `native` | `standard` | `"rename getCwd to getCurrentWorkingDirectory"` `"fix typos in README"` `"add type annotations to utils.py"` `"write a function to check if a number is prime"` |
| **INTEGRATION** | API, refactor, migrate, database, multiple files, REST, GraphQL | 重构, 迁移, API, 接口, 端点, 数据库, 多模块, 微服务 | `langgraph` | `pair_coding` | `"add a REST API endpoint for user registration"` `"refactor authentication logic across multiple modules"` `"migrate database queries from raw SQL to ORM"` |
| **ARCHITECTURE** | design, security, OAuth, RBAC, encryption, concurrency, notification system | 架构设计, 认证系统, 权限控制, OAuth, 加密, 并发, 分布式, 系统设计 | `langgraph` | `multi_agent` | `"design and implement a role-based access control system"` `"add OAuth 2.0 authentication with JWT token refresh"` `"build a highly available distributed notification system"` |

When confidence is below `auto_mode_threshold`, or when `auto_mode_llm_fallback = true` and a cheap LLM is configured, the system falls back to LLM-based classification — which works for **any language** without per-language regex patterns.

Force a specific mode via CLI to bypass auto-selection:

```bash
harness run --mode pair_coding "fix typo"       # force pair_coding
harness run --mode multi_agent "add comment"     # force full team
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

---

## Configuration Reference

Harness uses a two-file config system: `harness.toml` (shared, committed) + `harness.local.toml` (secrets, git-ignored). The local file deep-merges on top of the base file — sections and keys in `harness.local.toml` override those in `harness.toml`.

### `[llm]` — LLM Provider (harness.local.toml)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | str | `"anthropic"` | LLM provider: `anthropic`, `openai`, `deepseek`, `groq`, `openrouter`, `ollama` |
| `model` | str | `"claude-sonnet-4-6-20250514"` | Model name passed to LiteLLM (e.g. `"gpt-4o"`, `"deepseek-chat"`) |
| `fallback_model` | str | `"claude-haiku-3-5-20251001"` | Cheaper model for low-complexity tasks when auto_mode is enabled |
| `expensive_model` | str | `""` | Strongest model for reviews/architecture tasks (e.g. `"claude-opus-4-7"`) |
| `api_key` | str | `""` | API key for the provider — **keep in harness.local.toml** |
| `api_base` | str | `""` | Custom API base URL for proxies or private deployments |
| `max_tokens` | int | `8192` | Maximum tokens in LLM response |
| `temperature` | float | `0.0` | Sampling temperature (0.0 = deterministic) |

Env var overrides: `HARNESS_MODEL`, `HARNESS_PROVIDER` (api_key is **not** overridable via env var).

### `[loop]` — Agent Loop (harness.toml)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `engine` | `"native"` \| `"langgraph"` | `"native"` | Agent loop engine: `native` (async loop) or `langgraph` (StateGraph) |
| `mode` | `"standard"` \| `"pair_coding"` \| `"multi_agent"` | `"standard"` | Agent mode (langgraph only): single agent, coder+reviewer, or controller+implementers |
| `max_turns` | int (1–500) | `500` | Maximum tool-calling iterations before forced termination |
| `compaction_threshold` | float (0.5–0.95) | `0.80` | Token ratio at which MICRO compaction triggers (stubs old tool results) |
| `human_approval` | bool | `true` | Require CLI user approval for write/exec tools (langgraph pair_coding mode) |
| `max_review_iterations` | int (1–20) | `5` | Maximum coder→reviewer→coder cycles (pair_coding/multi_agent) |
| `auto_mode` | bool | `true` | Let ComplexityGate auto-select engine + mode + model based on task analysis |
| `auto_mode_threshold` | float (0.4–0.95) | `0.6` | Confidence threshold for auto_mode classification |
| `auto_mode_llm_fallback` | bool | `false` | Fall back to a cheaper LLM when auto_mode confidence is low |

### `[sandbox]` — Code Execution (harness.toml)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `runtime` | str | `"docker"` | Sandbox runtime: `"docker"` (isolated container) or `"noop"` (host execution, dev only) |

Docker mode uses: read-only rootfs, no network, no capabilities, 512MB memory limit, UID 1000.

### `[repomap]` — Code Structure Map (harness.toml)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Build a tree-sitter code structure map and inject it into the system prompt |
| `max_map_tokens` | int | `2000` | Token budget for the repository map |

Uses `tree-sitter-language-pack` + PageRank on the import graph to select the most important files.

### `[cache]` — Prompt Cache (harness.toml)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `warm_enabled` | bool | `true` | Enable periodic prompt cache warming to reduce LLM latency |
| `warm_interval_seconds` | int | `240` | Interval between cache warming requests (seconds) |

### `[observability]` — Tracing & Evaluation (harness.toml + harness.local.toml)

Non-sensitive defaults in `harness.toml`, secrets in `harness.local.toml`:

| Key | Type | Default | File | Description |
|-----|------|---------|------|-------------|
| `backend` | `"harness"` \| `"langfuse"` \| `"none"` | `"none"` | harness.toml | Observability backend: `none` (disabled), `langfuse` (external platform) |
| `langfuse_public_key` | str | `""` | local | Langfuse project public key |
| `langfuse_secret_key` | str | `""` | local | Langfuse project secret key |
| `langfuse_host` | str | `""` | local | Langfuse instance URL (e.g. `"http://localhost:3000"`) |
| `eval_llm_provider` | str | `"openai"` | harness.toml | LLM provider used by ragas evaluation metrics |
| `eval_llm_model` | str | `"gpt-4o-mini"` | harness.toml | Model for evaluation (use cost-effective model — it only judges, doesn't generate) |
| `eval_llm_api_key` | str | `""` | local | API key for the evaluation LLM |
| `eval_llm_api_base` | str | `""` | local | Custom API base URL for the evaluation LLM |

### Full example

**harness.toml** (shared, committed):
```toml
[loop]
engine = "native"
mode = "standard"
max_turns = 500
compaction_threshold = 0.80
human_approval = true
max_review_iterations = 5
auto_mode = true
auto_mode_threshold = 0.6
auto_mode_llm_fallback = false

[sandbox]
runtime = "docker"

[repomap]
enabled = true
max_map_tokens = 2000

[cache]
warm_enabled = true
warm_interval_seconds = 240

[observability]
backend = "none"
eval_llm_provider = "openai"
eval_llm_model = "gpt-4o-mini"
```

**harness.local.toml** (git-ignored, secrets):
```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6-20250514"
fallback_model = "claude-haiku-3-5-20251001"
expensive_model = "claude-opus-4-7"
api_key = "sk-..."
api_base = "https://api.deepseek.com/anthropic"
max_tokens = 8192
temperature = 0.0

[observability]
backend = "langfuse"
langfuse_public_key = "pk-..."
langfuse_secret_key = "sk-..."
langfuse_host = "http://localhost:3000"
eval_llm_api_key = "sk-..."
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
