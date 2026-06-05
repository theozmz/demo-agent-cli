# Harness Quick Start

5 分钟上手 Harness — AI 编程 Agent CLI。

## 1. 环境准备

- Python 3.12+
- uv（推荐）或 pip

```bash
# 安装 uv（如未安装）
pip install uv

# 进入项目
cd python
```

## 2. 安装

```bash
# 创建虚拟环境
uv venv

# 安装 Harness + 开发依赖
uv pip install -e ".[dev]"
```

## 3. 配置 API Key

Harness 通过 LiteLLM 支持所有主流 LLM provider。两种配置方式：

### 方式 A：写在 `harness.toml` 中（推荐，启动时自动读取）

```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6-20250514"
api_key = "sk-ant-..."        # 直接写在配置文件中
api_base = ""                  # 留空使用官方端点，或填写代理地址
```

### 方式 B：设置环境变量（配置文件未填写 api_key 时的 fallback）

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GROQ_API_KEY="gsk_..."
export DEEPSEEK_API_KEY="sk-..."
export OPENROUTER_API_KEY="sk-or-..."
```

**优先级**：配置文件 `api_key` > 环境变量 `{PROVIDER}_API_KEY`。当 `-p` 切换 provider 时，环境变量强制覆盖（因为配置文件中的 key 属于原 provider）。

## 4. 启动方式

```bash
# 方式 1：激活虚拟环境后直接使用 harness 命令
# Windows:
.venv\Scripts\activate
harness --help

# Linux / macOS:
source .venv/bin/activate
harness --help

# 方式 2：不激活，直接用 venv 中的绝对路径
.venv/Scripts/harness --help          # Windows
.venv/bin/harness --help              # Linux / macOS

# 方式 3：通过 python -m 启动（不需要 install，源码即可运行）
.venv/Scripts/python -m harness --help
python -m harness --help
```

## 5. 验证安装

```bash
# 激活虚拟环境后运行（推荐）
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/macOS

harness doctor
```

或直接使用绝对路径：

```bash
# Windows
.venv/Scripts/harness doctor

# Linux / macOS
.venv/bin/harness doctor
```

输出示例：
```
Harness Doctor

OK Config found: D:\code\harness\python\harness.toml
  Provider: anthropic
  Model:    claude-sonnet-4-6-20250514
OK ANTHROPIC_API_KEY set (Anthropic)
OK Python 3.12.11

Run 'harness run "hello"' to test the agent.
```

## 6. 第一次对话

> 以下示例假设已激活虚拟环境。如未激活，将 `harness` 替换为 `.venv/Scripts/harness`（Windows）或 `.venv/bin/harness`（Linux/macOS）。

```bash
# 简单问候
harness run "say hello in one word"

# 代码生成
harness run "write a python function that checks if a string is a palindrome"

# 使用工具（读文件、写文件、glob 搜索）
harness run "read the README.md file and summarize it in one sentence"

# 指定模型（覆盖配置文件中的默认模型）
harness run "explain recursion" -m gpt-4o

# 限制最大回合数
harness run "write a fibonacci function" -n 10

# Debug 模式（打印详细日志和耗时统计）
harness run "hi" -d

# 使用自定义配置文件
harness -c /path/to/custom.toml run "hello"

# 查看子命令帮助
harness run --help
harness doctor --help
```

## 7. 配置文件

创建 `harness.toml`（当前目录）或 `~/.harness/harness.toml`（全局）：

```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6-20250514"
fallback_model = "claude-haiku-3-5-20251001"
# 以下两项可选——不写则从环境变量 fallback
api_key = ""                   # 直接填写 API Key（优先于环境变量）
api_base = ""                  # 自定义 API 端点（代理/私有部署）
max_tokens = 8192

[loop]
engine = "native"
max_turns = 30
compaction_threshold = 0.80

[sandbox]
runtime = "docker"

[repomap]
enabled = false

[cache]
warm_enabled = false

[observability]
backend = "none"
```

Programmatic 切换 provider：

```bash
# 方式 1：修改 harness.toml 中的 provider 和 model
# provider = "openai"
# model = "gpt-4o"

# 方式 2：通过环境变量覆盖
export HARNESS_PROVIDER="openai"
export HARNESS_MODEL="gpt-4o"
```

## 8. 运行测试

```bash
# 全部测试
uv run pytest -v

# 特定模块
uv run pytest tests/test_tools.py -v
uv run pytest tests/test_loop.py -v
uv run pytest tests/test_config.py -v
```

## 9. 项目结构速览

```
src/harness/
├── cli/
│   ├── main.py              # argparse 主入口（全局 flag + 子命令分发）
│   ├── context.py           # AppContext — 初始化阶段产物
│   └── commands/
│       ├── run.py           # harness run 子命令
│       └── doctor.py        # harness doctor 子命令
├── config/config.py         # 配置系统 (Pydantic + TOML + env var)
├── core/
│   ├── loop.py              # AgenticLoop 状态机 + ChatDelegate
│   ├── loop_delegate.py     # LoopDelegate ABC + LoopContext
│   ├── context.py           # ContextGatherer — 系统提示词组装
│   ├── session.py           # Session/Thread/Turn 数据模型
│   └── errors.py            # 错误分类体系
├── llm/
│   ├── client.py            # LlmClient ABC
│   ├── types.py             # ChatMessage, LlmResponse, ToolCall
│   └── providers/
│       └── litellm_provider.py  # LiteLLM 多 provider 集成
├── safety/
│   ├── sanitizer.py         # 注入检测 (Aho-Corasick)
│   ├── leak_detector.py     # 密钥/凭据扫描 (regex)
│   └── pipeline.py          # SafetyLayer 组合
└── tools/
    ├── tool.py              # Tool ABC + ToolOutput + ApprovalRequirement
    ├── registry.py          # ToolRegistry — 注册、查找、cache 友好排序
    ├── executor.py          # ToolExecutor — 6 步执行管线
    ├── permissions.py       # PermissionPolicy 权限决策
    └── builtin/
        ├── file_read.py     # FileReadTool
        ├── file_write.py    # FileWriteTool
        └── glob_search.py   # GlobSearchTool
```

## 10. CLI 完整参考

```
harness [-h] [-c CONFIG] [-d] {run,doctor} ...

全局选项:
  -c, --config PATH   配置文件路径（默认查找 ./harness.toml, ~/.harness/harness.toml）
  -d, --debug         启用 DEBUG 日志
  -h, --help          显示帮助

子命令:
  run "prompt"        发送一次性 prompt 给 Agent
    -m, --model       覆盖配置文件中的模型名
    -n, --max-turns   最大工具调用回合数（默认 30）

  doctor              系统健康检查
    （无额外选项）
```

### 初始化流程

每次运行 `harness` 命令时，都会先执行**初始化阶段**：

1. 加载 `harness.toml` 配置（cwd → `~/.harness/` → Pydantic 默认值）
2. 应用 `HARNESS_PROVIDER` / `HARNESS_MODEL` 环境变量覆盖
3. 根据 `provider` 字段自动查找对应的 `*_API_KEY` 环境变量
4. 创建 LLM 客户端（LiteLLM，按模型名自动路由 provider）
5. 构建工具注册表 + 安全层 + 工具执行器
6. 构建上下文收集器（系统提示词）

初始化完成后，命令处理函数只负责组装 `AgenticLoop` 并执行——不再包含基础设施创建逻辑。

## 11. 支持的所有 Provider

得益于 LiteLLM，任何有 API key 的 provider 都可以直接使用：

| Provider | 模型示例 | 环境变量 |
|----------|---------|---------|
| Anthropic | `claude-sonnet-4-6-20250514` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o`, `gpt-4o-mini` | `OPENAI_API_KEY` |
| Groq | `groq/llama-3.3-70b` | `GROQ_API_KEY` |
| DeepSeek | `deepseek/deepseek-chat` | `DEEPSEEK_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet` | `OPENROUTER_API_KEY` |
| Ollama (本地) | `ollama/llama3` | 无需 |

Provider 的识别与 API key 查找由 `config.py` 中的 `_PROVIDER_KEY_ENV_VARS` 集中管理，初始化阶段调用 `Config._apply_env_overrides()` + `AppContext.initialize()` 完成。

## 12. 下一步

- 阅读 [DESIGN.md](DESIGN.md) 了解完整架构设计
- 阅读 [README.md](README.md) 了解开发指南和路线图
- 探索 `src/harness/core/loop.py` 理解 Agent 循环实现
- 探索 `src/harness/cli/context.py` 理解初始化阶段
- 在 `src/harness/tools/builtin/` 目录下添加自定义工具
