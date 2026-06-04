# Harness

AI coding agent CLI — secure, high-performance, local-first. Written in Python.

## Quick Start

```bash
# Install
uv pip install -e .

# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."

# Run a prompt
harness prompt "write hello world in python"

# Or use python -m
python -m harness prompt "explain this code"
```

## Configuration

Harness loads configuration from `harness.toml` (current directory or `~/.harness/harness.toml`).

Minimal config:
```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6-20250514"
```

All providers supported via [LiteLLM](https://github.com/BerriAI/litellm). Set the corresponding `*_API_KEY` env var:
- Anthropic: `ANTHROPIC_API_KEY`
- OpenAI: `OPENAI_API_KEY`
- Groq: `GROQ_API_KEY`
- DeepSeek: `DEEPSEEK_API_KEY`

## Commands

| Command | Description |
|---------|-------------|
| `harness prompt "..."` | Send a one-shot prompt to the agent |
| `harness doctor` | Check system health and configuration |

## Architecture

```
Presentation (CLI) → Application (AgenticLoop) → Domain (Tools/Safety/Memory) → Infra (LLM/Sandbox)
```

See [DESIGN.md](DESIGN.md) for the complete architecture specification.

## Development

```bash
# Install with dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Run a specific test file
pytest tests/test_tools.py -v
```

## Project Structure

```
src/harness/
├── cli/          # CLI layer (typer)
├── config/       # Config system (Pydantic + TOML)
├── core/         # Agentic loop, context, session
├── llm/          # LLM client ABC + providers (LiteLLM)
├── safety/       # Sanitizer, leak detector, pipeline
└── tools/        # Tool ABC, registry, executor, builtins
```

## Roadmap

- [x] Phase 1: Config + LLM + Tools + Core Loop + CLI (~28 files)
- [ ] Phase 2: Compaction + SubAgent + REPL
- [ ] Phase 3: RepoMap (tree-sitter + PageRank)
- [ ] Phase 4: Docker sandbox + MCP
- [ ] Phase 5: TUI + E2E tests

## License

MIT

---

按时间由近到远的顺序，记录开发日志，需要包含：1.所有改动。2.需要强调和注意的点。

## 2026-06-05 — Phase 1 Build

### 改动
- 创建 pyproject.toml (uv/setuptools 构建, litellm 多 provider)
- 创建 harness.toml 默认配置
- 实现 config 层 (Pydantic BaseModel, TOML + env var 加载)
- 实现 llm 层 (LlmClient ABC, LiteLlmProvider via litellm)
- 实现 safety 层 (Sanitizer ahocorasick + LeakDetector regex + SafetyLayer pipeline)
- 实现 tools 层 (Tool ABC, ToolRegistry with cache-stable ordering, ToolExecutor 6-step pipeline, PermissionPolicy)
- 实现 3 个内置工具: file_read, file_write, glob_search
- 实现 core 层 (AgenticLoop 状态机, ChatDelegate, ContextGatherer, Session/Thread/Turn 数据模型)
- 实现 cli 层 (typer app: 'prompt' and 'doctor' commands)
- 实现错误分类体系 (HarnessError → LlmError/ToolError/SafetyError/...)
- 编写测试: test_config, test_tools (10 tests), test_loop (2 tests)
- 更新 README.md (安装说明、架构概览、开发指南)

### 注意事项
- LiteLLM 按模型名自动路由到对应 provider (claude-sonnet-4-6 → Anthropic, gpt-4o → OpenAI)
- ToolRegistry 保持内置工具在前作为连续前缀，保证 prompt cache 稳定性
- SafetyLayer 仅扫描 tool output，不阻断 — 阻断逻辑由 ToolExecutor 决定
- AgenticLoop 支持 tool_calls 循环 + text response 退出，max_turns 保护
