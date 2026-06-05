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

```
User prompt
  │
  ▼
ContextGatherer ──► System prompt (tools + repomap + memory)
  │
  ▼
┌─ AgenticLoop ───────────────────────────────────┐
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

### Commands
| Command | Description |
|---------|-------------|
| `harness` | Interactive REPL (default) |
| `harness run "prompt"` | One-shot task |
| `harness repl` | Explicit REPL |
| `harness tui` | Full-screen Textual TUI |
| `harness doctor` | System health check |

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

---

## Project Structure

```
python/
├── src/harness/
│   ├── cli/           # CLI entry, REPL, TUI, commands
│   ├── config/        # Pydantic config models
│   ├── core/          # Agent loop, compaction, context, session
│   ├── llm/           # LLM client ABC + LiteLLM provider
│   ├── tools/         # Tool ABC, executor, permissions, 12 built-ins
│   │   └── sandbox/   # Docker + NoOp runtimes
│   ├── memory/        # SQLite MemoryStore
│   ├── repomap/       # tree-sitter tag extraction + PageRank ranking
│   ├── safety/        # Output scanning + leak detection
│   └── logging/       # Structured JSONL task logger
├── tests/             # pytest test suite
├── harness.toml        # Shared config (committed)
├── harness.local.toml  # Secrets (git-ignored)
└── pyproject.toml     # Build + dependency config
```

---

## License

MIT
