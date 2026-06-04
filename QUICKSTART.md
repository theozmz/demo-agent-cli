# Harness Quick Start

5 分钟上手 Harness — AI 编程 Agent CLI。

## 1. 环境准备

- Python 3.12+
- uv（推荐）或 pip

```bash
# 安装 uv（如未安装）
pip install uv

# 克隆项目
git clone <repo-url> harness-python
cd harness-python
```

## 2. 安装

```bash
# 创建虚拟环境
uv venv

# 安装 Harness + 开发依赖
uv pip install -e ".[dev]"
```

## 3. 配置 API Key

Harness 通过 LiteLLM 支持所有主流 LLM provider。至少配置一个：

```bash
# Anthropic（推荐 — Claude 在编程任务上表现最佳）
export ANTHROPIC_API_KEY="sk-ant-..."

# 或 OpenAI
export OPENAI_API_KEY="sk-..."

# 或 Groq（快速 + 免费额度）
export GROQ_API_KEY="gsk_..."

# 或 DeepSeek
export DEEPSEEK_API_KEY="sk-..."
```

## 4. 验证安装

```bash
# 健康检查
harness doctor
```

输出示例：
```
Harness Doctor

OK Config found: /path/to/harness.toml
  Model: claude-sonnet-4-6-20250514
  Provider: anthropic
OK ANTHROPIC_API_KEY set
OK Python 3.12.11
```

## 5. 第一次对话

```bash
# 简单问候
harness prompt "say hello in one word"

# 代码生成
harness prompt "write a python function that checks if a string is a palindrome"

# 使用工具
harness prompt "read the README.md file and summarize it in one sentence"

# 指定模型
harness prompt "explain recursion" --model gpt-4o

# Debug 模式（打印更多日志）
harness prompt "hi" --debug
```

## 6. 配置文件

创建 `harness.toml`（当前目录）或 `~/.harness/harness.toml`（全局）：

```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6-20250514"
max_tokens = 8192

[loop]
max_turns = 30
```

完整配置项见项目根目录的 `harness.toml`。

## 7. 运行测试

```bash
# 全部测试
uv run pytest -v

# 特定模块
uv run pytest tests/test_tools.py -v
uv run pytest tests/test_loop.py -v
```

## 8. 项目结构速览

```
src/harness/
├── cli/app.py           # 命令行入口 (typer)
├── config/config.py     # 配置系统 (Pydantic)
├── core/loop.py         # AgenticLoop 状态机
├── llm/providers/       # LLM provider (LiteLLM)
├── safety/              # 注入检测 + 密钥扫描
└── tools/builtin/       # 内置工具 (file_read/write, glob_search)
```

## 9. 支持的所有 Provider

得益于 LiteLLM，任何有 API key 的 provider 都可以直接使用：

| Provider | 模型示例 | 环境变量 |
|----------|---------|---------|
| Anthropic | `claude-sonnet-4-6-20250514` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o`, `gpt-4o-mini` | `OPENAI_API_KEY` |
| Groq | `groq/llama-3.3-70b` | `GROQ_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet` | `OPENROUTER_API_KEY` |
| Ollama (本地) | `ollama/llama3` | 无需 |

## 10. 下一步

- 阅读 [DESIGN.md](DESIGN.md) 了解完整架构设计
- 阅读 [README.md](README.md) 了解开发指南和路线图
- 探索 `src/harness/core/loop.py` 理解 Agent 循环实现
- 在 `src/harness/tools/builtin/` 目录下添加自定义工具
