# Harness

**AI 编程助手 CLI — 安全、高性能、本地优先。**

Harness 是一个运行在终端中的 AI 编程助手。它能读取、编写、执行代码，通过 LiteLLM 支持任意 LLM 提供商，在沙箱环境中安全运行。

---

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 配置 LLM
cp harness.toml harness.local.toml
# 编辑 harness.local.toml — 设置 provider、model、api_key、api_base

# 3. 运行任务
harness run "创建一个打印当前时间的 Python 脚本"
```

或进入交互模式（REPL）：

```bash
harness
# > 读取 src/harness/core/loop.py 并总结 agent loop 的结构
```

### Windows 一键部署

```cmd
git clone <repo> && cd python && setup.bat
```

编辑生成的 `harness.local.toml` 填入 API key，运行 `.venv\Scripts\harness.exe`。

---

## 架构

```
┌─────────────────────────────────────────────────┐
│  表现层                                         │
│  CLI (argparse) → REPL (prompt_toolkit)         │
│  TUI (Textual) → Rich Markdown 输出             │
├─────────────────────────────────────────────────┤
│  应用层                                         │
│  AgenticLoop → ChatDelegate → CompactionEngine  │
│  ContextGatherer → RepoMap → SafetyLayer        │
├─────────────────────────────────────────────────┤
│  领域层                                         │
│  Tool ABC → ToolExecutor → PermissionPolicy     │
│  LlmClient ABC → SandboxRuntime ABC             │
│  MemoryStore → Session/Thread/Turn 模型          │
├─────────────────────────────────────────────────┤
│  基础设施层                                     │
│  LiteLLM 提供商 → Docker 沙箱                   │
│  tree-sitter → SQLite → prompt_toolkit          │
└─────────────────────────────────────────────────┘
```

### Agent 循环（查询 → LLM → 工具 → 观察 → 重复）

```
用户输入
  │
  ▼
ContextGatherer ──► 系统提示词（工具 + repomap + 记忆）
  │
  ▼
┌─ AgenticLoop ───────────────────────────────────┐
│                                                 │
│  1. call_llm() ──► LLM 响应                     │
│  2. 如果有 tool_calls: 执行工具, 追加结果        │
│  3. 如果是纯文本: 返回给用户                      │
│  4. 压缩检查 (MICRO / REACTIVE)                  │
│  5. 重复（最多 30 轮）                           │
│                                                 │
│  实时事件: thinking → tool_call →               │
│  tool_result → retry → done                      │
└─────────────────────────────────────────────────┘
  │
  ▼
Rich Markdown 输出 + JSONL 会话日志
```

---

## 技术细节

### LLM 提供商（LiteLLM）
多提供商支持：Anthropic、OpenAI、DeepSeek、Groq、OpenRouter、Ollama。通过 `harness.toml` 的 `[llm]` 段配置。密钥放在 `harness.local.toml`（git-ignored）。环境变量覆盖：`HARNESS_MODEL`、`HARNESS_PROVIDER`。

### 工具系统（12 个内置工具）
| 类别 | 工具 |
|------|------|
| 文件 I/O | `file_read`、`file_write`、`file_edit` |
| 搜索 | `glob_search`、`grep_search`（ripgrep）|
| 网络 | `web_fetch`、`web_search` |
| 执行 | `bash_exec`（Docker 沙箱或 NoOp）|
| 记忆 | `memory_read`、`memory_write`、`memory_delete` |
| 代理 | `agent`（子代理委派）|

每个工具调用经过 6 步管道：**查找 → 校验（JSON Schema）→ 权限检查 → 执行 → 安全扫描 → 日志记录**。

### 沙箱
- **Docker**：容器使用 `read_only` 根文件系统、无网络、无 capabilities、512MB 内存限制、UID 1000。
- **NoOp 回退**：Docker 不可用时在宿主机运行（仅开发/测试）。

### 记忆系统（SQLite）
持久化键值存储，位于 `~/.harness/memory.db`。WAL 模式支持多进程安全。Agent 工具可读写删。计划：自动将相关记忆注入系统提示词。

### RepoMap（tree-sitter + PageRank）
可选的仓库结构映射，注入系统提示词。使用 `tree-sitter-language-pack` 解析代码为标签（类、函数、方法），通过导入图的 PageRank 排序文件，在 token 预算内选取最重要的文件。

### LangGraph 多智能体协作
Harness 包含完整的基于 LangGraph 的多智能体系统，支持两种协作模式：

**结对编程模式**（`mode = "pair_coding"`）：
- Coder 智能体：根据任务和审查反馈生成/修改代码
- Reviewer 智能体：结构化 JSON 审查（决策 + 严重性 + 评论）
- 人机交互中断：LangGraph `interrupt_before` 暂停等待 CLI 用户审批
- 条件循环：APPROVED → done, CHANGES_REQUESTED → 返回 coder

**多智能体协作模式**（`mode = "multi_agent"`）：
- Controller：分解计划为依赖排序的任务列表，标记复杂度，绝不写代码
- Implementer 子智能体：执行单个任务，具有写权限和精选上下文（上下文隔离）
- Spec Reviewer：对照计划验证功能正确性（始终使用最强模型）
- Code Quality Reviewer：评估代码结构和质量（仅在规范通过后执行）
- Remediation 循环：审查失败创建修复任务，重新路由到实现者

**配置方式**：
```toml
[loop]
engine = "langgraph"
mode = "pair_coding"        # "standard" | "pair_coding" | "multi_agent"
human_approval = true
max_review_iterations = 5
```

### 上下文压缩
两级策略防止 token 溢出：
- **MICRO**（>80% tokens）：将旧只读工具结果替换为 `[stub: ... N chars]`
- **REACTIVE**（>90% tokens）：仅保留最后 5 轮对话，注入截断通知
- **TruncationTracker**：连续 3 次压缩后停止，防止抖动

### LLM 重试
3 次重试，指数退避（1s → 3s → 7s）。临时错误（超时、连接、限流、5xx）重试。永久错误（认证、无效请求）快速失败。

### 工作区隔离
`-w / --workspace` 标志限制所有文件工具访问到指定目录。尝试读写工作区外的路径将被拦截。路径解析：相对路径相对于工作区根目录。

### 任务日志
每个会话写入结构化 JSONL 日志到 `logs/<session_id>.jsonl`。事件类型：`task_start`、`context`、`llm_call`、`tool_call`、`memory_op`、`task_end`。敏感参数（api_key、password、token）已脱敏。

### 命令
| 命令 | 说明 |
|------|------|
| `harness` | 交互式 REPL（默认）|
| `harness run "提示"` | 单次任务 |
| `harness repl` | 显式 REPL |
| `harness tui` | 全屏 Textual TUI |
| `harness doctor` | 系统健康检查 |

全局参数：`-c/--config PATH`、`-d/--debug`。Run 参数：`-p/--provider`、`-m/--model`、`-n/--max-turns`、`-r/--repomap`、`-w/--workspace`。

---

## 亮点

- **多提供商**：一套接口，任意 LLM。无供应商锁定。
- **Docker 沙箱**：真实的容器隔离，安全执行代码。
- **实时进度**：实时查看 thinking、工具调用和结果。
- **会话日志**：每次 LLM 调用和工具执行记录为结构化 JSONL。
- **子进程 REPL**：每个任务作为子进程运行 — 崩溃隔离，永不丢失会话。
- **工作区边界**：将 agent 文件访问限制在指定目录。
- **压缩 + 重试**：优雅处理长对话和临时 LLM 故障。
- **可扩展工具**：清晰的 ABC 工具系统，继承 `Tool` 即可添加新工具。
- **配置分层**：`harness.toml`（共享）+ `harness.local.toml`（密钥，git-ignored）。
- **LangGraph 多智能体**：结对编程（coder + reviewer + 人工审批）和多智能体协作（controller + implementers + 两阶段审查）。自主复杂度评估与分层模型路由。
- **子智能体拓扑**：顺序链、并行扇出、树形嵌套和DAG依赖调度 — 灵活的编排模式。
- **两阶段审查流水线**：规范合规（功能正确性）→ 代码质量（结构/风格）— 审查始终使用最强模型。

---

## 项目结构

```
python/
├── src/harness/
│   ├── cli/           # CLI 入口、REPL、TUI、命令
│   ├── config/        # Pydantic 配置模型
│   ├── core/          # Agent 循环、压缩、上下文、会话、子智能体
│   ├── langgraph/     # LangGraph 多智能体图、节点、委托
│   │   └── nodes/     # 结对编程 + 多智能体协作节点
│   ├── llm/           # LLM 客户端 ABC + LiteLLM 提供商
│   ├── tools/         # 工具 ABC、执行器、权限、12 个内置工具
│   │   └── sandbox/   # Docker + NoOp 运行时
│   ├── memory/        # SQLite MemoryStore
│   ├── repomap/       # tree-sitter 标签提取 + PageRank 排序
│   ├── safety/        # 输出扫描 + 泄漏检测
│   └── logging/       # 结构化 JSONL 任务日志
├── tests/             # pytest 测试套件
├── harness.toml        # 共享配置（提交到 git）
├── harness.local.toml  # 密钥（git-ignored）
└── pyproject.toml     # 构建 + 依赖配置
```

---

## 许可证

MIT
