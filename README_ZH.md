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

每个工具调用经过 6 步管道：**查找 → 校验（JSON Schema）→ 权限检查 → 执行 → 安全扫描 → 日志记录**。

#### 文件 I/O

| 工具 | 参数 | 审批 | 只读 | 说明 |
|------|-----------|----------|-----------|-------------|
| `file_read` | `file_path` *(必填)*、`offset` (int)、`limit` (int, 最大 2000) | NEVER | 是 | 读取文件，支持行偏移和行数限制。自动检测二进制文件。 |
| `file_write` | `file_path` *(必填)*、`content` *(必填)* | UNLESS_AUTO | 否 | 写入或覆盖文件。自动创建父目录。 |
| `file_edit` | `file_path` *(必填)*、`old_string` *(必填)*、`new_string` *(必填)*、`replace_all` (bool) | UNLESS_AUTO | 否 | 精确字符串替换。`old_string` 在文件中必须唯一，除非设置 `replace_all=true`。修改前应先读取文件。 |

#### 搜索

| 工具 | 参数 | 审批 | 只读 | 说明 |
|------|-----------|----------|-----------|-------------|
| `glob_search` | `pattern` *(必填)*、`path`（目录，默认当前目录） | NEVER | 是 | 快速文件模式匹配（如 `**/*.py`、`src/**/*.ts`）。按修改时间排序，最多 500 条。 |
| `grep_search` | `pattern` *(必填)*、`path`（目录）、`glob`（文件过滤）、`output_mode`（`content`/`files_with_matches`/`count`）、`-i` (bool)、`head_limit` (int, 默认 50) | NEVER | 是 | 通过 ripgrep 进行正则内容搜索。支持完整正则语法、大小写不敏感和 glob 过滤。需安装 `rg`。 |

#### 网络

| 工具 | 参数 | 审批 | 只读 | 说明 |
|------|-----------|----------|-----------|-------------|
| `web_fetch` | `url` *(必填)* | UNLESS_AUTO | 是 | 获取 URL 内容并转为 Markdown。HTTP 自动升级为 HTTPS。拦截本地/私有 IP。15 秒超时，结果截断至 50k 字符。 |
| `web_search` | `query` *(必填，最少 2 字符)* | UNLESS_AUTO | 是 | 通过 DuckDuckGo HTML 搜索网页（无需 API Key）。返回最多 20 条结果，含标题、摘要和 URL。 |

#### 执行

| 工具 | 参数 | 审批 | 只读 | 说明 |
|------|-----------|----------|-----------|-------------|
| `bash_exec` | `command` *(必填)*、`timeout` (int, 5–300 秒, 默认 120)、`cwd`（目录，默认 `/workspace`） | UNLESS_AUTO | 否 | 在沙箱容器中执行 Shell 命令。Docker 模式：只读根文件系统、无网络、512MB 限制。惰性创建容器，带健康检查和故障自动重建。 |

#### 记忆

| 工具 | 参数 | 审批 | 只读 | 说明 |
|------|-----------|----------|-----------|-------------|
| `memory_read` | `key` *(必填)* | NEVER | 是 | 按键读取持久化记忆。返回存储的值或"未找到记忆"。 |
| `memory_write` | `key` *(必填)*、`value` *(必填)* | UNLESS_AUTO | 否 | 创建或更新持久化键值对。跨会话保留。 |
| `memory_delete` | `key` *(必填)* | UNLESS_AUTO | 否 | 按键删除记忆。返回确认或"未找到记忆"。 |

记忆存储于 SQLite 数据库 `~/.harness/memory.db`（WAL 模式）。键使用 slug 风格标识符（如 `user-pref-editor`、`project-stack`）。

#### 编排

| 工具 | 参数 | 审批 | 只读 | 说明 |
|------|-----------|----------|-----------|-------------|
| `agent` | `description` *(必填)*、`prompt` *(必填)*、`subagent_type`（`claude`/`explore`/`general-purpose`/`plan`）、`max_turns` (int, 默认 50) | UNLESS_AUTO | 是 | 启动只读子智能体，用于研究、代码探索或信息收集。子智能体可访问 `file_read`、`glob_search`、`grep_search`、`web_fetch`、`web_search`。最大深度 2 层，每会话最多 50 个子智能体。 |

#### 审批级别

| 级别 | 行为 |
|-------|----------|
| `NEVER` | 始终允许，不弹窗提示。用于只读工具（`file_read`、`glob_search`、`grep_search`、`memory_read`）。 |
| `UNLESS_AUTO` | 自动/非交互模式下无需提示直接放行。交互模式下弹窗确认。用于写入/执行类工具。 |

### 沙箱
- **Docker**：容器使用 `read_only` 根文件系统、无网络、无 capabilities、512MB 内存限制、UID 1000。
- **NoOp 回退**：Docker 不可用时在宿主机运行（仅开发/测试）。

### 记忆系统（SQLite）
持久化键值存储，位于 `~/.harness/memory.db`。WAL 模式支持多进程安全。Agent 工具可读写删。计划：自动将相关记忆注入系统提示词。

### RepoMap（tree-sitter + PageRank）
可选的仓库结构映射，注入系统提示词。使用 `tree-sitter-language-pack` 解析代码为标签（类、函数、方法），通过导入图的 PageRank 排序文件，在 token 预算内选取最重要的文件。

### 多智能体系统 — 四种协作模式

Harness 有四种执行模式，从单智能体到完整多智能体编排。**ComplexityGate** 根据任务复杂度自动选择最佳模式，也可以通过 CLI 强制指定。

| # | 模式 | 引擎 | 参与智能体 | 适用场景 |
|---|------|--------|--------|----------|
| 1 | **Native 标准** | `native` | 1 个 agent（ChatDelegate） | 简单：重命名、拼写、单函数 |
| 2 | **LangGraph 标准** | `langgraph` | 1 个 agent + reviewer | 基础任务，内置审查 |
| 3 | **结对编程** | `langgraph` | Coder + Reviewer + 人工审批 | 跨模块：API、重构、迁移 |
| 4 | **多智能体协作** | `langgraph` | Controller + Implementer(s) + 2× Reviewer + Remediation | 架构级：设计、安全、认证 |

所有 LangGraph 模式共享 **单个 TypedDict 状态对象**。智能体之间通过读写类型化状态字段进行通信 — 没有直接的智能体间消息传递。

---

#### 模式 1：Native 标准

```
用户 → AgenticLoop → ChatDelegate → LLM → tools → observe → repeat
```

**触发方式：** 默认模式。当 `engine = "native"` 或 ComplexityGate 将任务分类为 SIMPLE 时使用。

**通信方式：** `ChatDelegate` 携带完整 `LoopContext`（消息列表 + 系统提示词 + 工具 schema）调用 `llm.generate()`。工具结果以 `ChatMessage.tool_result()` 形式追加到 `ctx.messages`。除消息列表外无其他共享状态。

**结束条件：**
- LLM 返回纯文本（无工具调用）→ `outcome.kind = "completed"`
- 超过 `max_turns` → `outcome.kind = "max_turns"`
- LLM 异常 → `outcome.kind = "error"`
- delegate 发出 `LoopSignal.STOP` → `outcome.kind = "stopped"`

---

#### 模式 2：LangGraph 标准

```
ENTRY → agent → reviewer → END
```

**触发方式：** 当 `engine = "langgraph"` 且 `mode = "standard"` 时。内部构建为 `pair_coding_graph`，但 `interrupt_on_approval=False`，`max_review_iterations=1`。

**通信方式：** 单个共享 `BaseAgentState`，包含 `messages`（通过 LangGraph 的 `add_messages` reducer 自动累积）、`iteration`、`max_iterations` 和 `terminal_reason`。

**结束条件：** agent 产出 → reviewer 验证 → `terminal_reason = "completed"`。出错时：`terminal_reason = "max_turns"` 或 `"error"`。

---

#### 模式 3：结对编程

```
ENTRY → coder → reviewer → human_approval ─┬─[APPROVED]──→ done → END
                                    ▲       │
                                    │       └─[CHANGES_REQUESTED]→ coder（循环）
                                    │
                                    └── 条件边：_route_after_approval
```

**触发方式：** ComplexityGate 将任务分类为 INTEGRATION，或用户强制 `--mode pair_coding`。通过 `harness.toml` 配置：
```toml
[loop]
engine = "langgraph"
mode = "pair_coding"
human_approval = true          # 暂停等待 CLI 用户确认
max_review_iterations = 5      # 最多 coder→reviewer 循环次数
```

**通过共享 `PairCodingState` 字段通信：**

| 步骤 | 写入者 → 读取者 | 字段 | 内容 |
|------|----------------|-------|---------|
| 1 | 用户 → Coder | `task` | 原始任务描述 |
| 2 | Coder → Reviewer | `code` | 生成/修改后的代码 |
| 3 | Reviewer → Human | `review_comments` | 列表：`{severity, file, line, comment}` |
| 3 | Reviewer → Router | `final_decision` | `"APPROVED"` 或 `"CHANGES_REQUESTED"` |

循环回退时（`CHANGES_REQUESTED`）：Coder 读取 `review_comments` + 当前 `code`，生成修订版，清空 `review_comments` 供新一轮审查。

**结束条件：**

| 条件 | 路由 | `terminal_reason` |
|-----------|-------|-------------------|
| `final_decision = "APPROVED"` | human_approval → done | `"approved"` |
| `review_iteration >= max_review_iterations`（5） | human_approval → done | `"max_review_iterations"` |

**人机交互中断：** 图编译时设置 `interrupt_before=["human_approval"]`。Reviewer 给出审查意见后，图**暂停**。CLI 展示审查意见，询问用户批准或要求修改。`LangGraphDelegate.resume_with_approval(decision)` 在中断点注入 `{"final_decision": decision}` 并继续执行。

**容错处理：** Coder 的 LLM 失败保留旧代码并递增计数器（reviewer 可拒绝旧代码）。Reviewer 的 LLM 失败默认返回 `APPROVED`（fail-progress）。

---

#### 模式 4：多智能体协作（Controller + 团队）

```
ENTRY → controller → task_router ─┬─→ implementer → result_collector ───┐
                                  │                                      │
                                  │◄─────────────────────────────────────┘
                                  │  （循环：选取下一个就绪任务）
                                  │
                                  ├─[全部完成, review=spec]→ spec_reviewer → code_quality_reviewer
                                  │                                                    │
                                  │                              ┌─[未通过]→ remediation ─┐
                                  │                              │                       │
                                  │                              └──→ task_router ◄──────┘
                                  │                                   （注入修复任务）
                                  │
                                  └─[阻塞/错误] → finalize → END
```

**触发方式：** ComplexityGate 将任务分类为 ARCHITECTURE，或用户强制 `--mode multi_agent`。配置：
```toml
[loop]
engine = "langgraph"
mode = "multi_agent"
max_review_iterations = 3       # 最多修复循环次数
```

**8 个智能体节点：**

| 节点 | 角色 | 模型 | 有工具？ |
|------|------|-------|------------|
| `controller` | 📋 将计划分解为带依赖 DAG 的 `task_list` | default（Sonnet） | 否 — 纯文本 |
| `task_router` | 🔀 拓扑排序，选取下一个就绪任务 | 纯逻辑 | 否 — 无 LLM |
| `implementer` | 💻 执行单个任务 — 生成带写权限的 `AgenticLoop` 子智能体 | 按任务 tier | 是 — 全部文件/编辑/执行工具 |
| `result_collector` | 📥 收集结果，更新 `completed_tasks` | 纯逻辑 | 否 |
| `spec_reviewer` | ✅ 对照原始计划验证实现 | expensive（Opus） | 是 — file_read 验证 |
| `code_quality_reviewer` | 🔍 评估结构、模块化、命名 | expensive（Opus） | 是 — file_read |
| `remediation` | 🔧 为未通过的审查创建修复 `TaskItem` | 纯逻辑 | 否 |
| `finalize` | 🏁 汇总所有结果组装 `final_code` | 纯逻辑 | 否 |

**DAG 调度（task_router）：** 任务携带 `dependencies: list[task_id]` 字段。当某任务的所有依赖 ID 都在 `completed_tasks` 中时，该任务"就绪"。Router 选第一个就绪任务，标记 `IN_PROGRESS`。全部任务 `DONE` 后，设置 `review_stage = "spec"` 触发审查阶段。

**并行扇出：** 当 `fan_out=True` 时，所有无依赖任务通过 `asyncio.gather()` 并行执行。

**通过共享 `MultiAgentState` 字段通信：**

| 步骤 | 写入者 → 读取者 | 字段 |
|------|----------------|-------|
| Controller → Router | `plan`、`task_list` | |
| Router → Implementer | `task_list[].status`（`IN_PROGRESS`）、`current_task_index` | |
| Implementer → Collector | `task_list[].result`、`implementation_results` | |
| Collector → Router | `completed_tasks`、`pending_tasks` | |
| Router → Reviewers | `plan`、`implementation_results` | |
| Reviewers → Remediation | `spec_review`、`code_quality_review` | |
| Remediation → Router | 新 `TaskItem` 追加到 `task_list` | |

**两阶段审查流水线：**
1. **Spec Reviewer** 读取 plan + `implementation_results`，通过 tool_executor 读取实际文件，验证功能正确性。返回 `ReviewResult{passed, issues}`。
2. **Code Quality Reviewer** 仅在 spec 通过后执行。评估关注点分离、文件增长、命名、错误处理。**明确不复检**功能正确性。
3. 两个审查者始终使用 **最强模型**（Opus）。

**结束条件：**

| 条件 | 路由 | `terminal_reason` |
|-----------|-------|-------------------|
| 全部任务完成 + 两个审查均通过 | code_quality_reviewer → finalize | `"completed"` |
| DAG 阻塞（无法解决的依赖） | task_router → finalize | `"blocked"` |
| Controller 错误 | controller → finalize | `"error"` |
| 修复循环达到 3 次上限 | remediation → finalize | `"max_review_iterations"` |

**修复循环：** 任一审查未通过时，`remediation` 为每个问题创建一个 `TaskItem`（complexity=`"simple"`，无依赖），追加到 `task_list`。重置 `review_stage = "spec"`，递增 `review_iteration`。task_router 立即拾取修复任务。最多 3 个循环。

**子智能体实现：** 每个 `implementer` 任务生成一个 `AgenticLoop`，携带有限上下文（仅 plan + 任务描述，不含完整对话历史）、完整写权限、`max_turns=10`、`subagent_depth=1`。这提供了上下文隔离 — 实现者只看自己需要的内容。

**人机交互中断：** 未配置。多智能体图从 controller 到 finalize 完全自主运行。

---

#### 自动模式：任务驱动智能体选择

当 `auto_mode = true` 时，**ComplexityGate** 会分析你的提示词，自动选择最合适的引擎 + 模式。

| 复杂度 | 英文触发词 | 中文触发词 | 引擎 | 模式 | 示例提示词 |
|------|----------|----------|------|------|-----------------|
| **SIMPLE** | rename, fix typo, add comment, single function, basic CRUD | 重命名, 修正, 拼写, 格式化, 加注释, 单个函数, 增删改查 | `native` | `standard` | `"把 getCwd 重命名为 getCurrentWorkingDirectory"` `"修正 README 中的拼写错误"` `"给 utils.py 添加类型注解"` `"写一个判断质数的函数"` |
| **INTEGRATION** | API, refactor, migrate, database, multiple files, REST, GraphQL | 重构, 迁移, API, 接口, 端点, 数据库, 多模块, 微服务 | `langgraph` | `pair_coding` | `"添加用户注册的 REST API 端点，连接数据库"` `"跨多个模块重构认证逻辑"` `"将数据库查询从原始 SQL 迁移到 ORM"` |
| **ARCHITECTURE** | design, security, OAuth, RBAC, encryption, concurrency, notification system | 架构设计, 认证系统, 权限控制, OAuth, 加密, 并发, 分布式, 系统设计 | `langgraph` | `multi_agent` | `"设计微服务架构并实现基于角色的访问控制系统"` `"添加 OAuth 2.0 认证和 JWT 令牌刷新"` `"构建高可用分布式通知系统"` |

当置信度低于 `auto_mode_threshold`，或 `auto_mode_llm_fallback = true` 且配置了廉价 LLM 时，系统回退到 LLM 分类 — 适用于**任意语言**，无需按语言维护正则。

通过 CLI 强制指定模式来跳过自动选择：

```bash
harness run --mode pair_coding "修复拼写"       # 强制结对编程
harness run --mode multi_agent "添加注释"        # 强制完整团队
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

### 可观测性（Langfuse 追踪）
可选集成 [Langfuse](https://langfuse.com)，实现可视化 Trace 追踪：

```toml
# harness.local.toml
[observability]
backend = "langfuse"
langfuse_public_key = "pk-..."
langfuse_secret_key = "sk-..."
langfuse_host = "http://localhost:3000"
```

每个 agent 会话创建一个 **Trace**。工具调用和 LLM 调用记录为嵌套的 **Observation**（span/generation）。所有事件可在 Langfuse Dashboard 中查看。langfuse 未安装或 `backend = "none"` 时为零开销空操作。

安装方式：`uv pip install -e ".[observability]"`

### 记忆评估（Ragas + Langfuse）
使用 [Ragas](https://docs.ragas.io) 评估指标，从三个维度评估记忆质量：

| 维度 | 指标 | 评估内容 |
|-----------|---------|-----------------|
| 检索（Retrieval） | `context_precision`、`context_recall` | `memory_read` 是否查到了正确的记忆？ |
| 存储（Storage） | `faithfulness` | `memory_write` 存储的内容是否忠实于来源？ |
| 影响（Impact） | `answer_correctness`、`answer_relevancy` | 记忆是否提升了 agent 回答质量？ |

每次评估运行创建一个专用 Langfuse Trace，每个指标结果记录为 Score，实现端到端可追溯：**trace → observation → score → dashboard**。

```bash
# 安装评估依赖
uv pip install -e ".[eval]"

# 在 harness.local.toml 中配置评估 LLM
# [observability]
# eval_llm_api_key = "sk-..."

# 运行评估
harness eval memory                          # 全部维度
harness eval memory --dimension retrieval    # 仅检索维度
harness eval memory --session abc123         # 指定会话
harness eval memory --output report.json     # JSON 输出
harness eval list-metrics                    # 列出可用指标
```

---

## 配置参考

Harness 使用双文件配置系统：`harness.toml`（共享，提交到 git）+ `harness.local.toml`（密钥，git-ignored）。本地文件会深度合并到基础文件之上 — `harness.local.toml` 中的段和键会覆盖 `harness.toml` 中的对应项。

### `[llm]` — LLM 提供商（harness.local.toml）

| 键 | 类型 | 默认值 | 说明 |
|-----|------|---------|------|
| `provider` | str | `"anthropic"` | LLM 提供商：`anthropic`、`openai`、`deepseek`、`groq`、`openrouter`、`ollama` |
| `model` | str | `"claude-sonnet-4-6-20250514"` | 传递给 LiteLLM 的模型名称（如 `"gpt-4o"`、`"deepseek-chat"`） |
| `fallback_model` | str | `"claude-haiku-3-5-20251001"` | 低复杂度任务时使用的廉价模型（需启用 auto_mode） |
| `expensive_model` | str | `""` | 审查/架构任务使用的顶级模型（如 `"claude-opus-4-7"`） |
| `api_key` | str | `""` | 提供商的 API 密钥 — **请放在 harness.local.toml** |
| `api_base` | str | `""` | 自定义 API 端点（代理或私有部署） |
| `max_tokens` | int | `8192` | LLM 响应的最大 token 数 |
| `temperature` | float | `0.0` | 采样温度（0.0 = 确定性输出） |

环境变量覆盖：`HARNESS_MODEL`、`HARNESS_PROVIDER`（api_key 不可通过环境变量覆盖）。

### `[loop]` — Agent 循环（harness.toml）

| 键 | 类型 | 默认值 | 说明 |
|-----|------|---------|------|
| `engine` | `"native"` \| `"langgraph"` | `"native"` | Agent 循环引擎：`native`（异步循环）或 `langgraph`（StateGraph） |
| `mode` | `"standard"` \| `"pair_coding"` \| `"multi_agent"` | `"standard"` | Agent 模式（仅 langgraph）：单智能体、编码+审查、控制器+执行者 |
| `max_turns` | int（1–500） | `500` | 最大工具调用轮数，超限强制终止 |
| `compaction_threshold` | float（0.5–0.95） | `0.80` | MICRO 压缩触发的 token 占比（stub 旧工具结果） |
| `human_approval` | bool | `true` | 写/执行工具需要 CLI 用户审批（langgraph pair_coding 模式） |
| `max_review_iterations` | int（1–20） | `5` | 编码→审查→编码的最大循环次数（pair_coding/multi_agent） |
| `auto_mode` | bool | `true` | 让 ComplexityGate 根据任务分析自动选择引擎+模式+模型 |
| `auto_mode_threshold` | float（0.4–0.95） | `0.6` | auto_mode 分类的置信度阈值 |
| `auto_mode_llm_fallback` | bool | `false` | auto_mode 置信度低时回退到更便宜的 LLM |

### `[sandbox]` — 代码执行（harness.toml）

| 键 | 类型 | 默认值 | 说明 |
|-----|------|---------|------|
| `runtime` | str | `"docker"` | 沙箱运行时：`"docker"`（隔离容器）或 `"noop"`（宿主机执行，仅开发） |

Docker 模式使用：只读根文件系统、无网络、无 capabilities、512MB 内存限制、UID 1000。

### `[repomap]` — 代码结构图谱（harness.toml）

| 键 | 类型 | 默认值 | 说明 |
|-----|------|---------|------|
| `enabled` | bool | `true` | 构建 tree-sitter 代码结构图并注入系统提示词 |
| `max_map_tokens` | int | `2000` | 仓库图谱的 token 预算 |

使用 `tree-sitter-language-pack` + 导入图 PageRank 选取最重要的文件。

### `[cache]` — 提示词缓存（harness.toml）

| 键 | 类型 | 默认值 | 说明 |
|-----|------|---------|------|
| `warm_enabled` | bool | `true` | 启用周期性提示词缓存预热，降低 LLM 延迟 |
| `warm_interval_seconds` | int | `240` | 缓存预热请求的间隔（秒） |

### `[observability]` — 追踪与评估（harness.toml + harness.local.toml）

非敏感默认值在 `harness.toml`，密钥在 `harness.local.toml`：

| 键 | 类型 | 默认值 | 文件 | 说明 |
|-----|------|---------|------|------|
| `backend` | `"harness"` \| `"langfuse"` \| `"none"` | `"none"` | harness.toml | 可观测性后端：`none`（禁用）、`langfuse`（外部平台） |
| `langfuse_public_key` | str | `""` | local | Langfuse 项目公钥 |
| `langfuse_secret_key` | str | `""` | local | Langfuse 项目密钥 |
| `langfuse_host` | str | `""` | local | Langfuse 实例地址（如 `"http://localhost:3000"`） |
| `eval_llm_provider` | str | `"openai"` | harness.toml | ragas 评估指标使用的 LLM 提供商 |
| `eval_llm_model` | str | `"gpt-4o-mini"` | harness.toml | 评估用模型（建议用廉价模型 — 只做评判，不生成内容） |
| `eval_llm_api_key` | str | `""` | local | 评估 LLM 的 API 密钥 |
| `eval_llm_api_base` | str | `""` | local | 评估 LLM 的自定义 API 端点 |

### 完整示例

**harness.toml**（共享，提交到 git）：
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

**harness.local.toml**（git-ignored，密钥）：
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

### 命令
| 命令 | 说明 |
|------|------|
| `harness` | 交互式 REPL（默认）|
| `harness run "提示"` | 单次任务 |
| `harness repl` | 显式 REPL |
| `harness tui` | 全屏 Textual TUI |
| `harness doctor` | 系统健康检查 |
| `harness eval memory` | 评估记忆质量（ragas + langfuse） |
| `harness eval list-metrics` | 列出可用的评估指标 |

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
- **Langfuse 追踪**：会话级 Trace、工具调用和 LLM 生成嵌套 Span。未安装 langfuse 时优雅降级。
- **Ragas 记忆评估**：从检索、存储、影响三个维度评估记忆质量。评估结果端到端可追溯至 Langfuse Dashboard。
- **可选依赖分组**：核心依赖精简 — 可观测性（`[observability]`）和评估（`[eval]`）为可选扩展。

---

## 项目结构

```
python/
├── src/harness/
│   ├── cli/           # CLI 入口、REPL、TUI、命令
│   ├── config/        # Pydantic 配置模型
│   ├── core/          # Agent 循环、压缩、上下文、会话、子智能体
│   ├── eval/          # Ragas 记忆评估（指标、执行器、报告器）
│   ├── langgraph/     # LangGraph 多智能体图、节点、委托
│   │   └── nodes/     # 结对编程 + 多智能体协作节点
│   ├── llm/           # LLM 客户端 ABC + LiteLLM 提供商
│   ├── logging/       # 结构化 JSONL 任务日志
│   ├── memory/        # SQLite MemoryStore
│   ├── observability/ # Langfuse 追踪后端（ABC + NoOp + Langfuse）
│   ├── repomap/       # tree-sitter 标签提取 + PageRank 排序
│   ├── safety/        # 输出扫描 + 泄漏检测
│   └── tools/         # 工具 ABC、执行器、权限、12 个内置工具
│       └── sandbox/   # Docker + NoOp 运行时
├── tests/             # pytest 测试套件
├── harness.toml        # 共享配置（提交到 git）
├── harness.local.toml  # 密钥（git-ignored）
└── pyproject.toml     # 构建 + 依赖配置
```

---

## 许可证

MIT
