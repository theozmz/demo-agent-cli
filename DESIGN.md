# Harness CLI — Python 架构设计文档

## 项目定位

**Harness** 是一个安全、高性能、本地优先的 AI 编程 Agent CLI 工具，用 Python 实现，融合三大开源框架的核心技术：

| 来源 | 借鉴特性 | Python 实现策略 |
|------|---------|----------------|
| **Aider** (Python) | RepoMap 代码图谱 | 直接复用并增强 `repomap.py` — tree-sitter + PageRank + token budget |
| **IronClaw** (Rust) | 安全机制 + Docker 沙箱 | ABC 抽象 + Docker SDK + `pyahocorasick` + Pydantic 强类型 |
| **Claude Code** (TypeScript) | Agentic Loop | `AsyncGenerator` 链式状态机 + `asyncio` 并发 + context manager |

## 参考项目来源

本设计文档参考了以下开源项目的架构模式和实现：

### 核心参考（设计基础）

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **Aider** | https://github.com/Aider-AI/aider | RepoMap (tree-sitter + PageRank + token budget)；ChatChunks prompt 缓存；多模型 settings |
| **IronClaw** | (Rust, 内部项目 `ironclaw/`) | SafetyLayer (Sanitizer + LeakDetector + Policy)；ToolDispatcher 审计轨迹；Observer trait 可观测性抽象；Workspace 混合搜索 (RRF) |
| **Claude Code** | https://github.com/anthropics/claude-code | Agentic Loop (AsyncGenerator 状态机)；工具系统 (Tool ABC + ToolRegistry + assembleToolPool)；Prompt Cache 协同 (cache_control, splitSysPromptPrefix, toolSchemaCache, sticky latches)；MemDir memory 文件格式 |

### 沙箱与安全

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **OpenSandbox** | https://github.com/alibaba/OpenSandbox | Docker 沙箱替代 WASM；execd/egress sidecar 注入；三层防御体系 (容器加固 + 网络隔离 + 可选 MicroVM)；Runtime Factory 模式；async 容器生命周期 |

### 记忆系统

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **Claude-Mem** | https://github.com/thedotmack/claude-mem | 渐进式三层检索 (search → timeline → get)；AI 压缩管线；生命周期钩子驱动注入；MEMORY.md + SQLite + ChromaDB 三层存储；观察类型分类 (decision/bugfix/feature/refactor/discovery/change) |

### Skills 与行为契约

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **Superpowers** | https://github.com/obra/superpowers | Bootstrap 注入管线；CSO (Condition/Situation/Opportunity) 描述模式；1% 规则 + Red Flags 表；Controller + Reviewer 子 Agent 编排；TDD for behavioral contracts |

### 可观测性

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **Langfuse** | https://github.com/langfuse/langfuse | Trace-Observation-Score 数据模型；异步 S3-buffered 摄入管线；PostgreSQL + ClickHouse 双数据库；三层仪表化金字塔；Prompt 版本管理 + Trace 关联；ScoreConfig schema 约束 |
| **OpenTelemetry** | https://github.com/open-telemetry/opentelemetry-python | 分布式追踪标准 (Span, TracerProvider, Sampler)；Metrics (Histogram, Counter, Gauge)；Log 关联 (trace_id + span_id)；contextvars 跨 asyncio 传播 |

### 评估基准

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **SWE-bench** | https://github.com/princeton-nlp/SWE-bench | 评估管线 (8-step)；FAIL_TO_PASS / PASS_TO_PASS 测试分类；Resolution 三级评分；三层 Docker 镜像缓存；框架特定日志解析器；JSONL prediction 约定 |

### Agent 框架

| 项目 | 仓库 | 引用领域 |
|------|------|---------|
| **LangGraph** | https://github.com/langchain-ai/langgraph | StateGraph Agent Loop 替代实现；TypedDict AgentState；conditional_edges 声明式路由；AsyncSqliteSaver checkpointing；astream_events 流式输出；RetryPolicy 内置重试 |

> **说明**: 以上项目均为独立开源项目，Harness 设计借鉴其架构模式和设计理念，不使用其代码。所有引用均在对应章节中标注了借鉴来源。

---

## 目录

1. [技术栈](#一技术栈)
2. [项目结构](#二项目结构)
   - [分层架构与层间接口](#分层架构与层间接口) ★
3. [Agentic Loop、入口点与编排方式](#三agentic-loop入口点与编排方式) ★
   - 3.7 [LangGraph 集成 — StateGraph Agent Loop](#37-langgraph-集成--stategraph-agent-loop) ★
   - 3.8 [Session / Thread / Turn 数据模型](#38-session--thread--turn-数据模型) ★
   - 3.9 [SubAgent 实现](#39-subagent-实现) ★
   - 3.10 [多模型路由 (Multi-Model Router)](#310-多模型路由-multi-model-router)
   - 3.11 [流式架构 (SSE → UI 渲染管线)](#311-流式架构-sse--ui-渲染管线)
   - 3.12 [Skills 系统与行为契约](#312-skills-系统与行为契约借鉴-superpowers) ★
   - 3.13 [多 Agent 编排模式](#313-多-agent-编排模式借鉴-superpowers-controller--reviewer) ★
4. [Context 管理与压缩方式](#四context-管理与压缩方式) ★
   - 4.5 [RepoMap 刷新策略](#45-repomap-刷新策略)
5. [Tool 调用方式](#五tool-调用方式) ★
   - 5.3 [Domain 路由与 Docker 沙箱架构](#53-domain-路由与-docker-沙箱架构借鉴-opensandbox) ★
   - 5.5 [默认工具完整规范 (15 个内置工具)](#55-默认工具完整规范-15-个内置工具) ★
   - 5.6 [Tool 注册表与 Pool 组装](#56-tool-注册表与-pool-组装) ★
   - 5.7 [MCP 桥接协议](#57-mcp-桥接协议) ★
6. [Memory 记录形式与 System Prompt 组装](#六memory-记录形式与-system-prompt-组装) ★
   - 6.1.1 [观察类型分类](#611-观察类型分类借鉴-claude-mem)
   - 6.1.3 [AI 压缩管线](#613-ai-压缩管线借鉴-claude-mem-的-compression-pipeline) ★
   - 6.1.4 [生命周期钩子驱动的记忆注入](#614-生命周期钩子驱动的记忆注入)
   - 6.3.1 [渐进式三层检索](#631-渐进式三层检索借鉴-claude-mem-的-progressive-disclosure) ★
   - 6.6 [System Prompt 哈希校验与组装算法](#66-system-prompt-哈希校验与组装算法) ★
   - 6.7 [Transcript 格式](#67-transcript-格式)
   - 6.8 [Resource Limits 资源限制](#68-resource-limits-资源限制)
7. [结构化校验、重试与降级策略](#七结构化校验重试与降级策略) ★
   - 7.7 [错误分类体系 (Error Taxonomy)](#77-错误分类体系-error-taxonomy) ★
8. [核心抽象接口 (ABC)](#八核心抽象接口)
   - 8.1 [插件系统](#81-插件系统)
9. [配置系统](#九配置系统)
   - 9.1 [配置验证与热加载](#91-配置验证与热加载)
10. [测试策略](#十测试策略)
11. [与 Rust 版差异](#十一与-rust-版的关键差异)
12. [实施路线图](#十二实施路线图)
13. [可观测性 (Trace-Observation-Score 模型)](#十三可观测性trace-observation-score-模型借鉴-langfuse--opentelemetry) ★
    - 13.1 [数据模型: Trace → Observation → Score](#131-数据模型-trace--observation--score)
    - 13.2 [摄入管线: 异步缓冲架构](#132-摄入管线-异步缓冲架构) ★
    - 13.3 [仪表化金字塔](#133-仪表化金字塔借鉴-langfuse-三层集成)
    - 13.4 [Prompt 版本管理与 Trace 关联](#134-prompt-版本管理与-trace-关联)
    - 13.5 [评分系统](#135-评分系统)
14. [评估系统 (Evaluation Harness — SWE-bench 对齐)](#十四评估系统evaluation-harness--swe-bench-对齐) ★
    - 14.1 [评估管线 (8 步流程)](#141-评估管线8-步流程)
    - 14.2 [三层 Docker 镜像缓存](#142-三层-docker-镜像缓存)
    - 14.3 [框架特定日志解析器](#143-框架特定日志解析器)
    - 14.4 [评分与分级](#144-评分与分级)
    - 14.6 [EvalDelegate 评估模式 Delegate](#146-evaldelegate-评估模式专用-delegate)

---

## 一、技术栈

```
语言:          Python 3.12+
异步运行时:    asyncio (标准库)
数据模型:      Pydantic v2 (BaseModel + field_validator)
LLM 协议:      Anthropic Python SDK + OpenAI Python SDK + litellm
Agent 框架:    LangGraph v0.4+ (StateGraph + Checkpointing + Streaming)
WASM 沙箱:     已移除 — 采用 Docker 沙箱 + MicroVM 可选层 (参见 Section 5.3)
Docker SDK:    docker-py v7+ (容器生命周期管理)
代码分析:      tree-sitter-py + tree-sitter-language-pack + networkx
安全:          pyahocorasick + cryptography + re
CLI:           textual v1+ (TUI) + prompt_toolkit (REPL) + typer (CLI args)
持久化:        SQLite (via aiosqlite) + diskcache + JSONL transcript
可观测性:      OpenTelemetry SDK + OTLP exporter + Prometheus metrics
包管理:        uv (pip compatible, fast resolution)
测试:          pytest + pytest-asyncio + pytest-mock + VCR.py (HTTP 录制回放)
其它:          blake3 (hashing) + watchfiles (config hot-reload)
```

---

## 二、项目结构

```
harness-python/
├── pyproject.toml                    # uv/poetry 项目定义
├── harness.toml                      # 默认配置
│
├── src/harness/
│   ├── __init__.py
│   ├── __main__.py                   # `python -m harness` 入口
│   │
│   ├── cli/                          # CLI 层
│   │   ├── app.py                    # typer.Typer 子命令 (prompt/repl/tui/doctor)
│   │   ├── repl.py                   # prompt_toolkit REPL
│   │   └── tui/                      # textual TUI
│   │
│   ├── core/                         # ★ 核心: AgenticLoop + compaction + subagent
│   │   ├── loop.py                   # AgenticLoop + run_agentic_loop()
│   │   ├── langgraph_loop.py         # LangGraph StateGraph 替代实现
│   │   ├── loop_delegate.py          # LoopDelegate ABC + LoopSignal/TextAction/LoopOutcome
│   │   ├── session.py               # Session → Thread → Turn 数据模型
│   │   ├── context.py               # 上下文组装 (SystemPromptPart 三段式)
│   │   ├── compaction.py            # 多层压缩 + 断路保护
│   │   ├── subagent.py              # 子 Agent 派发 + 上下文隔离
│   │   ├── recovery.py             # 错误恢复 + 6 条 recovery path
│   │   ├── circuit_breaker.py      # 断路保护器
│   │   ├── tracker.py             # DuplicateToolCallTracker + TruncationTracker
│   │   ├── streaming.py           # SSE → EventBus → UI 流式管线
│   │   ├── transcript.py          # JSONL transcript 读写
│   │   ├── limits.py             # ResourceLimits 硬性资源限制
│   │   └── errors.py             # 完整错误分类体系
│   │
│   ├── llm/
│   │   ├── client.py               # LlmClient ABC
│   │   ├── types.py                # ChatMessage, ContentBlock, SystemPromptPart
│   │   ├── cache.py                # Prompt cache co-engineering
│   │   ├── cache_break_detection.py # 两阶段 cache break 检测
│   │   ├── sticky_latches.py       # 粘性锁存器 (防止 mid-session cache 失效)
│   │   ├── cache_warmer.py         # 后台缓存预热
│   │   ├── prompt_assembler.py     # 确定性 prompt 组装管线 + 哈希校验
│   │   ├── token.py                # Token 估算 (word × 1.3 + 4 × msg)
│   │   ├── router.py               # 多模型路由 (cheap/default/expensive)
│   │   ├── retry.py               # with_retry() 装饰器 + 指数退避 + fallback
│   │   ├── degradation.py         # 降级管理
│   │   └── providers/
│   │       ├── anthropic.py         # via `anthropic` SDK AsyncMessages
│   │       ├── openai.py            # via `openai` SDK
│   │       └── compat.py            # Ollama/vLLM/openai-compat
│   │
│   ├── tools/
│   │   ├── tool.py                  # Tool ABC + ToolOutput + ToolError
│   │   ├── registry.py              # ToolRegistry + assemble_tool_pool
│   │   ├── executor.py              # ToolExecutor (9-step pipeline)
│   │   ├── permissions.py           # PermissionPolicy + ApprovalContext
│   │   ├── builtin/                 # 15 个内置工具
│   │   │   ├── file_read.py
│   │   │   ├── file_write.py
│   │   │   ├── file_edit.py
│   │   │   ├── glob_search.py
│   │   │   ├── grep_search.py
│   │   │   ├── bash_exec.py
│   │   │   ├── web_fetch.py
│   │   │   ├── web_search.py
│   │   │   ├── memory_read.py
│   │   │   ├── memory_write.py
│   │   │   ├── memory_delete.py
│   │   │   ├── task_create.py
│   │   │   ├── task_update.py
│   │   │   ├── task_list.py
│   │   │   └── ask_user_question.py
│   │   ├── sandbox/                  # Docker 沙箱
│   │   │   ├── runtime.py           # SandboxRuntime ABC + Runtime Factory
│   │   │   ├── execd.py            # execd sidecar HTTP API
│   │   │   ├── egress.py           # egress sidecar 网络策略
│   │   │   └── docker_sandbox.py   # Docker 容器管理 (引用 OpenSandbox 模式)
│   │   └── mcp/                     # MCP 桥接
│   │       └── client_manager.py    # McpClientManager + McpToolWrapper
│   │
│   ├── safety/
│   │   ├── pipeline.py              # SafetyLayer 编排
│   │   ├── sanitizer.py             # Prompt 注入检测
│   │   ├── validator.py             # 内容校验
│   │   ├── policy.py               # 策略引擎
│   │   └── leak_detector.py         # 密钥检测
│   │
│   ├── repomap/                     # 代码图谱
│   │   ├── repomap.py               # RepoMap 主类
│   │   ├── tags.py                  # tree-sitter 标签
│   │   ├── ranking.py               # PageRank
│   │   ├── refresh.py              # 刷新策略 (manual/always/files/auto)
│   │   └── cache.py                # TagCache (diskcache / mtime-based)
│   │
│   ├── sandbox/                     # Docker sandbox
│   │
│   ├── config/                      # Pydantic Config
│   │   ├── config.py               # Config 主类
│   │   ├── validator.py            # ConfigValidator
│   │   └── watcher.py              # ConfigWatcher (热加载)
│   │
│   ├── memory/                      # ★ Memory 系统
│   │   ├── store.py                 # MemoryStore (SQLite + FTS5)
│   │   ├── writer.py               # MemoryWriter (memory_write tool 实现)
│   │   ├── reader.py               # MemoryReader (检索 + RRF 融合)
│   │   └── prompt.py               # Memory → system prompt 注入
│   │
│   ├── observability/               # ★ 可观测性 (Langfuse + OTel 双模型)
│   │   ├── tracer.py               # Trace-Observation-Score 数据模型
│   │   ├── ingestion.py            # 异步摄入管线 (S3/Redis/ClickHouse)
│   │   ├── metrics.py              # Histograms/Counters/Gauges
│   │   ├── scores.py              # Score 系统 + ScoreConfig 校验
│   │   ├── prompt_versioning.py   # Prompt 版本管理 + Trace 关联
│   │   └── log_correlation.py      # trace_id + span_id 注入 logging
│   │
│   ├── skills/                       # ★ Skills 系统 (借鉴 Superpowers)
│   │   ├── loader.py               # Skill 发现 + 加载 (YAML frontmatter)
│   │   ├── injector.py            # Bootstrap 注入管线
│   │   ├── registry.py            # SkillRegistry (按 priority 排序)
│   │   └── red_flags.py          # Red Flags 表管理
│   │
│   ├── eval/                         # ★ 评估系统 (SWE-bench 对齐)
│   │   ├── runner.py               # EvalRunner — 评估主循环
│   │   ├── dataset.py              # 数据集加载 (HF / JSONL / 自定义)
│   │   ├── grader.py               # 评分与分级引擎
│   │   ├── log_parser.py           # 框架特定日志解析器
│   │   ├── swebench_adapter.py     # SWE-bench 原生适配器
│   │   └── reporter.py            # 报告生成 (JSON / Markdown / 表格)
│   │
│   └── plugins/                     # 插件系统
│       └── manager.py              # PluginManager + HookPoint + HarnessPlugin ABC
│
├── tests/
│   ├── conftest.py                  # TestRig, MockDelegate, StubLlm fixtures
│   ├── test_loop.py
│   ├── test_langgraph_loop.py
│   ├── test_context.py
│   ├── test_prompt_assembler.py
│   ├── test_tools.py
│   ├── test_tool_registry.py
│   ├── test_safety.py
│   ├── test_repomap.py
│   ├── test_observability.py
│   ├── test_eval.py
│   └── e2e/
│
└── harness.toml
```

---

## 分层架构与层间接口

Harness 采用 **四层架构**，每层有明确的职责边界和接口契约。依赖方向严格单向：上层依赖下层，下层绝不引用上层。

### 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                             │
│                    (展示层 / CLI-TUI-REPL)                         │
│                                                                    │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ typer    │   │ prompt_toolkit│   │ textual TUI              │  │
│  │ (one-shot)│   │ (REPL loop)  │   │ (full-screen interactive)│  │
│  └────┬─────┘   └──────┬───────┘   └────────────┬─────────────┘  │
│       │                │                        │                 │
│       └────────────────┼────────────────────────┘                 │
│                        │                                          │
│              ┌─────────▼──────────┐                               │
│              │   UiAdapter ABC    │  ← 层间接口 #1                 │
│              │   .render(event)   │                               │
│              │   .input() → str   │                               │
│              └─────────┬──────────┘                               │
├────────────────────────┼──────────────────────────────────────────┤
│                        │                                          │
│                    APPLICATION LAYER                               │
│                    (应用层 / Orchestration)                        │
│                                                                    │
│  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐    │
│  │ SessionManager   │  │ AgenticLoop  │  │ LangGraphLoop    │    │
│  │ (lifecycle)      │  │ (native)     │  │ (alternative)    │    │
│  └────────┬─────────┘  └──────┬───────┘  └────────┬─────────┘    │
│           │                   │                    │               │
│           │        ┌──────────▼──────────┐         │               │
│           │        │  LoopDelegate ABC   │ ← 层间接口 #2          │
│           │        │  .call_llm()        │                        │
│           │        │  .execute_tools()   │                        │
│           │        │  .before_llm_call() │                        │
│           │        └──────────┬──────────┘                        │
│           │                   │                                    │
├───────────┼───────────────────┼────────────────────────────────────┤
│           │                   │                                    │
│                    DOMAIN LAYER                                    │
│                    (领域层 / Core Services)                        │
│                                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ Context  │ │  Tools   │ │ Safety   │ │  Memory  │ │ RepoMap │ │
│  │ Gatherer │ │ Executor │ │ Layer    │ │  Store   │ │         │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ │
│       │            │            │            │            │        │
│  ┌────▼─────┐      │      ┌─────▼─────┐      │            │        │
│  │ Prompt   │      │      │ Leak      │      │            │        │
│  │Assembler │      │      │ Detector  │      │            │        │
│  └──────────┘      │      └───────────┘      │            │        │
│                    │                         │            │        │
│         ┌──────────▼──────────┐              │            │        │
│         │  Tool ABC           │ ← 层间接口 #3              │        │
│         │  .execute()         │                            │        │
│         │  .input_schema      │                            │        │
│         │  .requires_approval │                            │        │
│         └──────────┬──────────┘                            │        │
│                    │                                        │        │
├────────────────────┼────────────────────────────────────────┼────────┤
│                    │                                        │        │
│                    INFRASTRUCTURE LAYER                               │
│                    (基础设施层 / Plumbing)                             │
│                                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ LlmClient│ │ Sandbox  │ │ MCP      │ │ Persist  │ │Observe  │ │
│  │ Provider │ │ (Docker/ │ │ Bridge   │ │ (SQLite/ │ │(OTel)   │ │
│  │ (Anthr/  │ │  Docker) │ │ (stdio/  │ │  JSONL)  │ │         │ │
│  │  OpenAI) │ │          │ │  SSE)    │ │          │ │         │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬────┘ │
│       │            │            │            │            │        │
│  ┌────▼─────┐      │            │            │            │        │
│  │ LlmClient│      │            │            │            │        │
│  │ ABC      │      │            │            │            │        │
│  │ .generate│      │            │            │            │        │
│  │ .stream  │      │            │            │            │        │
│  └──────────┘      │            │            │            │        │
└──────────────────────────────────────────────────────────────────┘

          CROSS-CUTTING CONCERNS (贯穿所有层)
          Config / Error Taxonomy / Plugin Hooks / Telemetry
```

### 层职责与依赖规则

| 层 | 职责 | 可以依赖 | 禁止依赖 | 模块 |
|----|------|---------|---------|------|
| **展示层** | 用户交互、事件渲染 | 应用层 (通过 UiAdapter) | 领域层、基础设施层 | `cli/` |
| **应用层** | 编排、生命周期、状态机 | 领域层 (通过 ABC) | 展示层、基础设施层 | `core/` |
| **领域层** | 业务逻辑、工具、安全 | 基础设施层 (通过 ABC) | 展示层、应用层 | `tools/`, `safety/`, `memory/`, `repomap/`, `llm/cache.py`, `llm/prompt_assembler.py` |
| **基础设施层** | 外部系统交互、持久化 | 无 (仅标准库 + 第三方 SDK) | 所有上层 | `llm/providers/`, `sandbox/`, `tools/mcp/`, `observability/`, `config/` |

### 层间接口 #1: UiAdapter (展示层 ↔ 应用层)

展示层通过 `UiAdapter` ABC 接收来自应用层的结构化事件，应用层不感知终端是 CLI、REPL 还是 TUI。

```
调用方向: 展示层 →调用→ UiAdapter.render(event)
         应用层 ←调用← UiAdapter.input() → Awaitable[str]
```

```python
# harness/cli/adapter.py — 展示层/应用层 边界

from abc import ABC, abstractmethod
from enum import Enum

class UiAdapter(ABC):
    """
    展示层抽象 — 三种 UI 模式共享同一个接口。

    应用层只调用 UiAdapter 的方法，不知道是 CLI pipe、
    prompt_toolkit REPL、还是 textual TUI 在消费事件。

    接口契约:
    - render(): 应用层 push 事件, 展示层决定如何绘制
    - input():  应用层 pull 用户输入, 展示层决定如何获取
    - 事件顺序保证: start → (delta|tool_start|tool_end)* → done
    - 展示层不得修改事件内容
    - 展示层异常不得传播到应用层 (catch + log + 降级)
    """

    @abstractmethod
    async def render(self, event: "UiEvent"):
        """接收来自应用层的 UI 事件 — 单向推送"""
        ...

    @abstractmethod
    async def input(self, prompt: str = "") -> str:
        """获取用户输入 — 应用层 pull"""
        ...

    @abstractmethod
    async def confirm(self, message: str, default: bool = False) -> bool:
        """获取用户确认 (审批流)"""
        ...

    @abstractmethod
    async def select(self, message: str, options: list[str]) -> str:
        """获取用户选择"""
        ...

    @abstractmethod
    def is_interactive(self) -> bool:
        """是否支持交互 (管道模式返回 False)"""
        ...

    @abstractmethod
    def supports_rich_text(self) -> bool:
        """是否支持富文本 (ANSI/Markdown)"""
        ...


# ─── UiEvent: 应用层→展示层 的事件协议 ──────────────

@dataclass
class UiEvent:
    """
    应用层产出的 UI 事件 — 与 StreamEvent (内部) 不同，
    UiEvent 是面向用户的最终渲染事件。

    事件类型:
    - TEXT_DELTA: 增量文本 → TUI append, CLI print(no newline)
    - TEXT_DONE: 文本响应完成 → TUI finalize, CLI print(newline)
    - TOOL_START: 工具调用开始 → 显示 spinner
    - TOOL_PROGRESS: 工具执行中 → 更新进度
    - TOOL_END: 工具调用完成 → 显示结果摘要
    - APPROVAL_NEEDED: 需要用户审批 → 展示审批对话框
    - TURN_COMPLETE: Turn 结束 → 显示统计
    - ERROR: 可恢复错误 → 显示警告
    - FATAL: 不可恢复错误 → 显示错误 + 退出
    - HEARTBEAT: 保活信号 → 忽略 (仅用于检测连接)
    """
    kind: str  # "text_delta" | "tool_start" | "tool_end" | "approval_needed" | ...
    payload: Any = None
    turn_id: str = ""
    timestamp: float = field(default_factory=time.monotonic)


# ─── 三种 UiAdapter 实现 ───────────────────────────

class PipeUiAdapter(UiAdapter):
    """管道模式 — 最小实现, 直接 print + stdin"""
    def is_interactive(self) -> bool: return False
    def supports_rich_text(self) -> bool: return False
    async def render(self, event): print(event.payload or "")

class ReplUiAdapter(UiAdapter):
    """REPL 模式 — prompt_toolkit 实现"""
    def is_interactive(self) -> bool: return True
    # ...

class TuiUiAdapter(UiAdapter):
    """TUI 模式 — textual 实现"""
    def is_interactive(self) -> bool: return True
    def supports_rich_text(self) -> bool: return True
    # ...
```

### 层间接口 #2: LoopDelegate (应用层 ↔ 领域层)

应用层的 `AgenticLoop` / `LangGraphLoop` 通过 `LoopDelegate` ABC 调用领域层服务。应用层只控制 *何时* 调用，领域层决定 *如何* 执行。

```
调用方向: 应用层 →调用→ delegate.call_llm(ctx)
         应用层 →调用→ delegate.execute_tools(calls, ctx)
         领域层 →回调→ ctx.emit_event(event)  (进度通知)
```

```python
# harness/core/loop_delegate.py — 应用层/领域层 边界

class LoopDelegate(ABC):
    """
    Agent 循环的策略接口 — 应用层不关心 LLM 是 Anthropic 还是 OpenAI，
    工具是内置还是 MCP，安全策略如何配置。

    每个方法接收 LoopContext (领域层聚合根)，返回领域对象。

    接口契约:
    - call_llm(): 输入 messages, 输出 LlmResponse (含 tool_calls)
    - execute_tools(): 输入 tool_calls, 不抛异常 (错误作为 tool_result 返回)
    - before_llm_call(): 前置检查 (compaction/cost guard), 可返回 early outcome
    - handle_text_response(): 文本响应处理 (tool intent nudge 检测)
    - check_signals(): 外部信号检查 (取消/暂停/注入)
    - 领域层错误通过 LlmError / ToolError 传播, 应用层负责分类重试
    - 应用层不直接访问 LLM SDK 或 Tool Registry
    """

    @abstractmethod
    async def check_signals(self) -> "LoopSignal":
        """检查外部信号 (用户按 Ctrl-C, 系统注入消息等)"""
        ...

    @abstractmethod
    async def before_llm_call(
        self, ctx: "LoopContext", iteration: int
    ) -> "LoopOutcome | None":
        """
        LLM 调用前的检查点。

        可返回 early outcome 以短路 (如 compaction 失败 → 降级处理),
        或返回 None 以继续正常流程。

        此方法中发生的 compaction 由领域层 (ContextCompactor) 执行,
        应用层只负责触发时机。
        """
        ...

    @abstractmethod
    async def call_llm(
        self, ctx: "LoopContext", iteration: int
    ) -> "LlmResponse":
        """
        调用 LLM — 领域层负责:
        1. 选择 provider (Anthropic/OpenAI/...)
        2. 组装 messages + tools + system prompt
        3. 处理 streaming
        4. 返回标准化的 LlmResponse

        应用层只需要 LlmResponse.text 和 LlmResponse.tool_calls。
        """
        ...

    @abstractmethod
    async def handle_text_response(
        self, text: str, ctx: "LoopContext"
    ) -> "TextAction":
        """
        处理纯文本响应。

        返回 TextAction.RETURN (退出循环) 或
        TextAction.CONTINUE (tool intent nudge 注入后继续)。
        """
        ...

    @abstractmethod
    async def execute_tool_calls(
        self, tool_calls: list["ToolCall"], ctx: "LoopContext"
    ) -> "LoopOutcome | None":
        """
        执行工具调用 — 领域层负责完整管线:
        lookup → validate → redact → approve → execute → safety scan

        返回 None 表示继续循环 (工具结果已注入 ctx.messages)。
        返回 LoopOutcome 表示提前终止 (如用户拒绝、安全阻断)。
        """
        ...

    @abstractmethod
    async def after_iteration(self, iteration: int, ctx: "LoopContext"):
        """每次迭代后的回调 — 用于 logging/metrics/状态更新"""
        ...


# ─── 三种 Delegate 实现 ────────────────────────────

class ChatDelegate(LoopDelegate):
    """交互式对话 — 用户可见, 可审批"""
    def __init__(self, ui: UiAdapter, tool_executor: "ToolExecutor",
                 safety: "SafetyLayer", permission_policy: "PermissionPolicy"):
        self._ui = ui          # 展示层依赖 (通过 UiAdapter ABC)
        self._tools = tool_executor
        self._safety = safety
        self._policy = permission_policy

    async def execute_tool_calls(self, tool_calls, ctx):
        for tc in tool_calls:
            # 需要审批 → 通过 UI 请求用户
            if self._policy.authorize(tc.name, tc.input, ctx) == PermissionOutcome.NEEDS_APPROVAL:
                approved = await self._ui.confirm(
                    f"Allow {tc.name} with params {tc.input}?"
                )
                if not approved:
                    continue  # skip this tool call
            # 执行
            output = await self._tools.execute(tc.name, tc.input, ctx)
            ctx.messages.append(ChatMessage.tool_result(tc.id, output.content))


class JobDelegate(LoopDelegate):
    """后台任务 — 自治, allowed_tools 白名单, 无用户交互"""
    def __init__(self, allowed_tools: set[str], tool_executor: "ToolExecutor"):
        self._allowed = allowed_tools
        self._tools = tool_executor

    async def execute_tool_calls(self, tool_calls, ctx):
        for tc in tool_calls:
            if tc.name not in self._allowed:
                ctx.messages.append(ChatMessage.tool_result(
                    tc.id, f"Tool '{tc.name}' not allowed in job mode", is_error=True
                ))
                continue
            output = await self._tools.execute(tc.name, tc.input, ctx)
            ctx.messages.append(ChatMessage.tool_result(tc.id, output.content))


class ContainerDelegate(LoopDelegate):
    """Docker 容器内 worker — 最小权限, 容器沙箱"""
    ...
```

### 层间接口 #3: Tool ABC (领域层 ↔ 基础设施层)

`Tool` ABC 是领域层定义的工具契约，基础设施层 (Docker/MCP) 提供具体执行能力。领域层通过 `ToolExecutor` 调用工具，不感知执行环境。

```
调用方向: 领域层 →调用→ ToolExecutor.execute(tool_name, params, ctx)
         领域层 →调用→ Tool.execute(params, ctx)  → 基础设施层执行
         领域层 ←返回← ToolOutput
```

```python
# harness/tools/tool.py — 领域层/基础设施层 边界

class Tool(ABC):
    """
    工具抽象 — 所有工具 (内置/Docker/MCP) 实现此接口。

    接口契约:
    - execute(): 输入 params (dict), 输出 ToolOutput。错误通过 ToolError 传播。
    - input_schema: JSON Schema dict — 领域层用于 LLM tool_use 声明
    - requires_approval(): 返回 NEVER/UNLESS_AUTO/ALWAYS
    - is_read_only: bool — 用于模式过滤 (simple mode 只允许只读工具)
    - timeout_seconds: 硬超时, ToolExecutor 负责 enforce
    - domain: Orchestrator (进程内) 或 Container (Docker 沙箱)
    - 工具实现必须是幂等的 (相同 params → 相同结果) 或至少声明副作用
    - 工具不得直接访问 UI、文件系统 (通过 ctx.workspace)、网络 (通过 host 函数)
    - 工具结果由 ToolExecutor 包装 (safety scan + wrap_for_llm)
    """

    name: str                          # 唯一名称
    description: str                   # LLM 可读的描述
    domain: "ToolDomain"               # Orchestrator | Container
    timeout_seconds: int = 30
    sensitive_params: set[str] = field(default_factory=set)

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema — LLM 看到的工具参数定义"""
        ...

    @abstractmethod
    async def execute(self, params: dict, ctx: "ToolContext") -> "ToolOutput":
        """执行工具 — 返回 ToolOutput 或抛 ToolError"""
        ...

    def requires_approval(self, params: dict) -> "ApprovalRequirement":
        """默认: 根据 is_read_only 判断"""
        return ApprovalRequirement.NEVER if self.is_read_only else ApprovalRequirement.UNLESS_AUTO

    @property
    def is_read_only(self) -> bool:
        return False

    def is_enabled(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        """破坏性工具 (如 bash_exec, file_delete) — 需要额外审批"""
        return False


# ─── ToolContext: 领域层聚合根, 跨层传递 ────────────

@dataclass
class ToolContext:
    """
    工具执行上下文 — 所有工具 execute() 的第一个参数。

    跨层传递规则:
    - 应用层创建 → 领域层注入依赖 → 基础设施层读取
    - cwd, user_id: 应用层设置
    - tool_registry, safety: 领域层注入
    - workspace, secrets: 基础设施层提供
    - 工具不得修改 ctx, 只能读取
    - ctx 通过 contextvars 在 asyncio 任务间传播
    """
    cwd: str                                  # 工作目录 (展示层/应用层设置)
    user_id: str                              # 用户标识
    session_id: str                           # 当前 Session ID
    turn_id: str                              # 当前 Turn ID
    tool_registry: "ToolRegistry"             # 领域层: 工具注册表 (只读引用)
    safety: "SafetyLayer"                     # 领域层: 安全管线
    workspace: "Workspace"                    # 基础设施层: 文件系统抽象
    secrets: "SecretsStore"                   # 基础设施层: 凭证存储
    llm: "LlmClient"                          # 基础设施层: LLM 客户端
    subagent_depth: int = 0                   # 应用层: 子 Agent 嵌套深度
    parent_agent_id: str | None = None        # 应用层: 父 Agent ID
    config: "Config"                          # 全局配置
    permissions: "PermissionPolicy"           # 领域层: 权限策略


# ─── ToolDomain 路由 ──────────────────────────────

class ToolDomain(Enum):
    ORCHESTRATOR = "orchestrator"  # 进程内执行 (Python) — 只读或受控 API 调用
    CONTAINER = "container"        # Docker 沙箱执行


class ToolOutput:
    """工具执行结果 — 领域层→应用层的返回值"""
    content: str
    is_error: bool = False
    risk_level: str = "low"        # "low" | "medium" | "high" | "blocked"
    duration_ms: float = 0.0
    truncated: bool = False
    retry_count: int = 0
```

### 层间接口 #4: LlmClient ABC (领域层 ↔ 基础设施层)

领域层通过 `LlmClient` ABC 调用 LLM，不绑定特定 provider。

```python
# harness/llm/client.py — 领域层/基础设施层 边界

class LlmClient(ABC):
    """
    LLM 客户端抽象 — 领域层调用, 基础设施层实现。

    接口契约:
    - generate(): 非流式 — 返回完整 LlmResponse
    - stream(): 流式 — 返回 AsyncIterator[StreamEvent]
    - estimate_tokens(): 估算 token 数
    - context_window: 模型上下文窗口大小
    - 重试和降级由领域层 (LlmRetryHandler) 处理, 不在此 ABC 中
    - cache 命中信息在 response.usage 中, 由领域层 CacheBreakDetector 消费
    """

    @property
    @abstractmethod
    def context_window(self) -> int:
        """模型上下文窗口大小 (tokens)"""
        ...

    @abstractmethod
    async def generate(
        self,
        messages: list["ChatMessage"],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        **kwargs,
    ) -> "LlmResponse":
        """非流式调用 LLM"""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list["ChatMessage"],
        tools: list[dict] | None = None,
        system_prompt: str | None = None,
        **kwargs,
    ) -> AsyncIterator["StreamEvent"]:
        """流式调用 LLM"""
        ...

    @abstractmethod
    def estimate_tokens(self, messages: list["ChatMessage"]) -> int:
        """估算 token 数 (word × 1.3 + 4 × msg)"""
        ...

# ─── LlmResponse: 标准化的 LLM 响应 ────────────────

@dataclass
class LlmResponse:
    """所有 provider 统一返回此结构"""
    id: str
    text: str | None
    tool_calls: list["ToolCall"] | None
    stop_reason: str                          # "end_turn" | "tool_use" | "max_tokens" | "stop"
    usage: "LlmUsage"
    model: str
    duration_ms: float

@dataclass
class LlmUsage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
```

### 层间接口 #5: 跨层上下文传递 — LoopContext

`LoopContext` 是贯穿应用层和领域层的"聚合根"对象，承载一次 Agent 循环所需的所有依赖。它通过 `contextvars` 在 asyncio 任务间传递，避免在每个函数签名中显式传递。

```python
# harness/core/context.py — 跨层上下文

@dataclass
class LoopContext:
    """
    Agent 循环上下文 — 应用层创建, 领域层读写, 基础设施层提供后端。

    生命周期:
    - created: Session.run_turn() 开始前
    - mutated: 每轮迭代 messages 追加, usage 累加
    - read: 所有领域层服务 (PromptAssembler, ToolExecutor, SafetyLayer)
    - destroyed: Turn 结束后转为 transcript

    线程安全:
    - 单线程 asyncio 模型 — 不需要锁
    - contextvars 传播到 asyncio.create_task() 子任务
    """

    # ─── 基础设施层提供的后端 ──────────────────────
    llm: "LlmClient"
    workspace: "Workspace"
    secrets: "SecretsStore"

    # ─── 领域层提供的策略 ──────────────────────────
    tool_registry: "ToolRegistry"
    safety: "SafetyLayer"
    permission_policy: "PermissionPolicy"
    memory_reader: "MemoryReader"

    # ─── 应用层管理的状态 ──────────────────────────
    session: "Session"
    messages: list["ChatMessage"] = field(default_factory=list)
    usage: "LlmUsage" = field(default_factory=lambda: LlmUsage(0, 0))
    iteration: int = 0
    subagent_depth: int = 0
    force_text: bool = False
    agent_id: str = field(default_factory=lambda: uuid4().hex[:8])

    # ─── 非序列化字段 ──────────────────────────────
    _event_emitter: "Callable[[UiEvent], Awaitable[None]] | None" = field(
        default=None, repr=False
    )

    async def emit_event(self, event: "UiEvent"):
        """向展示层推送事件 — 如果 emitter 已注入"""
        if self._event_emitter:
            await self._event_emitter(event)
```

### 跨层数据流 — 一个 Turn 的完整旅程

```
用户输入 "add login endpoint"
    │
    ▼
┌─ 展示层 ──────────────────────────────────────────────────────┐
│  UiAdapter.input() → "add login endpoint"                     │
│  用户按键 → prompt_toolkit 捕获 → UiAdapter ABC → 返回字符串    │
└────────────────────────┬──────────────────────────────────────┘
                         │ user_input: str
                         ▼
┌─ 应用层 ──────────────────────────────────────────────────────┐
│  Session.run_turn(user_input)                                 │
│    │                                                           │
│    ├── 1. ctx = LoopContext(llm=..., tools=..., safety=...)   │
│    │      ↑ 依赖注入: 基础设施层后端 + 领域层策略                │
│    │                                                           │
│    ├── 2. safety.scan_inbound(user_input)  ← 领域层            │
│    │      └── Sanitizer.scan() → LeakDetector.scan()          │
│    │                                                           │
│    ├── 3. ctx = ContextGatherer.gather(cwd, messages) ← 领域层 │
│    │      ├── PromptAssembler.assemble() ← 领域层 (缓存逻辑)   │
│    │      │     └── tool_registry.get_schemas() ← 领域层      │
│    │      │           └── [MCP tool discovery] ← 基础设施层     │
│    │      ├── RepoMap.get_map() ← 领域层                       │
│    │      │     └── tree-sitter + PageRank ← 基础设施层        │
│    │      └── MemoryReader.search() ← 领域层                   │
│    │            └── SQLite FTS5 + vector ← 基础设施层           │
│    │                                                           │
│    ├── 4. messages = ctx.to_messages() + CacheBreakDetector    │
│    │                                                           │
│    └── 5. AgenticLoop.run(messages)                            │
│          │                                                     │
│          ├── before_llm_call(ctx, iter) ← LoopDelegate         │
│          │     └── CompactionEngine.evaluate() ← 领域层         │
│          │                                                     │
│          ├── call_llm(ctx, iter) ← LoopDelegate                │
│          │     └── LlmClient.generate(messages, tools) ← 基础设施层│
│          │           └── Anthropic SDK HTTP POST ← 外部系统      │
│          │           └── return LlmResponse                    │
│          │           └── CacheBreakDetector.check_response()   │
│          │                                                     │
│          ├── execute_tools(tool_calls, ctx) ← LoopDelegate     │
│          │     └── ToolExecutor.execute(name, params, ctx)     │
│          │           └── [9-step pipeline] ← 领域层             │
│          │                 ├── PermissionPolicy.authorize()     │
│          │                 ├── domain dispatch → 基础设施层     │
│          │                 │     ├── Orchestrator → 进程内执行│
│          │                 │     └── Container → Docker exec   │
│          │                 └── SafetyLayer.scan_output()        │
│          │                                                     │
│          └── after_iteration(iter, ctx) ← metrics/logging      │
│                └── OTel spans ← 基础设施层                      │
│                                                               │
│  返回: Terminal(reason="completed", usage, duration)           │
└────────────────────────┬──────────────────────────────────────┘
                         │ LoopOutcome
                         ▼
┌─ 展示层 ──────────────────────────────────────────────────────┐
│  UiAdapter.render(TURN_COMPLETE)                              │
│  终端显示: "✓ Done — 2 tool calls, 15K tokens, 3.2s"          │
└───────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─ 基础设施层 (异步后处理) ─────────────────────────────────────┐
│  TranscriptWriter.append(turn) → JSONL file                   │
│  MemoryStore.upsert() → SQLite                                │
│  CacheWarmer.maybe_warm() → background ping                   │
│  OTel trace export → OTLP endpoint (if configured)            │
└──────────────────────────────────────────────────────────────┘
```

### 依赖注入模式

Harness 不使用 DI 框架，而是用 **工厂函数 + contextvars** 实现轻量级 DI：

```python
# harness/core/di.py — 简化的依赖注入

import contextvars

# ─── 全局服务注册表 (contextvars 保证 asyncio 安全) ───

_current_loop: contextvars.ContextVar["LoopContext | None"] = (
    contextvars.ContextVar("current_loop", default=None)
)

_current_tool_executor: contextvars.ContextVar["ToolExecutor | None"] = (
    contextvars.ContextVar("current_tool_executor", default=None)
)

_current_gatherer: contextvars.ContextVar["ContextGatherer | None"] = (
    contextvars.ContextVar("current_gatherer", default=None)
)


# ─── 工厂函数 ──────────────────────────────────────

def build_application_layer(config: "Config") -> "LoopDelegate":
    """
    组装应用层 — 创建所有领域层/基础设施层依赖并注入。

    这是唯一的"组装点" — 所有层间接口在此连接。
    """
    # 基础设施层
    llm = _create_llm_client(config.llm)
    sandbox = _create_sandbox(config.sandbox)
    mcp_manager = McpClientManager(config.mcp_servers)
    observability = _init_observability(config.observability)

    # 领域层
    safety = SafetyLayer(config.safety)
    tool_registry = ToolRegistry()
    memory_store = MemoryStore(config.memory)
    memory_reader = MemoryReader(memory_store, config.memory)
    repo_map = RepoMap(config.repomap) if config.repomap.enabled else None

    # 注册基础工具
    _register_builtin_tools(tool_registry)
    # 注册 MCP 工具 (异步)
    asyncio.create_task(mcp_manager.discover_and_register(tool_registry))

    # 权限策略
    policy = PermissionPolicy(config.permissions)

    # 工具执行器
    tool_executor = ToolExecutor(
        registry=tool_registry,
        safety=safety,
        policy=policy,
        sandbox=sandbox,
    )

    # 创建 delegate
    if config.loop_engine == "langgraph":
        loop = LangGraphLoop(llm, tool_executor, safety, config)
    else:
        delegate = ChatDelegate(
            ui=_ui_adapter,  # 展示层注入
            tool_executor=tool_executor,
            safety=safety,
            permission_policy=policy,
        )
        loop = AgenticLoop(delegate=delegate, ctx=None, config=config.loop)

    return loop
```

### 层间调用规则总结

```
┌────────────┬───────────────────────────────────────────────────┐
│ 规则        │ 说明                                              │
├────────────┼───────────────────────────────────────────────────┤
│ 依赖方向    │ Presentation → Application → Domain → Infra       │
│            │ 箭头方向 = 编译时 import 方向                       │
├────────────┼───────────────────────────────────────────────────┤
│ 接口归属    │ 接口 (ABC) 定义在被调用层                          │
│            │ Tool ABC 在领域层, LlmClient ABC 在领域层          │
│            │ UiAdapter ABC 在展示层                             │
├────────────┼───────────────────────────────────────────────────┤
│ 数据方向    │ LoopContext 在应用层创建, 向下传递到领域层/基础设施层 │
│            │ ToolOutput → 领域层, LlmResponse → 应用层          │
│            │ UiEvent → 展示层 (单向推送)                        │
├────────────┼───────────────────────────────────────────────────┤
│ 跨层通信    │ 向上: 返回值 (ToolOutput, LlmResponse)             │
│            │ 向下: 方法参数 (LoopContext, ToolContext)           │
│            │ 事件: UiAdapter.render(UiEvent) — 应用层→展示层     │
│            │ 回调: LoopDelegate 方法 — 应用层→领域层             │
├────────────┼───────────────────────────────────────────────────┤
│ 错误传播    │ 基础设施层 → ToolError/LlmError → 领域层           │
│            │ 领域层 → 分类 + 策略 → 应用层                       │
│            │ 应用层 → RecoveryAction → 重试/降级/终止           │
│            │ 展示层 → 用户可读消息 (不传播原始错误)               │
├────────────┼───────────────────────────────────────────────────┤
│ 测试隔离    │ 每层独立测试 — Mock 下层接口 (ABC)                 │
│            │ 展示层: Mock LoopDelegate                          │
│            │ 应用层: Mock LlmClient + Mock Tool                │
│            │ 领域层: Mock LlmClient + Stub Workspace           │
│            │ 基础设施层: Mock 外部服务 (VCR.py)                  │
├────────────┼───────────────────────────────────────────────────┤
│ 插件扩展    │ 插件可在展示层 (TUI widget)、领域层 (Tool)、        │
│            │ 基础设施层 (LlmClient provider) 注册                │
│            │ 所有扩展都通过 ABC 接口, 不直接修改框架代码          │
└────────────┴───────────────────────────────────────────────────┘
```

---

## 三、Agentic Loop、入口点与编排方式

### 3.1 入口点层级

```
CLI 入口 (harness/__main__.py)
  │
  ├── $ harness prompt "add login"   → 一次性, Pipe 模式
  ├── $ harness repl                  → 交互式 REPL
  ├── $ harness tui                   → 全屏 TUI
  └── $ harness doctor               → 健康检查
```

**三种 UI 模式共享同一套核心逻辑**，差异只在输入/输出适配器:

```python
# harness/cli/app.py
import typer
from harness.core.loop import AgenticLoop
from harness.core.context import ContextGatherer
from harness.config import Config

app = typer.Typer()

@app.command()
def prompt(text: str, model: str | None = None, max_turns: int = 30):
    """一次性 prompt 模式 (pipe/CI)"""
    config = Config.load().apply_cli_overrides(model=model)
    async def _run():
        session = Session.create_new(config)
        outcome = await session.run_turn(text)
        print(outcome.content)
    asyncio.run(_run())

@app.command()
def repl():
    """交互式 REPL (prompt_toolkit)"""
    asyncio.run(ReplApp().run())

@app.command()
def tui():
    """全屏 TUI (textual)"""
    TuiApp().run()  # textual 自带事件循环
```

### 3.2 两级 Loop 架构

**外层: Session.run_turn()** — 一次用户输入对应一次调用
**内层: AgenticLoop.run()** — 工具调用循环，持续到模型产出纯文本

```
Session.run_turn(user_input)               ← 外层: 每个用户消息调用一次
  │
  ├── 1. safety.scan_inbound(user_input)    ← 安全检查
  ├── 2. ctx = ContextGatherer.gather()     ← 上下文组装 (含 RepoMap 更新)
  ├── 3. messages = ctx.to_messages()       ← 组装 chat messages
  ├── 4. yield* AgenticLoop.run(messages)   ← 内层: 工具调用循环
  │       │
  │       ├── while iteration < max_turns:
  │       │   ├── check_signals()           ← 外部信号 (取消/暂停)
  │       │   ├── before_llm_call()         ← compaction check
  │       │   ├── call_llm()                ← 流式 API 调用
  │       │   ├── parse response:
  │       │   │   ├── text only → handle_text_response()
  │       │   │   │   ├── tool_intent_nudge? → 注入提醒 + continue
  │       │   │   │   └── TextAction.RETURN → break
  │       │   │   └── tool_use → execute_tool_calls()
  │       │   │       ├── 对每个 tool_call:
  │       │   │       │   ├── permission check
  │       │   │       │   ├── domain dispatch (Orch→进程内 / Cont→Docker)
  │       │   │       │   ├── execute with timeout
  │       │   │       │   └── safety scan output + wrap_for_llm()
  │       │   │       ├── DuplicateToolCallTracker 检查
  │       │   │       └── push tool_results → messages
  │       │   ├── after_iteration()         ← compaction evaluation
  │       │   └── continue / break (loop)
  │       └── return LoopOutcome
  │
  ├── 5. safety.scan_llm_response(outcome)  ← 防密钥外泄
  ├── 6. transcript.record(messages)        ← 持久化
  └── 7. return TurnSummary
```

### 3.3 AgenticLoop 状态机详细设计

```python
# harness/core/loop.py
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import AsyncIterator, Any
import asyncio
import time
import hashlib
import json

class LoopState(Enum):
    IDLE = auto()
    ASSEMBLING_CONTEXT = auto()
    CALLING_LLM = auto()
    HANDLING_TEXT = auto()
    EXECUTING_TOOLS = auto()
    AWAITING_APPROVAL = auto()
    COMPACTING = auto()
    RECOVERING = auto()
    DONE = auto()

class LoopTransition(Enum):
    NEXT_ITERATION = auto()     # 正常继续 → CALLING_LLM
    RETURN = auto()              # 退出循环 (text response)
    AWAIT_APPROVAL = auto()     # 暂停等待审批
    RECOVER = auto()             # 错误恢复路径

@dataclass
class LoopEvent:
    """驱动状态转换的事件"""
    kind: str  # "text" | "tool_calls" | "error" | "approval_needed" | "compaction_done"

class AgenticLoop:
    """
    核心 agentic loop — 纯 AsyncGenerator 实现。

    借鉴 Claude Code src/query.ts 的 query() generator:
    - yield 每条消息给上层 (流式输出)
    - return Terminal { reason: 'completed' | 'max_turns' | ... }
    - 6 条 recovery continue path 作为状态转换

    借鉴 IronClaw src/agent/agentic_loop.rs 的 run_agentic_loop():
    - LoopDelegate 策略模式解耦 consumer (chat/job/container)
    - DuplicateToolCallTracker 防重复失败
    """

    def __init__(
        self,
        delegate: "LoopDelegate",
        ctx: "LoopContext",
        config: "LoopConfig"
    ):
        self.delegate = delegate
        self.ctx = ctx
        self.config = config
        self.state = LoopState.IDLE
        self._dup_tracker = DuplicateToolCallTracker()
        self._truncation_tracker = TruncationTracker(max_truncations=3)
        self._circuit_breaker = CircuitBreaker(max_failures=3)

    async def run(self) -> AsyncIterator["LoopMessage"]:
        """
        主循环 — AsyncGenerator 模式。

        每条消息实时 yield 给调用方 (CLI/TUI)，
        调用方可以逐条渲染而不需要等待整个 turn 完成。

        Return: Terminal { reason, usage, duration }
        """
        self.state = LoopState.ASSEMBLING_CONTEXT
        iteration = 0
        start_time = time.monotonic()

        for iteration in range(1, self.config.max_turns + 1):
            # ---- Step 1: Check external signals ----
            signal = await self.delegate.check_signals()
            if signal == LoopSignal.STOP:
                return Terminal("stopped", self.ctx.usage, time.monotonic() - start_time)
            if signal == LoopSignal.INJECT_MESSAGE:
                self.ctx.messages.append(ChatMessage.user(signal.payload))

            # ---- Step 2: Pre-LLM hook (compaction, cost guard) ----
            early_outcome = await self.delegate.before_llm_call(self.ctx, iteration)
            if early_outcome:
                self.state = LoopState.DONE
                return Terminal.from_outcome(early_outcome)

            # ---- Step 3: Call LLM ----
            self.state = LoopState.CALLING_LLM
            try:
                response = await self.delegate.call_llm(self.ctx, iteration)
            except LlmError as e:
                recovery = self._classify_recovery(e, iteration)
                if recovery.kind == "retry":
                    await asyncio.sleep(recovery.delay)
                    continue
                elif recovery.kind == "compact_then_retry":
                    yield await self._auto_compact()
                    continue
                elif recovery.kind == "circuit_break":
                    return Terminal("error_circuit_break", self.ctx.usage, ...)
                else:
                    raise  # unrecoverable

            # ---- Step 4: Parse response ----
            if not response.tool_calls:
                # Text-only response
                self.state = LoopState.HANDLING_TEXT
                action = await self.delegate.handle_text_response(
                    response.text, self.ctx
                )
                if action == TextAction.RETURN:
                    self.state = LoopState.DONE
                    return Terminal("completed", self.ctx.usage, ...)
                # Tool intent nudge
                if (self.config.enable_tool_intent_nudge
                    and self._signals_tool_intent(response.text)):
                    await self.delegate.on_tool_intent_nudge(response.text, self.ctx)
                    self.ctx.messages.append(ChatMessage.user(TOOL_INTENT_NUDGE))
                continue

            # ---- Step 5: Execute tool calls ----
            self.state = LoopState.EXECUTING_TOOLS
            outcome = await self.delegate.execute_tool_calls(
                response.tool_calls, self.ctx
            )
            if outcome:
                self.state = LoopState.DONE
                return Terminal.from_outcome(outcome)

            # ---- Step 6: Duplicate failure tracking ----
            dup_count = self._dup_tracker.record(
                response.tool_calls,
                self.ctx.last_tool_batch_all_failed
            )
            if dup_count >= 5:
                self.ctx.force_text = True
                self.ctx.messages.append(ChatMessage.user(
                    "You have repeated the exact same failing tool call 5+ times. "
                    "Please try a DIFFERENT approach."
                ))
            elif dup_count >= 3:
                self.ctx.messages.append(ChatMessage.user(
                    "These tool calls keep failing the same way. Try a different approach."
                ))

            # ---- Step 7: Post-iteration ----
            await self.delegate.after_iteration(iteration, self.ctx)

            # ---- Step 8: Compaction check ----
            if self._compaction_needed():
                self.state = LoopState.COMPACTING
                yield await self._auto_compact()
                self.state = LoopState.CALLING_LLM

        # Max iterations reached
        self.state = LoopState.DONE
        return Terminal("max_turns", self.ctx.usage, time.monotonic() - start_time)

    # ─── Recovery classification ──────────────────────────────

    def _classify_recovery(self, error: LlmError, iteration: int) -> RecoveryAction:
        """将 API 错误映射到 6 条恢复路径之一"""
        if isinstance(error, RateLimitError):
            return RecoveryAction("retry", delay=error.retry_after or 2.0 ** min(iteration, 5))
        if isinstance(error, ContextOverflowError):
            if self._circuit_breaker.can_attempt():
                self._circuit_breaker.record_failure()
                return RecoveryAction("compact_then_retry")
            else:
                return RecoveryAction("circuit_break")
        if isinstance(error, AuthError):
            return RecoveryAction("credential_refresh", delay=0.5)
        return RecoveryAction("fatal")

    def _compaction_needed(self) -> bool:
        tokens = self.ctx.llm.estimate_tokens(self.ctx.messages)
        ratio = tokens / self.ctx.llm.context_window
        return ratio > self.config.compaction_threshold  # default: 0.80

    async def _auto_compact(self):
        """触发自动压缩"""
        compactor = ContextCompactor(self.ctx.llm, self.ctx.workspace)
        result = await compactor.compact(
            self.ctx.session.current_thread,
            self.ctx.messages,
            CompactionStrategy.AUTO_COMPACT
        )
        self.ctx.messages = result.messages
        if result.success:
            self._circuit_breaker.reset()
        return CompactBoundaryMessage(
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
            summary=result.summary
        )
```

### 3.4 DuplicateToolCallTracker 实现

```python
@dataclass
class DuplicateToolCallTracker:
    """
    对每批 tool_calls 计算 hash 指纹。
    连续相同失败 → 升级 (警告 → 强制 text-only)。

    借鉴 IronClaw src/agent/agentic_loop.rs DuplicateToolCallTracker
    """
    last_fingerprint: int | None = None
    consecutive_count: int = 0
    WARNING_THRESHOLD: int = 3
    FORCE_TEXT_THRESHOLD: int = 5

    @staticmethod
    def fingerprint(tool_calls: list["ToolCall"]) -> int:
        """计算一批 tool_calls 的 hash 指纹"""
        h = hashlib.sha256()
        for tc in sorted(tool_calls, key=lambda t: t.name):
            h.update(tc.name.encode())
            # 规范化 JSON key 顺序，确保相同参数产生相同 hash
            canonical = json.dumps(tc.input, sort_keys=True)
            h.update(canonical.encode())
        return int.from_bytes(h.digest()[:8], 'big')

    def record(self, tool_calls: list["ToolCall"], all_failed: bool) -> int:
        fp = self.fingerprint(tool_calls)
        if all_failed and self.last_fingerprint == fp:
            self.consecutive_count += 1
        elif all_failed:
            self.last_fingerprint = fp
            self.consecutive_count = 1
        else:
            self.reset()
            self.last_fingerprint = fp
        return self.consecutive_count

    def reset(self):
        self.last_fingerprint = None
        self.consecutive_count = 0
```

### 3.5 TruncationTracker 实现

```python
@dataclass
class TruncationTracker:
    """
    追踪 FinishReason.Length (截断) 频率。
    连续 3 次截断 → 强制 text-only (模型输出太短, 参数可能不完整)。

    借鉴 IronClaw agentic_loop.rs 的 truncation_count 逻辑。
    """
    max_truncations: int = 3
    count: int = 0

    def record(self) -> bool:
        """返回 True 表示应该 force_text"""
        self.count += 1
        return self.count >= self.max_truncations

    def reset(self):
        self.count = 0
```

### 3.6 编排总结 — 三者协作方式

```
┌─────────────────────────────────────────────────────────────┐
│                    ORCHESTRATION LAYER                       │
│                                                              │
│  LoopDelegate (策略接口)                                      │
│  ├── ChatDelegate      → 交互式对话 (用户可见, 可审批)        │
│  ├── JobDelegate       → 后台任务 (自治, allowed_tools 白名单)│
│  └── ContainerDelegate → Docker 容器内 worker                 │
│                                                              │
│  AgenticLoop (通用引擎)                                       │
│  ├── 不关心谁在调用 — 只实现 "LLM call → tool → observe" 循环  │
│  ├── 所有 consumer 共享: 状态机 / 防护 / 压缩 / 恢复           │
│  └── yield 每条消息 → 调用方自行决定如何渲染                    │
│                                                              │
│  ContextGatherer (上下文组装)                                  │
│  ├── 三段式 SystemPromptPart → Prompt Cache 协同优化           │
│  ├── RepoMap 后台更新 (asyncio.create_task)                   │
│  └── Memory 注入 (agentic search → RRF 融合)                  │
│                                                              │
│  ToolExecutor (工具管线)                                      │
│  ├── Permission → Domain dispatch → Execute → Safety scan    │
│  └── 所有工具调用必经此路径 (gateway handler 也不例外)          │
└─────────────────────────────────────────────────────────────┘
```

### 3.7 LangGraph 集成 — StateGraph Agent Loop

除了手写的 `AgenticLoop` AsyncGenerator，Harness 同时提供基于 **LangGraph** 的 `StateGraph` 实现。两者通过相同的 `LoopDelegate` 接口交换，通过配置开关切换。

**设计目标**:
- 利用 LangGraph 的内置重试、checkpointing、可视化调试能力
- 降低自研 loop 的维护成本
- 可选集成 LangSmith 进行 tracing 和调试
- 与现有 `LoopDelegate` 策略模式无缝对接

#### 3.7.1 AgentState 定义

```python
# harness/core/langgraph_loop.py

from typing import TypedDict, Annotated, Literal
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import AsyncSqliteSaver
from langgraph.pregel import RetryPolicy
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage

class AgentState(TypedDict):
    """
    LangGraph 状态 — 在节点间传递的完整 Agent 状态。

    使用 Annotated[list, add_messages] 实现消息自动追加
    (不是替换) 语义。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    context: "AssembledContext"
    iteration: int
    tool_calls: list["ToolCall"]
    errors: list["AgentError"]
    compaction_count: int
    terminal_reason: str | None
    session_id: str
    turn_id: str
```

#### 3.7.2 节点定义

```python
# ─── 节点函数 ───────────────────────────────────────

async def node_assemble_context(state: AgentState) -> dict:
    """组装上下文 → 产出 system + user messages"""
    gatherer = ContextGatherer.current()
    ctx = await gatherer.gather(
        cwd=state["context"].workspace.cwd,
        messages=state["messages"],
    )
    return {
        "context": ctx,
        "messages": [ctx.to_system_message()],
    }

async def node_call_llm(state: AgentState) -> dict:
    """调用 LLM — 流式或非流式"""
    ctx = state["context"]
    response = await ctx.llm.generate(
        messages=state["messages"],
        tools=ctx.tool_registry.get_schemas(),
    )
    new_messages = [AIMessage(
        content=response.text or "",
        tool_calls=response.tool_calls or [],
        id=response.id,
    )]
    return {
        "messages": new_messages,
        "tool_calls": response.tool_calls or [],
        "iteration": state["iteration"] + 1,
    }

async def node_execute_tools(state: AgentState) -> dict:
    """执行工具调用 → 产出 ToolMessage"""
    executor = ToolExecutor.current()
    tool_messages = []
    for tc in state["tool_calls"]:
        try:
            output = await executor.execute(tc.name, tc.input, state["context"])
            tool_messages.append(ToolMessage(
                content=output.content,
                tool_call_id=tc.id,
                name=tc.name,
            ))
        except ToolError as e:
            tool_messages.append(ToolMessage(
                content=f"Error: {e}",
                tool_call_id=tc.id,
                name=tc.name,
            ))
            state["errors"].append(e)
    return {"messages": tool_messages, "tool_calls": []}

async def node_handle_text(state: AgentState) -> dict:
    """文本响应 → 记录并准备退出"""
    last_msg = state["messages"][-1]
    return {
        "terminal_reason": "completed",
        "messages": [last_msg],
    }

async def node_handle_error(state: AgentState) -> dict:
    """错误处理 → 可重试则返回 call_llm, 否则终止"""
    last_error = state["errors"][-1] if state["errors"] else None
    if last_error and _is_retryable(last_error):
        # 注入错误信息为 user message, 继续循环
        return {"messages": [HumanMessage(
            content=f"Error occurred: {last_error}. Please try a different approach."
        )]}
    return {"terminal_reason": "error_fatal"}

async def node_compact(state: AgentState) -> dict:
    """上下文压缩"""
    compactor = ContextCompactor(state["context"].llm, state["context"].workspace)
    result = await compactor.compact(
        state["context"].session.current_thread,
        state["messages"],
        CompactionStrategy.AUTO_COMPACT,
    )
    return {
        "messages": result.messages,
        "compaction_count": state["compaction_count"] + 1,
    }

def _is_retryable(error: "AgentError") -> bool:
    return isinstance(error, (RateLimitError, OverloadedError, NetworkError))
```

#### 3.7.3 Graph 构建

```python
# ─── 图构建 ─────────────────────────────────────────

def build_agent_graph(
    checkpointer: "BaseCheckpointSaver | None" = None,
) -> "CompiledStateGraph":
    """
    构建 Agent StateGraph。

    Graph 结构:

              ┌─────────────────┐
              │ assemble_context │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
         ┌────│   call_llm      │◄──────────────┐
         │    └────────┬────────┘                │
         │             │                         │
         │    ┌────────▼────────┐                │
         │    │    router       │                │
         │    └───┬──┬──┬───────┘                │
         │        │  │  │                        │
         │   text │  │  │ error                  │
         │        │  │  └──────────┐             │
         │        │  │ tools       │             │
         │   ┌────▼──┴──┐  ┌──────▼──────┐      │
         │   │handle_text│  │handle_error  │     │
         │   └─────┬────┘  │(retryable?)  │     │
         │         │       └──────┬───────┘     │
         │      END/return    yes │  │ no       │
         │                  ┌─────┘  │          │
         │                  │        │          │
         │   ┌──────────────▼──┐ ┌──▼──────┐   │
         │   │ execute_tools   │ │  END    │   │
         │   └────────┬────────┘ │ (fatal) │   │
         │            │          └─────────┘   │
         │            │                        │
         │   ┌────────▼────────┐               │
         │   │  compaction?    │─── no ────────┘
         │   └────────┬───────┘
         │            │ yes
         │   ┌────────▼────────┐
         │   │    compact      │
         │   └────────┬────────┘
         │            │
         └────────────┘
    """

    workflow = StateGraph(AgentState)

    # 注册节点
    workflow.add_node("assemble_context", node_assemble_context)
    workflow.add_node("call_llm", node_call_llm,
        retry=RetryPolicy(
            max_attempts=3,
            initial_interval=1.0,
            backoff_factor=2.0,
            retry_on=(RateLimitError, OverloadedError, NetworkError),
        ))
    workflow.add_node("execute_tools", node_execute_tools)
    workflow.add_node("handle_text", node_handle_text)
    workflow.add_node("handle_error", node_handle_error)
    workflow.add_node("compact", node_compact)

    # 入口
    workflow.set_entry_point("assemble_context")

    # 边
    workflow.add_edge("assemble_context", "call_llm")

    # 条件路由: call_llm → text / tool_calls / error
    workflow.add_conditional_edges(
        "call_llm",
        _route_after_llm,
        {
            "text": "handle_text",
            "tool_calls": "execute_tools",
            "error": "handle_error",
        }
    )

    workflow.add_edge("handle_text", END)
    workflow.add_edge("execute_tools", "call_llm")  # 循环

    # handle_error: retryable → call_llm, fatal → END
    workflow.add_conditional_edges(
        "handle_error",
        lambda s: "call_llm" if s["terminal_reason"] is None else END,
        {
            "call_llm": "call_llm",
            END: END,
        }
    )

    # 编译
    return workflow.compile(checkpointer=checkpointer)

def _route_after_llm(state: AgentState) -> Literal["text", "tool_calls", "error"]:
    """路由决策: 根据 LLM 响应类型选择下一节点"""
    last_msg = state["messages"][-1]

    if state["errors"] and state["errors"][-1]:
        return "error"

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        # 检查重复失败
        fingerprint = DuplicateToolCallTracker.fingerprint(last_msg.tool_calls)
        if _is_duplicate_failure(fingerprint, state):
            return "error"
        return "tool_calls"

    return "text"
```

#### 3.7.4 Checkpointing 与恢复

```python
# ─── Checkpointing ──────────────────────────────────

class CheckpointManager:
    """
    对话检查点管理 — 支持断点续传和崩溃恢复。

    两种后端:
    - MemorySaver: 开发/测试用, 进程内 dict
    - AsyncSqliteSaver: 生产用, SQLite 持久化
    """

    @staticmethod
    def create(backend: Literal["memory", "sqlite"], db_path: str | None = None):
        match backend:
            case "memory":
                return MemorySaver()
            case "sqlite":
                return AsyncSqliteSaver.from_conn_string(
                    db_path or "~/.harness/checkpoints.db"
                )

    @staticmethod
    async def save(
        graph: "CompiledStateGraph",
        config: dict,
        state: AgentState,
    ):
        """保存检查点 — 每个 turn 结束时调用"""
        await graph.aget_state(config).update(state)

    @staticmethod
    async def resume(
        graph: "CompiledStateGraph",
        thread_id: str,
    ) -> AgentState | None:
        """恢复最近的检查点"""
        config = {"configurable": {"thread_id": thread_id}}
        state = await graph.aget_state(config)
        return state.values if state else None
```

#### 3.7.5 Streaming 输出

```python
# ─── LangGraph Streaming ────────────────────────────

async def run_langgraph_loop(
    graph: "CompiledStateGraph",
    user_input: str,
    thread_id: str,
    session_id: str,
) -> AsyncIterator["LoopMessage"]:
    """
    通过 LangGraph astream_events 流式输出。

    事件类型映射:
    - on_chat_model_stream → LoopMessage.text_delta
    - on_tool_start         → LoopMessage.tool_start
    - on_tool_end           → LoopMessage.tool_result
    - on_chain_end          → LoopMessage.turn_complete
    """
    config = {"configurable": {"thread_id": thread_id}}

    async for event in graph.astream_events(
        {"messages": [HumanMessage(content=user_input)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]
        name = event.get("name", "")

        match (kind, name):
            case ("on_chat_model_stream", _):
                chunk = event["data"]["chunk"]
                if chunk.content:
                    yield LoopMessage.text_delta(chunk.content)

            case ("on_tool_start", _):
                yield LoopMessage.tool_start(
                    tool_name=name,
                    tool_input=event["data"].get("input", {}),
                )

            case ("on_tool_end", _):
                yield LoopMessage.tool_result(
                    tool_name=name,
                    output=event["data"].get("output", ""),
                )

            case ("on_chain_end", "LangGraph"):
                yield LoopMessage.turn_complete(
                    terminal_reason=event["data"].get("output", {}).get(
                        "terminal_reason", "completed"
                    ),
                )
```

#### 3.7.6 与现有 LoopDelegate 集成

```python
# ─── 集成层 ─────────────────────────────────────────

class LangGraphDelegate(LoopDelegate):
    """
    将 LangGraph 图包装为 LoopDelegate — 与手写 AgenticLoop
    共享相同的接口, 通过配置切换。
    """

    def __init__(self, graph: "CompiledStateGraph", thread_id: str):
        self.graph = graph
        self.thread_id = thread_id

    async def call_llm(self, ctx, iteration) -> "LlmResponse":
        # LangGraph 内部处理 LLM 调用
        return await self.graph.ainvoke(
            {"context": ctx, "iteration": iteration},
            {"configurable": {"thread_id": self.thread_id}},
        )

    async def execute_tool_calls(self, tool_calls, ctx) -> "LoopOutcome | None":
        # LangGraph 内部处理工具执行
        ...

    # ... 其他 LoopDelegate 方法 ...


# ─── 配置切换 ────────────────────────────────────────
# harness.toml:
# [loop]
# engine = "langgraph"  # "native" | "langgraph"


def create_loop(config: "Config") -> "AgenticLoop | LangGraphLoop":
    """工厂函数 — 根据配置选择 loop 引擎"""
    match config.loop_engine:
        case "langgraph":
            graph = build_agent_graph(checkpointer=CheckpointManager.create("sqlite"))
            return LangGraphLoop(graph, config)
        case "native":
            return AgenticLoop(delegate=..., ctx=..., config=...)
```

**LangGraph 相比手写 AsyncGenerator 的优势**:

| 维度 | AsyncGenerator | LangGraph |
|------|---------------|-----------|
| 重试 | 手动实现 (retry.py) | RetryPolicy 内置 |
| Checkpointing | 无 (需自建) | AsyncSqliteSaver 内置 |
| 可视化 | 无 | LangSmith Studio |
| 调试 | pdb / log | LangSmith trace 回放 |
| 流式输出 | AsyncIterator | astream_events |
| 条件分支 | if/elif 硬编码 | conditional_edges 声明式 |
| 中断/恢复 | 手动管理 | interrupt() + Command(resume=...) |
| 学习曲线 | 低 | 中 |

#### 3.7.7 中断与人工审批

```python
# ─── 人工审批中断 ───────────────────────────────────

from langgraph.types import interrupt, Command

async def node_execute_tools_with_approval(state: AgentState) -> dict:
    """带审批中断的工具执行"""
    for tc in state["tool_calls"]:
        tool = ToolRegistry.current().get(tc.name)
        if tool.requires_approval(tc.input) == ApprovalRequirement.ALWAYS:
            # LangGraph interrupt — 暂停执行, 等待外部输入
            approval = interrupt({
                "type": "approval_needed",
                "tool_name": tc.name,
                "params": tc.input,
            })
            if not approval.get("approved"):
                return {"messages": [ToolMessage(
                    content="User denied the tool call.",
                    tool_call_id=tc.id,
                )]}

        output = await ToolExecutor.current().execute(tc.name, tc.input, state["context"])
        # ... 正常处理 ...

    return {"messages": tool_messages}

# 恢复执行:
# graph.invoke(Command(resume={"approved": True}), config)
```

---

### 3.8 Session / Thread / Turn 数据模型

Agentic Loop 的状态由三层嵌套的数据模型承载:

```
Session (一次 CLI 启动)
  ├── Thread 1 (一次对话主题)
  │     ├── Turn 1 (一次用户消息 → 完整响应)
  │     ├── Turn 2
  │     └── ...
  ├── Thread 2
  └── ...
```

```python
# harness/core/session.py

from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4
from enum import Enum

class SessionStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"

class Session(BaseModel):
    """
    一次 CLI 启动对应一个 Session。

    生命周期:
    - created: CLI 启动, 加载配置
    - active: 用户交互中
    - paused: 后台运行 (agentic triggers)
    - completed: 正常退出
    - error: 异常终止

    跨 Thread 追踪:
    - total_tokens_used: 所有 Thread 的 token 总和
    - total_cost_usd: 所有 LLM 调用的估算费用
    - active_thread_id: 当前活跃的 Thread
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    config_hash: str                    # 配置内容的 BLAKE3 hash
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    threads: list["Thread"] = Field(default_factory=list)
    active_thread_id: str | None = None
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    workspace_path: str

class ThreadStatus(Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPACTED = "compacted"

class Thread(BaseModel):
    """
    一个 Thread 代表一次连续的对话主题。

    与 Claude Code 的 conversation 概念对应:
    - 一个 session 可以有多个 thread
    - /clear 创建新 thread
    - /compact 压缩当前 thread
    - 旧 thread 归档到 workspace daily log

    compaction_summary: 当 thread 被压缩时, 保存 LLM 生成的摘要
    messages_snapshot: 压缩后的精简消息列表 (最近 N turns)
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    session_id: str
    status: ThreadStatus = ThreadStatus.ACTIVE
    turns: list["Turn"] = Field(default_factory=list)
    compaction_summary: str | None = None
    messages_snapshot: list["ChatMessage"] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    archived_at: datetime | None = None
    total_tokens_used: int = 0

    @property
    def message_count(self) -> int:
        return sum(len(t.messages) for t in self.turns)

class TurnStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    STOPPED = "stopped"
    ERROR = "error"
    CIRCUIT_BREAK = "circuit_break"

class Turn(BaseModel):
    """
    一次 Turn = 一个用户输入 → Agent 完整响应。

    内容:
    - user_input: 用户原始输入 (或系统注入消息)
    - messages: 该 turn 内所有 API messages
    - tool_calls: 该 turn 内所有工具调用 (含结果)
    - outcome: AgenticLoop 的最终状态

    LLM 调用可能发生多次 (工具调用循环),
    但逻辑上属于同一个 turn。
    """
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    thread_id: str
    user_input: str
    status: TurnStatus = TurnStatus.RUNNING
    messages: list["ChatMessage"] = Field(default_factory=list)
    tool_calls: list["ToolCallResult"] = Field(default_factory=list)
    outcome: "LoopOutcome | None" = None
    llm_call_count: int = 0
    tokens_used: int = 0
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    duration_ms: int = 0

    @property
    def tool_call_success_rate(self) -> float:
        if not self.tool_calls:
            return 1.0
        successful = sum(1 for tc in self.tool_calls if not tc.is_error)
        return successful / len(self.tool_calls)

@dataclass
class ToolCallResult:
    """单次工具调用的完整记录"""
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    output: str | None
    is_error: bool
    duration_ms: int
    risk_level: str  # "low" | "medium" | "high" | "blocked"
    truncated: bool
    retry_count: int = 0
    timestamp: float = field(default_factory=time.monotonic)

# ─── Session 生命周期管理 ──────────────────────────────────

class SessionManager:
    """管理 Session 的创建、恢复、切换、归档"""

    def __init__(self, config: "Config"):
        self.config = config
        self._active_session: Session | None = None
        self._transcript_writer: "TranscriptWriter | None" = None

    async def create_session(self, cwd: str) -> Session:
        """创建新 session"""
        session = Session(
            config_hash=self._hash_config(),
            workspace_path=cwd,
        )
        self._active_session = session
        self._transcript_writer = TranscriptWriter(session)
        await self._transcript_writer.write_event("session_start", session.model_dump())
        return session

    async def create_thread(self, session: Session) -> Thread:
        """在 session 内创建新 thread"""
        thread = Thread(session_id=session.id)
        session.threads.append(thread)
        session.active_thread_id = thread.id
        return thread

    async def create_turn(self, thread: Thread, user_input: str) -> Turn:
        """在 thread 内创建新 turn"""
        turn = Turn(thread_id=thread.id, user_input=user_input)
        thread.turns.append(turn)
        return turn

    async def end_session(self, session: Session, status: SessionStatus):
        """结束 session 并写入 transcript"""
        session.status = status
        session.ended_at = datetime.now()
        if self._transcript_writer:
            await self._transcript_writer.write_event("session_end", {
                "status": status.value,
                "total_tokens": session.total_tokens_used,
                "total_cost": session.total_cost_usd,
            })
            await self._transcript_writer.close()

    async def resume_session(self, transcript_path: str) -> Session | None:
        """从 transcript 恢复 session (崩溃恢复)"""
        reader = TranscriptReader(transcript_path)
        events = await reader.read_all()
        return self._reconstruct_session(events)

    def _hash_config(self) -> str:
        """对配置内容做 BLAKE3 hash — 用于检测跨 session 配置变更"""
        import blake3
        return blake3.blake3(
            self.config.model_dump_json().encode()
        ).hexdigest()[:12]
```

---

### 3.9 SubAgent 实现

SubAgent (子 Agent) 是 Agent 工具调用的一种特殊形式 — 将子任务派发给独立的 Agent 实例, 在受限上下文中执行, 返回结构化结果。

```python
# harness/core/subagent.py

@dataclass
class SubAgentConfig:
    """子 Agent 的沙箱配置"""
    max_depth: int = 2               # 最大嵌套深度 (子 Agent 不能再 spawn)
    max_turns: int = 15              # 子 Agent 最大循环次数
    max_tokens: int = 32_000         # 子 Agent 上下文窗口
    timeout_seconds: int = 120       # 总执行超时
    allowed_tools: set[str] = field(default_factory=lambda: {
        "file_read", "glob_search", "grep_search",
        "web_search", "web_fetch",
    })
    disallowed_tools: set[str] = field(default_factory=lambda: {
        # 子 Agent 永远不能使用的工具
        "bash_exec", "task_stop", "ask_user_question",
        "memory_write", "memory_delete", "agent",
        "exit_plan_mode",
    })
    isolation: Literal["process", "worktree", "none"] = "process"
    structured_output_schema: dict | None = None  # 要求子 Agent 返回结构化 JSON

class SubAgentManager:
    """
    子 Agent 派发 + 上下文隔离 + 结果合并。

    借鉴 Claude Code AgentTool + TaskTool:
    - 主 Agent 调用 agent() 工具 → 创建子 Agent
    - 子 Agent 在受限上下文中运行
    - 子 Agent 结果作为 tool_result 返回给主 Agent
    - 支持结构化输出 (子 Agent 被迫返回 JSON)

    借鉴 IronClaw JobDelegate:
    - 子 Agent = 一次性的 Job
    - 独立的消息数组 (不共享主 Agent 的上下文)
    - 工具 allowlist 白名单
    """

    def __init__(self, config: "Config"):
        self.config = config
        self._active_subagents: dict[str, "SubAgentContext"] = {}
        self._total_spawned = 0

    async def spawn(
        self,
        parent_ctx: "LoopContext",
        task: str,
        config: SubAgentConfig | None = None,
    ) -> "SubAgentResult":
        """
        派发子 Agent 任务。

        流程:
        1. 检查嵌套深度 (子 Agent 不能再 spawn)
        2. 构建受限上下文 (独立 messages + 白名单工具)
        3. 注入结构化输出指令 (如果需要 JSON)
        4. 运行 AgenticLoop (或 LangGraph loop)
        5. 收集结果并返回
        6. 记录 ActionRecord
        """
        cfg = config or SubAgentConfig()

        # Step 1: 深度检查
        if parent_ctx.subagent_depth >= cfg.max_depth:
            raise SubAgentDepthExceededError(
                f"max subagent depth ({cfg.max_depth}) exceeded"
            )

        # Step 2: 数量检查
        if self._total_spawned >= self.config.max_subagents_per_session:
            raise SubAgentLimitExceededError(
                f"max subagents ({self.config.max_subagents_per_session}) exceeded"
            )

        # Step 3: 构建子 Agent 上下文
        sub_ctx = await self._build_sub_context(parent_ctx, cfg, task)
        self._total_spawned += 1

        # Step 4: 运行
        try:
            loop = AgenticLoop(
                delegate=JobDelegate(sub_ctx, allowed_tools=cfg.allowed_tools),
                ctx=sub_ctx,
                config=LoopConfig(max_turns=cfg.max_turns, max_tokens=cfg.max_tokens),
            )

            result = await asyncio.wait_for(
                loop.run_to_completion(),
                timeout=cfg.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return SubAgentResult(
                success=False,
                error=f"Subagent timed out after {cfg.timeout_seconds}s",
                partial_output=sub_ctx.last_text_response,
            )

        # Step 5: 结构化输出提取
        if cfg.structured_output_schema and result.content:
            extracted = self._extract_structured_output(
                result.content, cfg.structured_output_schema
            )
            return SubAgentResult(
                success=True,
                content=json.dumps(extracted),
                structured_output=extracted,
                turns_used=result.turns,
                tokens_used=result.tokens_used,
            )

        # Step 6: 返回
        return SubAgentResult(
            success=result.kind == "completed",
            content=result.content or result.summary,
            turns_used=result.turns,
            tokens_used=result.tokens_used,
        )

    async def _build_sub_context(
        self,
        parent_ctx: "LoopContext",
        cfg: SubAgentConfig,
        task: str,
    ) -> "LoopContext":
        """
        构建子 Agent 的受限上下文:
        - 全新的 messages 列表
        - 只包含白名单工具
        - 继承父 Agent 的 workspace 引用 (只读)
        - 可选的 git worktree 隔离
        """
        # 只选择白名单工具
        restricted_tools = [
            t for t in parent_ctx.tool_registry.all_tools()
            if t.name in cfg.allowed_tools
            and t.name not in cfg.disallowed_tools
        ]

        sub_registry = ToolRegistry()
        for t in restricted_tools:
            sub_registry.register(t)

        # 可选的 worktree 隔离
        workspace_path = parent_ctx.workspace.cwd
        if cfg.isolation == "worktree" and parent_ctx.workspace.is_git_repo:
            worktree_path = await self._create_worktree(parent_ctx.workspace)
            if worktree_path:
                workspace_path = worktree_path

        # 构建子 Agent 专用的 system prompt
        system_prompt = f"""You are a sub-agent with a specific task. Complete it and return your result.

## Task
{task}

## Constraints
- You have access to a limited set of tools
- You CANNOT spawn additional sub-agents
- You CANNOT execute shell commands
- You CANNOT modify files (read-only access)
- Return your result as plain text or structured JSON
- Do NOT ask the user questions — work autonomously
"""

        return LoopContext(
            llm=parent_ctx.llm,
            tool_registry=sub_registry,
            session=parent_ctx.session,
            workspace=Workspace(cwd=workspace_path),
            system_prompt=system_prompt,
            subagent_depth=parent_ctx.subagent_depth + 1,
            parent_agent_id=parent_ctx.agent_id,
        )

    async def _create_worktree(self, workspace: "Workspace") -> str | None:
        """创建临时 git worktree 用于文件隔离"""
        import tempfile, os
        tmp = tempfile.mkdtemp(prefix="harness-subagent-")
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", workspace.cwd, "worktree", "add", "--detach", tmp,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            return tmp
        logger.warning(f"Worktree creation failed: {stderr.decode()}")
        return None

@dataclass
class SubAgentResult:
    """子 Agent 执行结果"""
    success: bool
    content: str | None = None
    structured_output: dict | None = None
    error: str | None = None
    partial_output: str | None = None
    turns_used: int = 0
    tokens_used: int = 0


# ─── AgentTool (供主 Agent 调用的工具接口) ──────────────

class AgentTool(Tool):
    """子 Agent 派发工具"""
    name = "agent"
    description = "Launch a sub-agent to handle a specific task autonomously."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the sub-agent to complete"
                },
                "agent_type": {
                    "type": "string",
                    "enum": ["general", "explore", "plan", "review"],
                    "default": "general",
                    "description": "Sub-agent specialization"
                },
                "expected_output": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "default": "text",
                },
                "output_schema": {
                    "type": "object",
                    "description": "JSON Schema for structured output (if expected_output=json)"
                },
                "max_turns": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30},
            },
            "required": ["task"],
        }

    async def execute(self, params: dict, ctx: "ToolContext") -> ToolOutput:
        manager = SubAgentManager(ctx.config)
        config = SubAgentConfig(
            max_turns=params.get("max_turns", 10),
            structured_output_schema=params.get("output_schema"),
        )
        result = await manager.spawn(ctx, params["task"], config)

        if result.success:
            return ToolOutput(content=result.content or "Done.", risk_level="low")
        return ToolOutput(
            content=f"Sub-agent failed: {result.error}",
            is_error=True,
            risk_level="low",
        )
```

**安全边界总结**:

| 边界 | 限制 | 实施方式 |
|------|------|---------|
| 嵌套深度 | max_depth=2 | SubAgentConfig 检查 |
| 工具访问 | allowed_tools 白名单 | ToolRegistry 过滤 |
| 文件系统 | 可选 worktree 隔离 | git worktree add --detach |
| 网络 | 只读工具 (web_fetch/search) | 白名单策略 |
| 交互 | 禁止 ask_user_question | disallowed_tools |
| 并发 | 最多 20 个子 Agent | _total_spawned 计数 |
| 超时 | 120s 默认 | asyncio.wait_for |
| Token | 32K 上下文窗口 | LoopConfig.max_tokens |

---

### 3.10 多模型路由 (Multi-Model Router)

不同任务需要不同能力/成本比的模型。`ModelRouter` 根据任务类型自动选择模型。

```python
# harness/llm/router.py

@dataclass
class ModelTier:
    """模型层级"""
    cheap: str       # 低成本 (Haiku) — 摘要、嵌入、计数
    default: str     # 默认 (Sonnet) — 主循环、代码生成
    expensive: str   # 高能力 (Opus) — 架构设计、安全审查

class ModelRouter:
    """
    任务驱动的模型路由器。

    决策逻辑 — 不是让 LLM 选模型, 而是根据任务类别硬路由:

    ┌──────────────────────────┬──────────────┬──────────────────────┐
    │ 任务                      │ 模型层        │ 理由                  │
    ├──────────────────────────┼──────────────┼──────────────────────┤
    │ compaction 摘要           │ cheap        │ 高容量, 简单任务       │
    │ embedding 生成            │ cheap        │ 批处理, 非延迟敏感     │
    │ RepoMap token 估算        │ cheap        │ 辅助函数, 不需要推理   │
    │ 结构化输出解析             │ cheap        │ 低复杂度               │
    │ cache warming pings       │ cheap        │ max_tokens=1          │
    │ 安全检查 (二级审查)         │ cheap        │ 模式匹配为主           │
    │ 主 Agent loop             │ default      │ 需要推理 + 工具使用    │
    │ 代码生成                   │ default      │ 平衡成本和能力         │
    │ 规划/架构设计              │ expensive    │ 需要深度推理           │
    │ 安全审查 (一级)            │ expensive    │ 需要 thoroughness      │
    │ 关键 Bug 修复              │ expensive    │ 不能出错               │
    └──────────────────────────┴──────────────┴──────────────────────┘

    动态降级:
    - 连续 3 次 529 Overloaded → expensive → default (自动降级)
    - fast mode cooldown: default → cheap (临时)
    - cheap model 不可用时 → default (fallback)
    """

    def __init__(self, config: "Config"):
        self.tiers = ModelTier(
            cheap=config.llm.fallback_model or "claude-haiku-3-5-20251001",
            default=config.llm.model or "claude-sonnet-4-6-20250514",
            expensive=config.llm.expensive_model or config.llm.model,
        )
        self._cooldown_until: dict[str, float] = {}

    # ─── 路由表 ─────────────────────────────────────

    ROUTES: dict[str, str] = {
        "compaction":        "cheap",
        "embedding":         "cheap",
        "token_count":       "cheap",
        "cache_warm":        "cheap",
        "structured_parse":  "cheap",
        "safety_secondary":  "cheap",
        "main_loop":         "default",
        "code_generation":   "default",
        "planning":          "expensive",
        "architecture":      "expensive",
        "security_review":   "expensive",
        "bug_fix_critical":  "expensive",
    }

    def route(self, task: str) -> str:
        """根据任务类型返回模型 ID"""
        tier_name = self.ROUTES.get(task, "default")

        # 检查 cooldown
        now = time.monotonic()
        if tier_name in self._cooldown_until and now < self._cooldown_until[tier_name]:
            tier_name = "cheap"  # 降级

        return getattr(self.tiers, tier_name)

    def set_cooldown(self, tier: str, duration_seconds: float = 600.0):
        """设置模型层级的冷却期 (应对 429/529)"""
        self._cooldown_until[tier] = time.monotonic() + duration_seconds

    def route_for_llm_request(
        self, messages: list["ChatMessage"], task: str = "main_loop"
    ) -> str:
        """
        增强路由 — 根据消息规模动态调整。
        如果上下文已经 > 150K tokens, 自动切换到更大的上下文窗口模型。
        """
        model = self.route(task)

        # Token-based override: 大上下文 → 大窗口模型
        estimated_tokens = estimate_tokens(messages)
        if estimated_tokens > 150_000:
            return self.tiers.expensive  # Opus 200K 窗口

        return model
```

**路由集成**:

```python
# 在 ContextGatherer 和 CompactionEngine 中使用:

class CompactionEngine:
    async def _auto_compact(self, thread, messages, tokens_before):
        model = self.router.route("compaction")  # → cheap model
        return await self.llm.complete(
            prompt,
            model=model,          # 覆盖默认模型
            max_tokens=2000,
            temperature=0.0,
        )
```

---

### 3.11 流式架构 (SSE → UI 渲染管线)

```
┌─────────────────────────────────────────────────────────────────┐
│                     STREAMING ARCHITECTURE                        │
│                                                                  │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌─────────────┐ │
│  │ LLM API  │ → │ SSE/NDJSON│ → │ EventBus │ → │  UI Render  │ │
│  │ Stream   │   │ Parser    │   │ (asyncio │   │  (TUI/CLI)  │ │
│  │          │   │           │   │  Queue)  │   │             │ │
│  └──────────┘   └───────────┘   └──────────┘   └─────────────┘ │
│       │              │                │                │         │
│  Anthropic      chunk →          Event:         terminal      │
│  SSE stream     StreamEvent      "text_delta"   repaint        │
│  OR             or               "tool_start"   or             │
│  LangGraph      StreamMessage    "tool_progress" prompt_       │
│  astream_events (internal)       "tool_result"  toolkit        │
│                                  "turn_complete" render        │
│                                                                  │
│  Pipeline:                                                       │
│  SSE bytes → ndjson.parse → StreamEvent → EventBus.put()        │
│       → UI.update() → terminal repaint                          │
│                                                                  │
│  Backpressure: EventBus 有界队列 (max 1000 pending).              │
│  队列满时 → 丢弃中间 text_delta 事件 (保留最新),                   │
│  tool_start / tool_end / error 从不会被丢弃.                      │
└─────────────────────────────────────────────────────────────────┘
```

```python
# harness/core/streaming.py

from enum import Enum
from dataclasses import dataclass
from typing import Any
import asyncio

class StreamEventKind(Enum):
    TEXT_DELTA = "text_delta"
    TEXT_DONE = "text_done"
    TOOL_START = "tool_start"
    TOOL_PROGRESS = "tool_progress"
    TOOL_END = "tool_end"
    TURN_COMPLETE = "turn_complete"
    ERROR = "error"
    HEARTBEAT = "heartbeat"

@dataclass
class StreamEvent:
    """统一的流事件 — 所有 LLM 提供者 + LangGraph 都映射到此格式"""
    kind: StreamEventKind
    data: Any = None
    timestamp: float = 0.0
    turn_id: str = ""

    @staticmethod
    def text_delta(text: str, turn_id: str = "") -> "StreamEvent":
        return StreamEvent(StreamEventKind.TEXT_DELTA, text, time.monotonic(), turn_id)

    @staticmethod
    def tool_start(name: str, input: dict, turn_id: str = "") -> "StreamEvent":
        return StreamEvent(StreamEventKind.TOOL_START, {"name": name, "input": input}, time.monotonic(), turn_id)

    @staticmethod
    def tool_end(name: str, output: str, duration_ms: int, turn_id: str = "") -> "StreamEvent":
        return StreamEvent(StreamEventKind.TOOL_END, {"name": name, "output": output, "duration_ms": duration_ms}, time.monotonic(), turn_id)

class EventBus:
    """
    有界异步队列 — 连接 SSE 解析器和 UI 渲染器。

    背压策略:
    - 容量: maxsize=1000
    - 满时 text_delta 丢弃 (UI 只在下一帧渲染最新文本)
    - tool_start/end、error、turn_complete: 永不丢弃 (用 put 阻塞)
    """
    DROPPABLE = {StreamEventKind.TEXT_DELTA}
    CRITICAL = {StreamEventKind.TOOL_START, StreamEventKind.TOOL_END,
                StreamEventKind.ERROR, StreamEventKind.TURN_COMPLETE}

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=maxsize)

    async def put(self, event: StreamEvent):
        if event.kind in self.DROPPABLE and self._queue.full():
            # 丢弃旧的 TEXT_DELTA, 放入新的
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        await self._queue.put(event)

    async def get(self) -> StreamEvent:
        return await self._queue.get()

    def get_nowait(self) -> StreamEvent | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


# ─── SSE Parser ──────────────────────────────────────

class SseParser:
    """
    解析 Anthropic SSE 流 (或 OpenAI NDJSON 流) → StreamEvent。

    支持:
    - Anthropic: server-sent events (message_start/content_block_delta/...)
    - OpenAI: NDJSON (每行一个 JSON)
    - LangGraph: Python 对象流 (passthrough)
    """

    async def parse_anthropic(self, stream) -> AsyncIterator[StreamEvent]:
        """解析 Anthropic SSE 流"""
        current_tool_name = None
        current_tool_input = {}
        text_buffer = ""

        async for event in stream:
            match event.type:
                case "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_name = block.name
                        current_tool_input = {}
                        yield StreamEvent.tool_start(block.name, {})

                case "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        text_buffer += delta.text
                        yield StreamEvent.text_delta(delta.text)
                    elif delta.type == "input_json_delta":
                        current_tool_input = _merge_json(
                            current_tool_input, delta.partial_json
                        )

                case "content_block_stop":
                    if current_tool_name:
                        yield StreamEvent.tool_end(
                            current_tool_name,
                            text_buffer,
                            int((time.monotonic() - start) * 1000),
                        )


# ─── LLM → EventBus 桥接 ────────────────────────────

class StreamingLlmClient(LlmClient):
    """将 LLM 流式响应桥接到 EventBus"""

    def __init__(self, event_bus: EventBus, provider: "LlmClient"):
        self.event_bus = event_bus
        self.provider = provider

    async def generate_stream(
        self, messages: list["ChatMessage"], **kwargs
    ) -> AsyncIterator[StreamEvent]:
        """流式生成 → yield event + put on bus"""
        async for event in self.provider.stream(messages, **kwargs):
            yield event
            await self.event_bus.put(event)


# ─── UI 渲染循环 ────────────────────────────────────

class UiRenderLoop:
    """
    TUI/CLI 渲染循环 — 从 EventBus 消费事件并更新终端。

    支持两种模式:
    - TUI (textual): 消息到达时重新渲染 widget
    - CLI (print): 行缓冲输出 (实时打印 text_delta)
    """

    def __init__(self, event_bus: EventBus, app: "textual.App | None" = None):
        self.event_bus = event_bus
        self.app = app
        self._running = False

    async def run(self):
        """主渲染循环"""
        self._running = True
        while self._running:
            event = await self.event_bus.get()
            await self._handle(event)

    async def _handle(self, event: StreamEvent):
        match event.kind:
            case StreamEventKind.TEXT_DELTA:
                # CLI: 立即打印 (无换行)
                # TUI: 追加到 text widget, 在下一帧渲染
                if self.app:
                    await self.app.update_text(event.data)
                else:
                    print(event.data, end="", flush=True)

            case StreamEventKind.TOOL_START:
                # 显示工具调用指示器
                pass

            case StreamEventKind.TOOL_END:
                # 显示工具结果摘要
                pass

            case StreamEventKind.TURN_COMPLETE:
                # print "\n" + summary
                pass

    def stop(self):
        self._running = False
```

### 3.12 Skills 系统与行为契约（借鉴 Superpowers）

Harness 借鉴 **Superpowers**（obra/superpowers, 149K stars）的零依赖技能框架，通过纯 Markdown 注入实现行为契约。Skills 不是可选的"建议"——它们是强制性的行为约束，通过 bootstrap 注入管线和 Red Flags 机制确保 Agent 遵守。

#### 3.12.1 Bootstrap 注入管线

借鉴 Superpowers 的三阶段注入模式，Harness 在 Session 启动时强制注入技能元数据，**在 Agent 首次响应之前完成**：

```
Session 启动 (或 /clear, /compact)
    │
    ├── 1. Hook 注册
    │   └── SessionStart hook (async: false) — 阻塞 Agent 响应直到注入完成
    │
    ├── 2. 平台检测
    │   └── 通过环境变量检测运行模式 (CLI/TUI/REPL)
    │   └── 选择对应的 skill 加载路径
    │
    └── 3. Skill 元数据注入
        └── 读取 .harness/skills/*/SKILL.md 的 YAML frontmatter
        └── 提取: name, description, when_to_use, priority, requires
        └── 包装在 <EXTREMELY_IMPORTANT> 标签中
        └── 注入为 system prompt 的最前缀（在 STATIC 块之前）
```

**注入优先级**: Bootstrap skill 元数据放在 system prompt 的最前面（在角色定义之前），确保 Agent 在处理任何其他指令前先加载行为约束。

**注入格式**: `<EXTREMELY_IMPORTANT>`, `<MANDATORY>`, `<CRITICAL>` 标签层次表明约束的强制级别，借鉴 Superpowers 的标签化注入，实验表明这比平文注入提高 ~10x 的遵守率（从 6-10% 到 ~66%）。

#### 3.12.2 SKILL.md 的 CSO 描述模式

每个 skill 的 YAML frontmatter 遵循 **CSO (Condition/Situation/Opportunity) 模式**——description 字段描述 *何时触发*，而非 *技能做什么*：

```yaml
---
name: test-driven-development
# CSO: 描述触发条件，不是功能摘要
description: |
  在编写或修改代码时使用。绝不要跳过 RED-GREEN-REFACTOR 循环，
  绝不要在编写测试之前编写实现代码。如果你正在修改任何 .py 文件，
  此技能适用。
priority: mandatory  # mandatory | recommended | optional
requires: []
---
```

**为什么 CSO 有效**:
- Agent 在搜索可用技能时，`description` 字段是主要匹配目标
- 功能摘要（"教你怎么写测试"）不会触发 Agent 的工具搜索
- 情境描述（"当你正在修改代码时使用"）直接关联到 Agent 的当前意图
- 借鉴 Superpowers 的验证：CSO 描述使技能触发率从 ~10% 提升到 ~66%

#### 3.12.3 1% 规则与 Red Flags 表

**1% 规则**: 如果有任何可能性（哪怕只有 1%）某个 skill 适用于当前任务，Agent 必须先调用该 skill。这是硬性约束，不是建议。

**Red Flags 表**: 借鉴 Superpowers 的"理性化阻断"模式，Harness 在 skill 元数据中预列 Agent 常用的 12+ 种跳过技能的借口，显式阻断：

| Agent 可能产生的借口 | 为什么它是错的 | 阻断规则 |
|---------------------|--------------|---------|
| "这只是一个简单的问题" | 简单问题中的 bug 代价最高 | 永远不能作为跳过 skill 的理由 |
| "这个 skill 太重量级了" | Skill 开销远小于 bug 修复开销 | 由 skill priority 决定，Agent 无权判断 |
| "我先看看代码再决定" | 先看代码 = 已经违反了 TDD（应该先写测试） | 违反 RED-GREEN-REFACTOR 顺序 |
| "我需要更多上下文" | 上下文可以从 skill 模板获取 | 调用 skill 获取上下文 |
| "这个改动太简单，不需要测试" | 没有"太简单"的改动 | TDD skill 标注为 mandatory |
| "我知道这个模式，不需要读 skill" | 记忆不可靠；skill 可能已更新 | Skill 是权威来源 |
| "这个 skill 不适用于这个语言" | Agent 不一定正确判断语言适用性 | 由 skill metadata 决定，Agent 无权判断 |
| "我先完成实现，回头再补" | "回头"永远不会发生 | 强制顺序：test → implement → refactor |

**优先级层级**: 用户指令 > Skill 指令 > Agent 默认行为。Skill 指令可以覆盖 Agent 的默认策略，但用户指令始终优先。

#### 3.12.4 TDD for Skills — 行为契约的迭代验证

借鉴 Superpowers 的 "No skill without a failing test" 规则：

| TDD 阶段 | Skill 等价 |
|----------|-----------|
| **RED** | 运行压力场景：让 Agent 在没有 skill 的情况下执行任务 → 记录 Agent 的失败和"找借口跳过"的具体措辞 |
| **GREEN** | 编写最小 SKILL.md 解决这些具体失败 → 将 Agent 的借口加入 Red Flags 表 → 重新运行验证 |
| **REFACTOR** | 关闭漏洞：收紧语言（"不要将其作为参考" → "你必须执行"），增加更多 Red Flags → 反复测试直到无借口 |

Skill 的回归防护：每次发现新的绕过方式时，更新 Red Flags 表。这是持续进化的行为契约。

#### 3.12.5 Skills 目录与加载

```
.harness/
├── skills/
│   ├── using-superpowers/     # ★ Bootstrap meta-skill (强制执行 Skill 系统)
│   │   └── SKILL.md
│   ├── test-driven-development/
│   │   └── SKILL.md
│   ├── systematic-debugging/
│   │   └── SKILL.md
│   ├── code-review/
│   │   └── SKILL.md
│   └── ... (用户自定义或 marketplace 安装)
```

加载优先级:
1. 内置 skills (`~/.harness/skills/builtin/`) — Harness 自带的 core skills
2. 用户 skills (`~/.harness/skills/`) — 用户自定义
3. 项目 skills (`.harness/skills/`) — 项目级配置

相同名称的 skill，后加载的覆盖先加载的（允许项目级定制）。

### 3.13 多 Agent 编排模式（借鉴 Superpowers Controller + Reviewer）

借鉴 Superpowers 的 Controller-Reviewer 子 Agent 编排模式，Harness 在 SubAgent 系统（Section 3.9）之上增加了结构化的多 Agent 协作模式。

#### 3.13.1 Controller + Implementer + Reviewer 三角模式

```
                    ┌──────────────┐
                    │  Controller  │  (读计划一次, 填充任务列表, 绝不写代码)
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │Implementer│ │Implementer│ │Implementer│  (每个任务: 全新上下文, 完整任务描述)
        │  Task 1  │ │  Task 2  │ │  Task 3  │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
             └────────────┼────────────┘
                          │ (每个 Implementer 完成后)
                          ▼
                   ┌──────────────┐
                   │ Spec Reviewer│  (对照计划验证, 读取实际代码)
                   └──────┬───────┘
                          │
                    pass? │
              ┌───────────┼───────────┐
              ▼                       ▼
         ┌─────────┐           ┌──────────┐
         │ 通过    │           │ 未通过    │
         └────┬────┘           └────┬─────┘
              │                    │
              ▼                    ▼
      ┌──────────────┐    ┌──────────────┐
      │ Code Quality │    │ 回到         │
      │ Reviewer     │    │ Implementer  │
      └──────────────┘    │ (修复差距)    │
                          └──────────────┘
```

**角色严格分离**:

| 角色 | 职责 | 限制 | 模型选择 |
|------|------|------|---------|
| **Controller** | 读计划、创建任务列表、协调顺序、决定何时完成 | **绝不能写代码**。如果代码需要修复，派遣子 Agent | 默认模型 (Sonnet) |
| **Implementer** | 在全新上下文中执行单个任务 | 只接收 Controller 精选的上下文，不继承 Session 历史 | 默认或廉价模型 (按任务复杂度) |
| **Spec Compliance Reviewer** | 对照计划验证实现，读取实际代码验证 | 不检查代码风格，只检查功能是否符合规范 | **始终最强模型** (Opus) |
| **Code Quality Reviewer** | 评估职责分离、分解质量、结构、文件增长 | **仅在 Spec 审查通过后执行** | **始终最强模型** (Opus) |

**模型分层选择**:

| 任务类型 | 模型层 | 示例 |
|---------|--------|------|
| 机械任务 | cheap (Haiku) | 格式化、重命名、简单 CRUD |
| 集成任务 | default (Sonnet) | 跨模块协调、API 设计 |
| 架构/设计/审查 | **expensive (Opus)** | 代码审查、安全审查、架构决策 |

**关键设计决策**:
- **Context isolation**: 每个 Implementer 是全新的 SubAgent，不继承任何 Session 上下文。Controller 精确精选每个 Implementer 需要的信息。
- **Sequential implementation**: 任务逐个顺序执行（避免 Git 冲突）。但 Debug 阶段允许并行 Agent 调查不同文件/根因。
- **Two-stage review**: Spec Compliance 必须在 Code Quality 之前通过 — 功能正确性是前提。
- **Strongest model for review**: 审查始终使用最强模型，因为漏过缺陷的代价远高于审查的 token 成本。

#### 3.13.2 实现者报告协议

每个 Implementer 完成后报告四种状态之一：

| 状态 | 含义 | Controller 行为 |
|------|------|----------------|
| `DONE` | 任务完成，所有测试通过 | → Spec Reviewer |
| `DONE_WITH_CONCERNS` | 完成但有疑虑（如不确定的边界情况） | → Spec Reviewer + 标记关注点 |
| `NEEDS_CONTEXT` | 需要 Controller 提供更多信息 | Controller 查询代码库 → 响应 → 重新派遣 |
| `BLOCKED` | 被依赖阻塞 (如需要先完成 Task 2) | Controller 调整依赖顺序 |

---

## 四、Context 管理与压缩方式

### 4.1 上下文组成模型

每个 turn 完整的上下文由以下部分组成，按 token 消耗排序:

```
完整上下文 (total_tokens = sum of):

  ┌─ System Prompt (~30-40K tokens) ──────────────────┐
  │ SystemPromptPart.STATIC        ~25K  (角色+工具)   │ ← 缓存命中则 0 tokens
  │ SystemPromptPart.REPO_MAP       ~4K  (代码图谱)    │ ← 半缓存
  │ SystemPromptPart.DYNAMIC        ~3K  (git+files)   │ ← 每次刷新
  │ SystemPromptPart.MEMORY         ~2K  (记忆注入)    │ ← 按需
  └────────────────────────────────────────────────────┘

  ┌─ Conversation History (~X tokens) ──────────────────┐
  │ 最近 N turns 的 user/assistant/tool_result 消息      │
  │ 大小可变 — compaction 控制                           │
  └────────────────────────────────────────────────────┘

  ┌─ Tool Results (~Y tokens) ──────────────────────────┐
  │ 当前 turn 的工具调用输出                              │
  │ 大小可变 — 单结果最大 50K chars, 超限截断            │
  └────────────────────────────────────────────────────┘
```

### 4.2 三段式 System Prompt 与 Prompt Cache

```python
# harness/llm/types.py
from enum import Enum

class SystemPromptPart(Enum):
    STATIC = "static"       # 角色定义 + 工具描述 → 缓存
    REPO_MAP = "repo_map"   # 代码图谱树形结构 → 半缓存
    DYNAMIC = "dynamic"     # git status + 文件列表 + 时间 → 不缓存
    MEMORY = "memory"       # 记忆注入 → 不缓存
```

**Anthropic Prompt Cache 协同设计**:

```python
# harness/llm/cache.py
def assemble_system_prompt(parts: list[SystemPromptBlock]) -> tuple[list[dict], list[int]]:
    """
    将 SystemPromptPart 组装为 Anthropic 的 cache_control 格式。

    Returns:
        blocks: [{"type": "text", "text": "...", "cache_control": {...}}, ...]
        breakpoints: [0, 1]  ← 前两个 block 之后的断点索引
    """
    blocks = []
    breakpoints = []
    for i, part in enumerate(parts):
        block = {"type": "text", "text": part.text}
        if part.kind in (SystemPromptPart.STATIC, SystemPromptPart.REPO_MAP):
            block["cache_control"] = {"type": "ephemeral"}
            breakpoints.append(i)
    return blocks, breakpoints
```

**缓存策略**:
- **STATIC**: 角色 + 工具描述 — 只有在工具集变更或升级版本时才失效 → 命中率 95%+
- **REPO_MAP**: 代码图谱 — 在当前 turn 内稳定 (只在用户编辑文件后重建) → 命中率 80%+
- **DYNAMIC**: git status + 时间 — 永不缓存
- **MEMORY**: 记忆注入 — 永不缓存 (随 search query 变化)

### 4.3 多层 Compaction 系统

```python
# harness/core/compaction.py

from enum import Enum
from dataclasses import dataclass, field
import time

class CompactionStrategy(Enum):
    """四种压缩策略, 按激进程度递增"""
    NONE = "none"                        # 不压缩
    MICRO = "micro"                      # 替换旧工具结果为桩
    AUTO = "auto"                        # LLM 摘要旧 turns
    REACTIVE = "reactive"                # 直接截断 (不调 LLM)
    COLLAPSE = "collapse"               # 完整归档到 workspace

@dataclass
class CompactionEngine:
    """
    多层压缩引擎。

    借鉴 Claude Code src/services/compact/ 的四层压缩体系:
    microCompact → autoCompact → reactiveCompact → contextCollapse

    借鉴 IronClaw src/agent/compaction.rs 的三策略 + ContextMonitor:
    MoveToWorkspace → Summarize → Truncate
    """
    llm: "LlmClient"              # 用于摘要的 LLM (cheap model)
    workspace: "Workspace | None"  # 持久化归档
    circuit_breaker: "CircuitBreaker"
    config: "CompactionConfig"

    def evaluate(
        self,
        messages: list["ChatMessage"],
        context_window: int,
        turn_count: int
    ) -> CompactionStrategy:
        """
        根据当前 token 使用率选择压缩策略。

        决策表:
        ┌───────────┬──────────────────┬──────────────────────────────────┐
        │ 使用率     │ 策略              │ 行为                              │
        ├───────────┼──────────────────┼──────────────────────────────────┤
        │ < 60%     │ NONE             │ 健康, 不压缩                       │
        │ 60-65%    │ MICRO            │ 替换 > 5 分钟的旧工具结果为桩        │
        │ 65-85%    │ AUTO             │ LLM 摘要旧 turns, 保留最近 10       │
        │ 85-95%    │ REACTIVE         │ 直接截断保留 5 turns (无 LLM 调用)   │
        │ > 95%     │ COLLAPSE         │ 完整归档到 workspace + 重置上下文     │
        └───────────┴──────────────────┴──────────────────────────────────┘

        断路保护: AUTO compact 连续失败 3 次 → trip → 跳过 AUTO, 直接降级
        """
        tokens = self._estimate_tokens(messages)
        ratio = tokens / context_window

        if ratio < 0.60:
            return CompactionStrategy.NONE

        if ratio < 0.65:
            return CompactionStrategy.MICRO

        if ratio < 0.85:
            if not self.circuit_breaker.can_attempt():
                # AUTO 连续失败 → 降级到 REACTIVE (不调 LLM)
                return CompactionStrategy.REACTIVE
            return CompactionStrategy.AUTO

        if ratio < 0.95:
            return CompactionStrategy.REACTIVE

        return CompactionStrategy.COLLAPSE

    async def compact(
        self,
        thread: "Thread",
        messages: list["ChatMessage"],
        strategy: CompactionStrategy
    ) -> "CompactionResult":
        """执行压缩并返回结果"""
        tokens_before = self._estimate_tokens(messages)

        match strategy:
            case CompactionStrategy.NONE:
                return CompactionResult(messages, tokens_before, tokens_before, False)

            case CompactionStrategy.MICRO:
                return self._micro_compact(messages, tokens_before)

            case CompactionStrategy.AUTO:
                return await self._auto_compact(thread, messages, tokens_before)

            case CompactionStrategy.REACTIVE:
                return self._reactive_compact(messages, tokens_before)

            case CompactionStrategy.COLLAPSE:
                return await self._collapse_compact(thread, messages, tokens_before)

    # ─── MICRO: 时间戳替换 ──────────────────────────────────

    def _micro_compact(
        self, messages: list[ChatMessage], tokens_before: int
    ) -> "CompactionResult":
        """
        将超过 5 分钟的工具结果替换为桩:
        "[Old tool result cleared — file was read 12 minutes ago]"

        只 compact 特定工具类型:
        file_read, shell, grep, glob, web_search, web_fetch
        """
        now = time.time()
        THRESHOLD = 300  # 5 分钟
        COMPACTABLE_TOOLS = {
            "file_read", "shell", "grep", "glob_search",
            "web_search", "web_fetch"
        }
        STUB = "[Old tool result cleared — {} was executed {} ago]"

        compacted = []
        for msg in messages:
            if (msg.role == "user"
                and msg.tool_result
                and msg.tool_result.tool_name in COMPACTABLE_TOOLS
                and (now - msg.tool_result.timestamp) > THRESHOLD):
                # 生成桩
                elapsed = int(now - msg.tool_result.timestamp)
                mins = elapsed // 60
                stub = STUB.format(msg.tool_result.tool_name, f"{mins} minutes")
                compacted.append(ChatMessage.user(stub))
            else:
                compacted.append(msg)

        tokens_after = self._estimate_tokens(compacted)
        return CompactionResult(compacted, tokens_before, tokens_after, False)

    # ─── AUTO: LLM 摘要 ─────────────────────────────────────

    async def _auto_compact(
        self, thread: "Thread", messages: list[ChatMessage], tokens_before: int
    ) -> "CompactionResult":
        """
        使用 cheap LLM 生成旧 turns 的摘要:
        1. 从 messages 中分离出 turns (最近 10 turns 保留)
        2. 将旧 turns 格式化为紧凑的 prompt
        3. 调用 cheap LLM 生成摘要
        4. 摘要写入 workspace daily log
        5. 如果 workspace 写入成功 → 截断 turns
        6. 如果 LLM 调用失败 → 保留所有 turns (不截断!)
        """
        turns = thread.turns
        if len(turns) <= 10:
            return CompactionResult(messages, tokens_before, tokens_before, False)

        old_turns = turns[:-10]  # 保留最近 10
        keep_turns = turns[-10:]

        # 构建 compact prompt
        prompt = self._build_compact_prompt(old_turns)

        try:
            # 调用 cheap LLM (非主模型, 低成本)
            summary = await self.llm.complete(LlmRequest(
                messages=[ChatMessage.user(prompt)],
                max_tokens=2000,
                temperature=0.0
            ))

            # 尝试写入 workspace (用于审计和恢复)
            summary_written = False
            if self.workspace:
                try:
                    date_key = datetime.now().strftime("%Y-%m-%d")
                    await self.workspace.append(
                        f"daily/{date_key}.md",
                        f"\n## Turn Summary ({len(old_turns)} turns)\n\n{summary.text}\n"
                    )
                    summary_written = True
                except Exception as e:
                    logger.warning(f"Workspace write failed: {e}, turns preserved")

            if summary_written:
                # 只有持久化成功后才截断
                thread.turns = keep_turns
                thread.compaction_summary = summary.text
                compacted = [ChatMessage.user(
                    f"[Previous conversation summarized]\n{summary.text}"
                )] + self._turns_to_messages(keep_turns)
                tokens_after = self._estimate_tokens(compacted)
                self.circuit_breaker.reset()
                return CompactionResult(
                    compacted, tokens_before, tokens_after,
                    summary_written=True, summary=summary.text
                )
            else:
                # workspace 写入失败 → 保留完整 turns (安全优先)
                self.circuit_breaker.record_failure()
                return CompactionResult(
                    messages, tokens_before, tokens_before, summary_written=False
                )

        except LlmError:
            # LLM 调用失败 → 保留完整 turns (绝不丢失数据)
            self.circuit_breaker.record_failure()
            return CompactionResult(
                messages, tokens_before, tokens_before, summary_written=False
            )

    # ─── REACTIVE: 直接截断 ─────────────────────────────────

    def _reactive_compact(
        self, messages: list[ChatMessage], tokens_before: int
    ) -> "CompactionResult":
        """
        临界情况 — 不调 LLM, 直接保留最近 5 turns.
        因为此时 context 已近满, 再调 LLM 摘要可能更危险.
        """
        turns = self._messages_to_turns(messages)
        kept = turns[-5:]
        compacted = [ChatMessage.user(
            "[Earlier conversation truncated — context window near limit]"
        )] + self._turns_to_messages(kept)
        tokens_after = self._estimate_tokens(compacted)
        return CompactionResult(compacted, tokens_before, tokens_after, False)

    # ─── COLLAPSE: 归档 + 重置 ──────────────────────────────

    async def _collapse_compact(
        self, thread: "Thread", messages: list[ChatMessage], tokens_before: int
    ) -> "CompactionResult":
        """
        最后的防线 — 完整归档所有 turns 到 workspace, 重置对话上下文.
        用户看到的是几乎全新的对话, 但有之前的摘要.
        """
        all_summary = await self._build_full_summary(thread.turns)
        if self.workspace:
            date_key = datetime.now().strftime("%Y-%m-%d")
            await self.workspace.write(
                f"daily/{date_key}.md",
                all_summary
            )
        thread.turns = []
        continuation = ChatMessage.user(
            f"## Session Continuation\n\n"
            f"The previous conversation was archived to workspace. "
            f"Summary:\n\n{all_summary[:2000]}\n\n"
            f"Continue from where you left off."
        )
        compacted = [continuation]
        tokens_after = self._estimate_tokens(compacted)
        return CompactionResult(
            compacted, tokens_before, tokens_after, summary_written=True,
            summary=all_summary[:2000]
        )
```

### 4.4 断路保护器

```python
# harness/core/circuit_breaker.py

@dataclass
class CircuitBreaker:
    """
    防止无限压缩循环。

    借鉴 Claude Code autoCompact.ts:
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3

    连续 3 次 AUTO compact 失败 → trip → 后续 evaluate() 跳过 AUTO, 直接降级
    """
    max_failures: int = 3
    failure_count: int = 0
    tripped: bool = False

    def can_attempt(self) -> bool:
        return not self.tripped

    def record_failure(self):
        self.failure_count += 1
        if self.failure_count >= self.max_failures:
            self.tripped = True

    def reset(self):
        self.failure_count = 0
        self.tripped = False
```

### 4.5 RepoMap 刷新策略

RepoMap 是上下文总 token 消耗的第二大来源 (仅次于 system prompt STATIC)。刷新策略需要在**时效性**和**延迟**之间权衡。

#### 4.5.1 四种刷新模式

```python
# harness/repomap/refresh.py

from enum import Enum

class RepoMapRefreshMode(Enum):
    MANUAL = "manual"     # 只在显式请求时刷新 (cli: /repomap)
    ALWAYS = "always"     # 每个 turn 都重新构建 (最准确, 最慢)
    FILES = "files"       # 只在聊天文件变更时刷新 (默认)
    AUTO = "auto"         # 智能刷新: 如果上次构建 > 1s 则缓存, 否则每次重建
```

**决策逻辑**:

```python
@dataclass
class RepoMapRefreshPolicy:
    """
    决定是否需要在当前 turn 重新构建 RepoMap。

    触发条件:
    1. mode == ALWAYS → 总是重建
    2. mode == MANUAL → 用户显式 /repomap 命令
    3. mode == FILES:
       - 首次构建 (无缓存)
       - 聊天中的文件被编辑 (mtime 变更)
       - 超过 max_staleness 时间 (默认 60s)
    4. mode == AUTO:
       - 同 FILES
       - 但上次构建耗时 < 1s 时, 即使无变更也重建 (快, 值得保持新鲜)

    不触发条件:
    - 纯对话 turn (无文件操作)
    - 缓存仍然新鲜
    - RepoMap 构建超时 (5s) — 跳过, 使用旧缓存
    """
    mode: RepoMapRefreshMode = RepoMapRefreshMode.FILES
    max_staleness_seconds: float = 60.0
    auto_fast_rebuild_threshold: float = 1.0  # < 1s → 无变更也重建 (AUTO mode)

    def should_refresh(
        self,
        repo_map: "RepoMap | None",
        chat_files: set[str],
        last_build_time: float | None,
        last_build_duration: float | None,
    ) -> bool:
        match self.mode:
            case RepoMapRefreshMode.ALWAYS:
                return True
            case RepoMapRefreshMode.MANUAL:
                return False  # 只有显式命令触发
            case RepoMapRefreshMode.FILES:
                if repo_map is None or last_build_time is None:
                    return True  # 首次
                if self._chat_files_changed(chat_files, last_build_time):
                    return True
                if (time.monotonic() - last_build_time) > self.max_staleness_seconds:
                    return True
                return False
            case RepoMapRefreshMode.AUTO:
                if repo_map is None or last_build_time is None:
                    return True
                if self._chat_files_changed(chat_files, last_build_time):
                    return True
                # 快速构建 (< 1s) → 即使没变更也重建
                if (last_build_duration is not None
                    and last_build_duration < self.auto_fast_rebuild_threshold):
                    return True
                if (time.monotonic() - last_build_time) > self.max_staleness_seconds:
                    return True
                return False

    def _chat_files_changed(self, chat_files: set[str], last_build: float) -> bool:
        """检查聊天文件是否有比 last_build 更新的修改"""
        for f in chat_files:
            try:
                mtime = Path(f).stat().st_mtime
                if mtime > last_build:
                    return True
            except FileNotFoundError:
                return True  # 文件被删除 → 需要刷新
        return False
```

#### 4.5.2 增量 Tag 缓存

```python
# harness/repomap/cache.py

@dataclass
class TagCache:
    """
    基于文件 mtime 的 tree-sitter tag 缓存。

    策略:
    - 每个文件独立缓存: key = (file_path, mtime, parser_language)
    - 缓存介质: diskcache (SQLite-backed, 持久化到 .harness/tags_cache/)
    - 失效条件: 文件 mtime 变化 OR tree-sitter grammar 版本变更
    - Fallback: SQLite 不可用时回退到内存 dict

    借鉴 Aider .aider.tags.cache.v4/ 的缓存模式。
    """
    def __init__(self, cache_dir: str):
        try:
            self._cache = diskcache.Cache(cache_dir)
        except Exception:
            logger.warning("diskcache unavailable, using in-memory tag cache")
            self._cache = {}  # fallback

    def get_tags(
        self, file_path: str, mtime: float, language: str
    ) -> list["Tag"] | None:
        """获取缓存的 tags, 命中返回 list, 未命中返回 None"""
        key = self._make_key(file_path, mtime, language)
        return self._cache.get(key)

    def set_tags(
        self, file_path: str, mtime: float, language: str, tags: list["Tag"]
    ):
        """缓存 tags"""
        key = self._make_key(file_path, mtime, language)
        self._cache.set(key, tags, expire=3600)  # 1 小时 TTL

    def invalidate(self, file_path: str):
        """使某个文件的所有缓存版本失效"""
        # diskcache 没有 prefix delete — 遍历 + 匹配
        pass

    @staticmethod
    def _make_key(file_path: str, mtime: float, language: str) -> str:
        return f"{file_path}|{mtime}|{language}"
```

#### 4.5.3 Token 预算二元搜索

```python
@dataclass
class TokenBudgetOptimizer:
    """
    使用二分搜索确定最优输出大小。

    给定 token budget (如 4000 tokens), 在 RepoMap 树中找到
    能容纳的最大文件/符号数。

    算法:
    1. 按 PageRank 分数降序排列文件
    2. 从 budget // 25 个文件开始尝试
    3. 渲染树 + 估算 token 数 (抽样法, 对大文件高效)
    4. 令牌数 > budget → 减少文件数; < budget * 0.85 → 增加文件数
    5. 迭代直到误差 < 15% 或超过 10 次迭代

    借鉴 Aider repomap.py get_ranked_tags_map() 的 token budget 逻辑。
    """

    def optimize(
        self,
        ranked_files: list[tuple[str, float]],  # (path, pagerank_score)
        renderer: "TreeRenderer",
        token_budget: int,
        max_iterations: int = 10,
        tolerance: float = 0.15,
    ) -> str:
        """二分搜索最优文件子集"""
        if not ranked_files:
            return ""

        low, high = 1, min(len(ranked_files), token_budget // 25)
        best_text = ""
        best_tokens = 0

        for _ in range(max_iterations):
            mid = (low + high) // 2
            if mid < 1:
                break

            subset = ranked_files[:mid]
            text = renderer.render_tree(subset)
            tokens = self._estimate_tokens(text)

            if tokens <= token_budget and tokens > best_tokens:
                best_text = text
                best_tokens = tokens

            error_ratio = abs(tokens - token_budget) / token_budget

            if error_ratio < tolerance:
                break

            if tokens > token_budget:
                high = mid - 1
            else:
                low = mid + 1

        return best_text

    def _estimate_tokens(self, text: str) -> int:
        """快速 token 估算 — 对超过 10K 字符的内容抽样"""
        if len(text) <= 10_000:
            return estimate_tokens(text)  # full count
        # 抽样: 取 5 段 1000 字符的样本, 外推
        sample_size = 1000
        samples = 5
        total = 0
        step = len(text) // samples
        for i in range(samples):
            start = i * step
            chunk = text[start:start + sample_size]
            total += estimate_tokens(chunk)
        return (total / samples) * (len(text) / sample_size)
```

#### 4.5.4 后台构建 + 超时保护

```python
async def build_repo_map_with_timeout(
    repo_map: "RepoMap",
    chat_files: set[str],
    mentioned_files: set[str],
    mentioned_idents: set[str],
    token_budget: int,
    timeout: float = 5.0,
) -> str | None:
    """
    后台构建 RepoMap, 带超时保护。

    宁可缺 map 也不阻塞 turn。

    Returns:
        map_text if successful, None if timeout or error
    """
    try:
        map_text = await asyncio.wait_for(
            asyncio.to_thread(
                repo_map.get_map,
                chat_files=chat_files,
                mentioned_files=mentioned_files,
                mentioned_idents=mentioned_idents,
                token_budget=token_budget,
            ),
            timeout=timeout,
        )
        return map_text
    except asyncio.TimeoutError:
        logger.info(
            f"RepoMap build timed out after {timeout}s, proceeding without map"
        )
        return None
    except Exception as e:
        logger.warning(f"RepoMap build failed: {e}")
        return None
```

---

## 五、Tool 调用方式

### 5.1 工具执行完整管线

```
ToolExecutor.execute(tool_name, params, ctx)
    │
    ├── Step 1: lookup
    │   registry.get(tool_name) → Tool instance
    │
    ├── Step 2: validate
    │   jsonschema.validate(params, tool.input_schema)  ← JSON Schema 验证
    │   + safety.validator.validate_tool_params(params)  ← 递归检查
    │
    ├── Step 3: redact
    │   redact_params(params, tool.sensitive_params)  ← 敏感字段 → [REDACTED]
    │   (日志/历史/SSE 中的参数已脱敏, 但 execute() 收到的是原始值)
    │
    ├── Step 4: approve
    │   requirement = tool.requires_approval(params)  ← NEVER/UNLESS_AUTO/ALWAYS
    │   outcome = policy.authorize(tool.name, requirement, approval_ctx)
    │   ├── Allow → continue
    │   ├── Deny → raise NotAuthorizedError(reason)
    │   └── NeedsApproval → yield PendingApproval → await 用户决策
    │
    ├── Step 5: timeout
    │   asyncio.wait_for(tool.execute(params, ctx), timeout=tool.timeout_seconds)
    │
    ├── Step 6: domain dispatch
    │   match tool.domain:
    │     ORCHESTRATOR → execute in-process (Python 进程内)
    │     CONTAINER → execute via Docker sandbox
    │
    ├── Step 7: execute
    │   output = await tool.execute(params, ctx)
    │
    ├── Step 8: safety scan
    │   result = safety.scan_tool_output(output.content, tool.name)
    │   ├── Pass(text)     → wrap_for_llm(text, tool.name)
    │   ├── Flagged(text)  → wrap_for_llm(text, tool.name) + log 告警
    │   └── Blocked(reason) → raise NotAuthorizedError(reason)
    │
    └── Step 9: return
        return ToolOutput(content=wrapped, risk_level=..., duration=..., truncated=...)
```

### 5.2 ToolExecutor 实现

```python
# harness/tools/executor.py

class ToolExecutor:
    """
    工具执行管线——所有工具调用 (Agent/CLI/Gateway) 必经此路径。

    借鉴 IronClaw 的 "Everything Goes Through Tools" 原则:
    - 统一的审计轨迹 (ActionRecord)
    - 统一的安全管线
    - channel-agnostic (新 channel 自动继承全部保护)
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        safety: "SafetyLayer",
        policy: "PermissionPolicy",
    ):
        self.registry = registry
        self.safety = safety
        self.policy = policy

    async def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        ctx: "ToolContext",
        approval_ctx: "ApprovalContext | None" = None
    ) -> "ToolOutput":
        """完整的执行管线"""
        # Step 1: Lookup
        tool = self.registry.get(tool_name)
        if not tool:
            raise NotFoundError(f"tool '{tool_name}' not found in registry")

        # Step 2: Validate params against JSON Schema
        try:
            jsonschema.validate(params, tool.input_schema)
        except jsonschema.ValidationError as e:
            raise InvalidParametersError(str(e))

        # Step 2b: Safety validator (recursive JSON string check)
        self.safety.validator.validate_tool_params(params, max_depth=32)

        # Step 3: Redact sensitive params for logging
        safe_params = redact_params(params, tool.sensitive_params)
        logger.debug(f"executing {tool_name}: {json.dumps(safe_params)}")

        # Step 4: Permission check
        requirement = tool.requires_approval(params)
        approval = approval_ctx or ApprovalContext.autonomous()
        outcome = self.policy.authorize(tool_name, requirement, approval)
        match outcome:
            case PermissionOutcome.DENY:
                raise NotAuthorizedError(outcome.reason)
            case PermissionOutcome.NEEDS_APPROVAL:
                # 返回给上层 (AgenticLoop/DirectHandler) 处理审批
                raise ApprovalRequiredError(tool_name, params, outcome.reason)

        # Step 5: Domain dispatch + execute
        start = time.monotonic()
        try:
            match tool.domain:
                case ToolDomain.ORCHESTRATOR:
                    output = await asyncio.wait_for(
                        tool.execute(params, ctx),
                        timeout=tool.timeout_seconds
                    )
                case ToolDomain.CONTAINER:
                    output = await self._execute_in_container(tool, params, ctx)
        except asyncio.TimeoutError:
            raise TimeoutError(tool.timeout_seconds)

        duration = time.monotonic() - start

        # Step 6: Safety scan output
        if tool.requires_sanitization:
            result = self.safety.scan_tool_output(output.content, tool_name)
            match result:
                case SafetyResult.BLOCKED:
                    raise NotAuthorizedError(f"tool output blocked: {result.reason}")
                case SafetyResult.PASS(text):
                    output.content = text
                case SafetyResult.FLAGGED(text):
                    output.content = text
                    logger.warning(f"safety flagged {tool_name}: {result.findings}")

        output.duration_secs = duration
        return output
```

### 5.3 Domain 路由与 Docker 沙箱架构

Harness 的沙箱策略借鉴 **OpenSandbox** (alibaba/OpenSandbox, CNCF Landscape) 的 defense-in-depth 模式。选择 Docker 而非 WASM 作为主要沙箱，原因如下：

| 需求 | WASM/WASI | Docker |
|------|-----------|--------|
| `pip install` / `npm install` | 不支持 (无原生包管理器) | ✓ 完整 Linux 环境 |
| 任意子进程 (`bash -c`, `node spawn`) | 不支持 (无 fork/exec) | ✓ |
| Python C 扩展 (numpy, pandas) | Pyodide only (不兼容) | ✓ |
| POSIX 文件系统语义 | 有限 (无真实 FS) | ✓ |
| Shell 交互 | 无 | ✓ |
| 隔离强度 | 强 (进程内沙箱) | 强 (cgroups + namespace + 可选 MicroVM) |
| 冷启动延迟 | ~1ms | Docker: ~500ms; MicroVM: ~125ms |
| 适用场景 | 纯计算 (JSON 处理等) | AI 编程 Agent (需要完整工具链) |

**结论**: AI 编程 Agent 需要 `pip install pandas`、`node child_process.spawn()` 和任意 shell 命令，WASM 从根本上无法支持。Docker 提供完整 POSIX 环境，通过 defense-in-depth 达到与 WASM 相当的安全级别。

#### 5.3.1 Runtime Factory 模式

Harness 通过 **SandboxRuntime ABC** 抽象沙箱后端，支持在配置中切换：

```
harness.toml 配置:
[sandbox]
runtime = "docker"       # 默认 — Docker 容器
# runtime = "firecracker"  # 可选 — MicroVM (需 KVM)
# runtime = "kata"         # 可选 — Kata Containers
```

所有沙箱后端实现相同的 `SandboxRuntime` 接口（创建、执行、暂停、恢复、销毁），上层 `ToolExecutor` 不感知具体后端。

#### 5.3.2 Sidecar 注入架构（借鉴 OpenSandbox execd + egress）

每个 Harness 沙箱容器注入两个 sidecar：

```
┌─────────────────────────────────────────────────────────┐
│                  SANDBOX CONTAINER                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  execd sidecar (port 44772)                       │   │
│  │  ────────────                                     │   │
│  │  • 进程内 HTTP API                                │   │
│  │  • 端点: /ping, /code, /command, /files, /pty    │   │
│  │  • 认证: X-EXECD-ACCESS-TOKEN header              │   │
│  │  • 流式输出: SSE (Server-Sent Events)              │   │
│  │  • 实时指标: GET /metrics/watch (SSE)              │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  egress sidecar (port 18080)                      │   │
│  │  ──────────────                                   │   │
│  │  • 共享容器网络命名空间                             │   │
│  │  • DNS 过滤: iptables redirect port 53 → 127.0.0.1:15353│
│  │  • IP 过滤: nftables 动态规则 (可选全锁模式)        │   │
│  │  • 策略端点: GET/PATCH /policy                     │   │
│  │  • 阻断 DoH (port 443) + DoT (port 853) bypass     │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  用户进程 (非 root, UID 1000)                            │
│  ──────────────────────────                              │
│  • 通过 execd API 执行所有操作                            │
│  • 无直接网络访问 (由 egress sidecar 控制)                │
│  • 无根文件系统访问 (read-only rootfs + tmpfs /tmp)       │
└─────────────────────────────────────────────────────────┘
```

**execd sidecar 职责**:
- 代码执行 (`/code`) — 支持上下文持久化 (跨调用保留变量)
- Shell 命令 (`/command`) — 前台/后台，SSE 流式输出
- 交互式 PTY (`/pty`) — WebSocket 双向通信
- 文件系统操作 (`/files`) — upload/download/search(glob)/mv/replace/permissions/delete
- 实时指标 (`/metrics/watch`) — CPU/内存/磁盘 IO 的 SSE 流

**egress sidecar 职责**:
- DNS 过滤模式（默认）: iptables 将所有 DNS 流量重定向到代理，按 JSON 策略允许/拒绝域名
- DNS+nftables 全锁模式: 额外使用 nftables 实施 IP 级出站规则，动态解析允许域名的 IP 并自动更新规则
- 策略格式: `{"defaultAction": "deny", "egress": [{"action": "allow", "target": "*.pypi.org"}, ...]}`
- 运行时策略更新: PATCH `/policy` 无需重启容器

#### 5.3.3 三层防御体系

| 层级 | 技术 | 防护目标 |
|------|------|---------|
| **Layer 1: 容器加固** | `--cap-drop=ALL` + 选择性 `--cap-add`; `--security-opt no-new-privileges`; `--read-only` rootfs + `--tmpfs /tmp`; seccomp profile (syscall 白名单); AppArmor profile (强制访问控制); 非 root 用户 (UID 1000); `--pids-limit 4096` (防 fork bomb); `--ulimit` CPU/内存/文件描述符/核心转储 | 通用容器逃逸、权限提升 |
| **Layer 2: 网络隔离** | egress sidecar: DNS 过滤 + nftables IP 白名单; 默认 `network=none`, 按需开启 `network=bridge` + egress 策略; 阻断 DoH (443) / DoT (853) 绕过 | 数据外泄、恶意下载、SSRF |
| **Layer 3: 硬件隔离 (可选)** | MicroVM 运行时: Firecracker (~125ms 启动, ~5MB 内存) / Kata Containers (~150-500ms); Kubernetes RuntimeClass 动态切换; 按工具或 workload 类型配置 | 不可信/对抗性 workload |

**Layer 3 运行时选择**:

| 运行时 | 隔离类型 | 启动延迟 | 额外内存 | 需要 KVM |
|--------|---------|---------|---------|----------|
| `runc` (默认) | cgroups/namespaces | < 50ms | ~5MB | 否 |
| gVisor | 用户态内核 (syscall 拦截) | +10-50ms | ~30-50MB | 否 |
| Firecracker | MicroVM (硬件虚拟化) | ~125ms | ~5MB | 是 |
| Kata (QEMU) | 完整 VM | ~500ms | ~20-50MB | 是 |
| Kata (Firecracker) | MicroVM via Kata | ~150ms | ~5-10MB | 是 |

配置驱动选择：`[sandbox] runtime = "firecracker"` 或按工具类型指定：
```
[sandbox.runtime_per_tool]
bash_exec = "firecracker"  # 对抗性 workload → 硬件隔离
file_read = "runc"         # 低风险 → 默认容器
```

#### 5.3.4 容器生命周期管理

借鉴 OpenSandbox 的异步生命周期：

```
Pending → Running ↔ Paused
  |         |
  v         v
Stopping → Terminated / Failed
```

- **创建**: `POST /v1/sandboxes` → 202 Accepted（异步供应，非阻塞）
- **自动过期**: TTL 机制，`/renew-expiration` 续期
- **暂停/恢复**: 保持沙箱 ID 不变 — Docker: `container pause`；Kubernetes: rootfs 快照 → OCI 镜像推送
- **ToolExecutor 集成**: `get_or_create_container()` 实现容器池复用 — 相同配置的工具调用复用同一容器实例，减少冷启动开销

#### 5.3.5 Domain 路由决策

```
ToolExecutor.execute(tool_name, params, ctx)
    │
    ├── ToolDomain.ORCHESTRATOR → 进程内直接执行
    │   └── 适用: file_read, file_write, glob_search, grep_search,
    │              web_fetch, web_search, memory_*, task_*, ask_user_question
    │   └── 安全: Python 进程级别，无沙箱（这些工具本身就是受控 API 调用）
    │
    └── ToolDomain.CONTAINER → Docker 沙箱执行
        └── 适用: bash_exec, 以及任何标记为 container 的自定义工具
        └── 安全: 三层防御体系（容器加固 + 网络隔离 + 可选硬件隔离）
```

**关键设计决策**: 只读工具（file_read、grep_search 等）不走沙箱 — 它们在 Harness 进程内直接执行，因为：
- 它们本身就是受控的 API 调用，不执行用户代码
- 跳过沙箱避免了 Docker 的冷启动延迟（~500ms → ~0ms）
- 安全性通过 SafetyLayer（路径验证、注入检测、密钥检测）保证
- 只有执行**任意用户代码**的工具（bash_exec）需要沙箱隔离

这与 OpenSandbox 的理念一致：不是所有操作都需要沙箱，只有不可信代码执行才需要。


### 5.4 工具结果进入 Agentic Loop

```python
# 在 AgenticLoop.run() 中:
# 工具执行后, 结果以 ChatMessage.tool_result 形式推入消息数组

for tool_call in response.tool_calls:
    try:
        output = await tool_executor.execute(
            tool_call.name, tool_call.input, ctx, approval_ctx
        )
        # 结果包装为 tool_result 消息 → 下一轮 API call 会包含它
        ctx.messages.append(ChatMessage(
            role="user",
            content=[ContentBlock.tool_result(
                tool_use_id=tool_call.id,
                content=output.content,        # ← 已经 safety scan + wrap_for_llm
                is_error=False
            )]
        ))
    except ToolError as e:
        # 错误也作为 tool_result 返回给模型 (让它自己处理)
        ctx.messages.append(ChatMessage(
            role="user",
            content=[ContentBlock.tool_result(
                tool_use_id=tool_call.id,
                content=f"Error: {e}",
                is_error=True
            )]
        ))
```

### 5.5 默认工具完整规范 (15 个内置工具)

Harness 内置 15 个核心工具, 覆盖文件操作、搜索、执行、网络、记忆和任务管理。每个工具的完整规范见下。

#### 工具总览

| # | 工具名 | Domain | 权限级别 | 只读 | 超时 | 敏感参数 |
|---|--------|--------|---------|------|------|---------|
| 1 | `file_read` | Orchestrator | NEVER | ✓ | 10s | — |
| 2 | `file_write` | Orchestrator | UNLESS_AUTO | ✗ | 30s | content |
| 3 | `file_edit` | Orchestrator | UNLESS_AUTO | ✗ | 30s | content |
| 4 | `glob_search` | Orchestrator | NEVER | ✓ | 15s | — |
| 5 | `grep_search` | Orchestrator | NEVER | ✓ | 15s | — |
| 6 | `bash_exec` | Container | ALWAYS | ✗ | 120s | command |
| 7 | `web_fetch` | Orchestrator | NEVER | ✓ | 30s | url |
| 8 | `web_search` | Orchestrator | NEVER | ✓ | 20s | query |
| 9 | `memory_write` | Orchestrator | NEVER | ✗ | 5s | content |
| 10 | `memory_read` | Orchestrator | NEVER | ✓ | 5s | — |
| 11 | `memory_delete` | Orchestrator | UNLESS_AUTO | ✗ | 5s | — |
| 12 | `task_create` | Orchestrator | NEVER | ✗ | 5s | — |
| 13 | `task_update` | Orchestrator | NEVER | ✗ | 5s | — |
| 14 | `task_list` | Orchestrator | NEVER | ✓ | 5s | — |
| 15 | `ask_user_question` | Orchestrator | NEVER | ✗ | 300s | — |

#### 5.5.1 file_read — 文件读取

```python
# harness/tools/builtin/file_read.py

class FileReadTool(Tool):
    """
    读取文件内容。支持行偏移和行数限制, 用于读取大文件的关键片段。

    安全规则:
    - 最大读取 10MB
    - 禁止读取 /etc/passwd、/etc/shadow、~/.ssh/、~/.aws/credentials
    - 二进制文件返回 hexdump (前 512 字节)
    - 路径规范化防止目录穿越
    """
    name = "file_read"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 10
    sensitive_params = set()

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read"
                },
                "offset": {
                    "type": "integer", "minimum": 0,
                    "description": "Line number to start reading from (0-indexed)"
                },
                "limit": {
                    "type": "integer", "minimum": 1, "maximum": 2000,
                    "description": "Maximum number of lines to read (default: 2000)"
                },
                "pages": {
                    "type": "string",
                    "description": "Page range for PDF files (e.g. '1-5', '3'). Max 20 pages."
                },
            },
            "required": ["file_path"]
        }

    def requires_approval(self, params: dict) -> ApprovalRequirement:
        return ApprovalRequirement.NEVER

    @property
    def is_read_only(self) -> bool:
        return True
```

#### 5.5.2 file_write — 文件写入

```python
class FileWriteTool(Tool):
    """
    写入或覆写文件。自动创建父目录。

    安全规则:
    - overwrite 模式下, 写入前先备份到 .harness/backups/
    - 路径在 workspace 外时要求审批
    - 写入后触发 RepoMap 缓存失效
    """
    name = "file_write"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 30
    sensitive_params = {"content"}

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file"
                },
            },
            "required": ["file_path", "content"]
        }

    def requires_approval(self, params: dict) -> ApprovalRequirement:
        path = Path(params["file_path"])
        if not str(path.resolve()).startswith(str(self._workspace_root())):
            return ApprovalRequirement.ALWAYS  # 工作区外的写入需要审批
        return ApprovalRequirement.UNLESS_AUTO

    async def execute(self, params: dict, ctx: "ToolContext") -> ToolOutput:
        path = Path(params["file_path"])
        # 备份 (如果文件已存在)
        if path.exists():
            backup_dir = Path(ctx.cwd) / ".harness" / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{path.name}.{int(time.time())}"
            shutil.copy2(path, backup_path)

        # 创建父目录
        path.parent.mkdir(parents=True, exist_ok=True)

        # 写入
        path.write_text(params["content"], encoding="utf-8")

        # 失效 RepoMap 缓存
        ctx.repo_map.invalidate_file(str(path))

        return ToolOutput(
            content=f"File written: {path} ({len(params['content'])} bytes)",
            risk_level="low",
        )

    @property
    def is_read_only(self) -> bool:
        return False
```

#### 5.5.3 file_edit — 精确字符串替换

```python
class FileEditTool(Tool):
    """
    基于精确字符串匹配的文件编辑。

    使用 `old_string` → `new_string` 替换模式。必须精确匹配
    (包含缩进), 且 old_string 在文件中唯一。支持 replace_all。

    安全规则:
    - 编辑前验证 old_string 在文件中的唯一性 (replace_all=False 时)
    - 自动创建备份
    - 编辑后触发 RepoMap 缓存失效
    """
    name = "file_edit"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 30
    sensitive_params = {"new_string"}

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit"
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace. Must match exactly including whitespace."
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace it with (must differ from old_string)"
                },
                "replace_all": {
                    "type": "boolean", "default": False,
                    "description": "Replace all occurrences instead of just the first"
                },
            },
            "required": ["file_path", "old_string", "new_string"]
        }

    async def execute(self, params: dict, ctx: "ToolContext") -> ToolOutput:
        path = Path(params["file_path"])
        content = path.read_text()
        old = params["old_string"]

        occurrences = content.count(old)
        if occurrences == 0:
            raise ToolError(f"old_string not found in {path}")
        if not params.get("replace_all") and occurrences > 1:
            raise ToolError(
                f"old_string found {occurrences} times in {path}. "
                f"Use replace_all=true or make old_string more specific."
            )

        new_content = content.replace(old, params["new_string"])
        path.write_text(new_content)

        return ToolOutput(
            content=f"Edit applied to {path}: {occurrences} replacement(s)",
            risk_level="low",
        )

    @property
    def is_read_only(self) -> bool:
        return False
```

#### 5.5.4 glob_search — 文件模式匹配

```python
class GlobSearchTool(Tool):
    """
    快速文件模式匹配。支持 glob 模式 (如 `**/*.py`, `src/**/*.ts`)。

    按修改时间排序, 返回匹配路径列表。
    """
    name = "glob_search"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 15

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g. '**/*.py', 'src/**/*.ts')"
                },
                "path": {
                    "type": "string",
                    "description": "Search root directory (default: workspace root)"
                },
            },
            "required": ["pattern"]
        }

    async def execute(self, params: dict, ctx: "ToolContext") -> ToolOutput:
        root = Path(params.get("path", ctx.cwd))
        matches = sorted(root.glob(params["pattern"]), key=lambda p: p.stat().st_mtime, reverse=True)
        lines = [str(m.relative_to(root)) for m in matches[:500]]
        return ToolOutput(content="\n".join(lines), risk_level="low")

    @property
    def is_read_only(self) -> bool:
        return True
```

#### 5.5.5 grep_search — 正则内容搜索

```python
class GrepSearchTool(Tool):
    """
    基于 ripgrep 的源码内容搜索。支持正则、文件类型过滤、大小写不敏感。

    输出模式:
    - files_with_matches: 只返回文件路径 (默认)
    - content: 返回匹配行 (+ line numbers)
    - count: 返回匹配计数
    """
    name = "grep_search"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 15

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for"
                },
                "path": {"type": "string", "description": "Search root directory"},
                "glob": {"type": "string", "description": "File filter glob (e.g. '*.py')"},
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "default": "files_with_matches"
                },
                "-i": {"type": "boolean", "default": False, "description": "Case insensitive"},
                "-n": {"type": "boolean", "default": True, "description": "Show line numbers"},
                "head_limit": {"type": "integer", "default": 250, "description": "Max output lines"},
            },
            "required": ["pattern"]
        }

    @property
    def is_read_only(self) -> bool:
        return True
```

#### 5.5.6 bash_exec — Shell 命令执行

```python
class BashExecTool(Tool):
    """
    在 Docker 沙箱中执行 Shell 命令。

    安全规则:
    - 必须在 Container domain (Docker 沙箱)
    - 默认网络 = none (除非工具指定需要网络)
    - 非 root 用户
    - 只读 rootfs
    - 最大输出 64KB (截断)
    - 总是需要审批 (ALWAYS)
    """
    name = "bash_exec"
    domain = ToolDomain.CONTAINER
    timeout_seconds = 120
    sensitive_params = {"command"}

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "timeout": {
                    "type": "integer", "minimum": 0, "maximum": 600000,
                    "default": 120000,
                    "description": "Timeout in milliseconds (max 600000)"
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory within the container"
                },
                "env": {
                    "type": "object",
                    "description": "Additional environment variables",
                    "additionalProperties": {"type": "string"}
                },
            },
            "required": ["command"]
        }

    def requires_approval(self, params: dict) -> ApprovalRequirement:
        return ApprovalRequirement.ALWAYS

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return True  # 容器内可写, 需要谨慎
```

#### 5.5.7 web_fetch — URL 内容获取

```python
class WebFetchTool(Tool):
    """
    获取 URL 内容并转换为 Markdown。

    - HTTP 自动升级为 HTTPS
    - 跨主机重定向返回目标 URL (不自动跟随)
    - 结果缓存 15 分钟
    - 禁止访问内网地址 (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    """
    name = "web_fetch"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 30
    sensitive_params = {"url"}

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri", "description": "URL to fetch"},
                "prompt": {
                    "type": "string",
                    "description": "Prompt to run against fetched content for targeted extraction"
                },
            },
            "required": ["url", "prompt"]
        }

    @property
    def is_read_only(self) -> bool:
        return True
```

#### 5.5.8 web_search — 网络搜索

```python
class WebSearchTool(Tool):
    """
    执行网络搜索, 返回带标题和 URL 的结果块。

    - 使用可配置的搜索后端 (默认: 内置 API)
    - 支持域名白名单/黑名单过滤
    - 结果缓存 15 分钟
    """
    name = "web_search"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 20
    sensitive_params = {"query"}

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2, "description": "Search query"},
                "allowed_domains": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Restrict results to these domains"
                },
                "blocked_domains": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Exclude these domains from results"
                },
            },
            "required": ["query"]
        }

    @property
    def is_read_only(self) -> bool:
        return True
```

#### 5.5.9-11 Memory 工具

```python
class MemoryWriteTool(Tool):
    """参见 Section 6.2 MemoryWriter — 写入持久记忆"""
    name = "memory_write"
    # ... (已在 Section 6.2 详述)

class MemoryReadTool(Tool):
    """查询持久记忆 — FTS5 + 语义搜索 → RRF 融合"""
    name = "memory_read"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 5

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for memory retrieval"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "preference", "project", "feedback", "reference"],
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
            },
            "required": ["query"]
        }

class MemoryDeleteTool(Tool):
    """删除持久记忆条目"""
    name = "memory_delete"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 5

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "UUID of the memory to delete"},
            },
            "required": ["memory_id"]
        }

    def requires_approval(self, params: dict) -> ApprovalRequirement:
        return ApprovalRequirement.UNLESS_AUTO
```

#### 5.5.12-14 Task 管理工具

```python
class TaskCreateTool(Tool):
    """
    创建结构化任务用于跟踪复杂多步工作。

    借鉴 Claude Code TaskCreate + TodoWrite 工具:
    - 用 tasks 跟踪进度, 组织复杂任务
    - 每个 task 有: subject, description, status, dependencies
    - 创建后自动排序 (先完成依赖)
    """
    name = "task_create"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 5

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Brief actionable title"},
                "description": {"type": "string", "description": "What needs to be done"},
                "activeForm": {
                    "type": "string",
                    "description": "Present continuous form for progress display (e.g. 'Running tests')"
                },
                "metadata": {
                    "type": "object",
                    "description": "Arbitrary metadata to attach to the task"
                },
            },
            "required": ["subject", "description"]
        }

class TaskUpdateTool(Tool):
    """更新任务状态: pending → in_progress → completed; 支持依赖管理"""
    name = "task_update"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 5

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "taskId": {"type": "string", "description": "Task ID to update"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"]
                },
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "addBlocks": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Task IDs that this task blocks"
                },
                "addBlockedBy": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Task IDs that block this task"
                },
            },
            "required": ["taskId"]
        }

class TaskListTool(Tool):
    """列出所有任务及其状态和依赖关系"""
    name = "task_list"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 5

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def is_read_only(self) -> bool:
        return True
```

#### 5.5.15 ask_user_question — 用户交互

```python
class AskUserQuestionTool(Tool):
    """
    向用户提问 — 当 Agent 遇到需要用户决策的问题时使用。

    支持单选和多选, 选项可以有 preview (用于 UI 对比选择)。

    使用条件 (满足任一即触发):
    - 多种可行方案 (无法从代码/上下文中确定最优)
    - 需要用户偏好的决策
    - 涉及用户数据的操作
    """
    name = "ask_user_question"
    domain = ToolDomain.ORCHESTRATOR
    timeout_seconds = 300  # 等待用户输入

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1, "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "header": {"type": "string", "maxLength": 12},
                            "options": {
                                "type": "array", "minItems": 2, "maxItems": 4,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                        "preview": {"type": "string"},
                                    },
                                    "required": ["label", "description"]
                                }
                            },
                            "multiSelect": {"type": "boolean", "default": False},
                        },
                        "required": ["question", "header", "options", "multiSelect"]
                    }
                },
            },
            "required": ["questions"]
        }

    def requires_approval(self, params: dict) -> ApprovalRequirement:
        return ApprovalRequirement.NEVER  # 工具本身就是审批机制

    @property
    def is_read_only(self) -> bool:
        return True

    def requires_user_interaction(self) -> bool:
        return True
```

### 5.6 Tool 注册表与 Pool 组装

```python
# harness/tools/registry.py

class ToolRegistry:
    """
    工具注册表 — 管理所有已知工具的注册、查询、启用/禁用。

    借鉴 Claude Code assembleToolPool:
    - 内置工具 (builtins) 注册为连续前缀 (保证 cache 稳定性)
    - MCP 工具追加在后
    - 每个分区内按名称字母排序
    - 会话级 toolSchemaCache: 防止 mid-session schema 变化
      (如 GrowthBook flag flip) 破坏 prompt cache

    借鉴 IronClaw ToolDispatcher:
    - register() 时验证 JSON Schema 合法性
    - get() 返回 Tool 实例 (或 None)
    - all_tools() 返回当前启用的工具列表
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._builtin_names: list[str] = []
        self._mcp_names: list[str] = []
        # 会话级 schema 缓存 — 防止运行时 schema 变化破坏 prompt cache
        self._schema_cache: dict[str, dict] = {}
        self._sorted_cache: list[Tool] | None = None

    def register(self, tool: Tool, source: Literal["builtin", "mcp", "plugin"] = "builtin"):
        """注册工具 — 验证 + 存储"""
        # JSON Schema 验证
        jsonschema.Draft202012Validator.check_schema(tool.input_schema)

        if tool.name in self._tools:
            # 内置工具优先于 MCP 工具
            existing = self._tools[tool.name]
            if source == "builtin" and existing._source == "mcp":
                logger.info(f"Builtin '{tool.name}' shadows MCP tool with same name")
            else:
                logger.warning(f"Tool '{tool.name}' already registered, skipping")
                return

        tool._source = source
        self._tools[tool.name] = tool
        if source == "builtin":
            self._builtin_names.append(tool.name)
        elif source == "mcp":
            self._mcp_names.append(tool.name)

        # 缓存失效
        self._sorted_cache = None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def unregister(self, name: str):
        """移除工具 (MCP server 断开时)"""
        self._tools.pop(name, None)
        if name in self._builtin_names:
            self._builtin_names.remove(name)
        if name in self._mcp_names:
            self._mcp_names.remove(name)
        self._sorted_cache = None
        self._schema_cache.pop(name, None)

    def all_tools(self, enabled_only: bool = True) -> list[Tool]:
        """
        返回工具列表 — 稳定排序以保证 prompt cache 稳定性。

        顺序:
        1. 内置工具 (字母排序)
        2. MCP 工具 (字母排序)
        3. 插件工具 (字母排序)

        每个分区内的字母排序保证:
        - 新工具追加不会打乱已有工具的 position
        - Anthropic prompt cache 前缀匹配仍然有效
        """
        if self._sorted_cache is not None:
            return [
                t for t in self._sorted_cache
                if not enabled_only or t.is_enabled()
            ]

        builtins = sorted(
            [self._tools[n] for n in self._builtin_names if n in self._tools],
            key=lambda t: t.name,
        )
        mcps = sorted(
            [self._tools[n] for n in self._mcp_names if n in self._tools],
            key=lambda t: t.name,
        )
        self._sorted_cache = builtins + mcps
        return [t for t in self._sorted_cache if not enabled_only or t.is_enabled()]

    def get_schemas(self) -> list[dict]:
        """
        返回所有工具 schema (用于 LLM API request)。

        使用会话级缓存 — 同一 session 内 always 返回相同的 schema 对象,
        防止 mid-session GrowthBook flag flip 等运行时变更破坏
        Anthropic 的 prompt cache。

        Cache key: tool.name (会话生命周期内不变)
        """
        schemas = []
        for tool in self.all_tools():
            if tool.name not in self._schema_cache:
                self._schema_cache[tool.name] = tool_to_api_schema(tool)
            schemas.append(self._schema_cache[tool.name])
        return schemas

    def filter_by_mode(self, mode: str) -> list[Tool]:
        """
        根据 Agent 模式过滤工具。

        - simple: 只读工具 + ask_user_question
        - repl: 所有非 destructive 工具
        - full: 全部工具
        - coordinator: Agent tool + TaskStop + SendMessage
        """
        match mode:
            case "simple":
                return [t for t in self.all_tools() if t.is_read_only]
            case "repl":
                return [t for t in self.all_tools() if not getattr(t, 'is_destructive', lambda: False)()]
            case "coordinator":
                return [t for t in self.all_tools() if t.name in {"agent", "task_stop", "send_message"}]
            case "full":
                return self.all_tools()

    def filter_by_deny_rules(self, permission_context: "ApprovalContext") -> list[Tool]:
        """根据权限规则移除被禁止的工具 (在发送给 LLM 之前过滤)"""
        return [
            t for t in self.all_tools()
            if not permission_context.is_tool_blocked(t.name)
        ]

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.all_tools()]


def tool_to_api_schema(tool: Tool) -> dict:
    """
    将 Tool 转换为 LLM API 接受的 tool schema 格式。

    Anthropic API 格式:
    {
        "name": "file_read",
        "description": "...",
        "input_schema": { ... JSON Schema ... }
    }

    OpenAI API 格式 (同 JSON Schema, 包装在 function 对象中):
    {
        "type": "function",
        "function": { "name": "...", "description": "...", "parameters": { ... } }
    }
    """
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }
```

### 5.7 MCP 桥接协议

MCP (Model Context Protocol) 允许动态加载第三方工具和资源。Harness 的 MCP 层负责:

```python
# harness/tools/mcp/client_manager.py

@dataclass
class McpServerConfig:
    """MCP server 配置"""
    name: str                          # 逻辑名称 (用于工具命名前缀)
    transport: Literal["stdio", "sse"]  # 传输方式
    command: str | None = None          # stdio: 可执行文件路径
    args: list[str] = field(default_factory=list)  # stdio: 命令行参数
    url: str | None = None              # sse: HTTP endpoint URL
    env: dict[str, str] = field(default_factory=dict)  # 环境变量
    auto_approve: list[str] = field(default_factory=list)  # 自动审批的工具列表
    timeout_ms: int = 30_000

class McpClientManager:
    """
    管理 MCP server 的生命周期: 连接 → 发现 → 注册 → 心跳 → 断开。

    借鉴 Claude Code src/services/mcp/client.ts:
    - 每个 MCP server 对应一个 McpClientManager 实例
    - 通过 MCP SDK 的 ClientSession + StdioServerParameters 交互
    - tools/list → 发现工具 → 注册到 ToolRegistry

    借鉴 IronClaw src/tools/mcp/:
    - 工具命名: mcp__<server_name>__<tool_name> 防止冲突
    - 凭证注入: MCP 工具的 HTTP 请求通过 host 凭证代理
    - 心跳检测: 定期 ping, 超时自动重连
    """
```

**MCP 工具生命周期**:

```
Server 启动 (stdio subprocess 或 SSE 连接)
    │
    ├── 1. initialize → 协商协议版本 + 能力 (tools, resources, prompts)
    │
    ├── 2. tools/list → 获取所有工具定义
    │       │
    │       └── 每个工具: {name, description, inputSchema (JSON Schema)}
    │           注册为: mcp__<server>__<tool_name>
    │                     │
    │                     ├── inputJSONSchema (来自 MCP server, 不转换)
    │                     ├── call: 通过 MCP tools/call 执行
    │                     └── isMcp: True (标记, 用于 UI 分组)
    │
    ├── 3. resources/list → 获取资源模板 (可选)
    │       └── mcp__<server>__<resource_name> 资源读取工具
    │
    ├── 4. 心跳 — 定期 ping (默认 30s)
    │       ├── pong: 连接正常
    │       └── timeout 3 次连续: 标记 disconnected → 尝试重连
    │
    └── 5. 断开 — Session 结束时发送 shutdown
            ├── 清理注册的工具
            └── 终止子进程 (stdio) 或关闭连接 (SSE)
```

**MCP 命名规范**: `mcp__<server_name>__<tool_name>`

| 来源 | 示例 | 说明 |
|------|------|------|
| stdio MCP server | `mcp__filesystem__read_file` | server_name = "filesystem" |
| SSE MCP server | `mcp__github__list_issues` | server_name = "github" |
| 冲突处理 | 内置工具覆盖 MCP 同名工具 | 内置 file_read > mcp__xxx__file_read |

**MCP 工具包装器**:

```python
class McpToolWrapper(Tool):
    """
    包装 MCP server 提供的工具, 使其遵循 Tool ABC。

    关键适配:
    - inputJSONSchema → input_schema (直接传递, 不转换)
    - execute → MCP tools/call (带上 server_name + tool_name)
    - is_enabled → server 连接状态
    - is_read_only → 从 MCP tool annotations 推断
    """

    def __init__(self, mcp_tool: "McpToolDef", server: "McpClientManager"):
        self._mcp_tool = mcp_tool
        self._server = server
        self.name = f"mcp__{server.name}__{mcp_tool.name}"
        self.description = mcp_tool.description or f"MCP tool: {mcp_tool.name}"
        self._input_schema = mcp_tool.inputSchema

    @property
    def input_schema(self) -> dict:
        return self._input_schema

    def is_enabled(self) -> bool:
        return self._server.is_connected

    async def execute(self, params: dict, ctx: "ToolContext") -> ToolOutput:
        result = await self._server.call_tool(self._mcp_tool.name, params)
        # MCP tool 的输出已经过 MCP server 的安全检查,
        # 但 Harness 仍会再次扫描 (双重保护)
        return ToolOutput(
            content=_extract_text(result.content),
            risk_level="low",
        )
```

---

## 六、Memory 记录形式与 System Prompt 组装

### 6.1 Memory 存储模型

```python
# harness/memory/store.py

@dataclass
class MemoryEntry:
    """一条记忆记录"""
    id: str                          # UUID
    content: str                     # 记忆内容
    embedding: list[float] | None    # text-embedding-3-small 向量 (1536-d)
    metadata: MemoryMetadata         # 类型/来源/时间/标签
    created_at: datetime
    updated_at: datetime

@dataclass
class MemoryMetadata:
    memory_type: Literal["fact", "preference", "project", "feedback", "reference"]
    source: str                      # "user", "agent", "auto", "heartbeat"
    scope: str                       # "global" | "project:<path>" | "session:<id>"
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5          # 0-1, LLM 评估的重要性
    ttl_days: int | None = None      # 过期时间
```

**存储后端**: SQLite (via `aiosqlite`) + FTS5 (全文搜索)

```sql
-- 主表
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    embedding BLOB,           -- 序列化的 float32 数组
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- FTS5 全文索引 (用于关键词搜索)
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid'
);

-- 元数据索引 (用于过滤)
CREATE INDEX idx_memories_type ON memories(json_extract(metadata_json, '$.memory_type'));
CREATE INDEX idx_memories_scope ON memories(json_extract(metadata_json, '$.scope'));
CREATE INDEX idx_memories_importance ON memories(json_extract(metadata_json, '$.importance'));
```

#### 6.1.1 观察类型分类（借鉴 claude-mem）

每条记忆在存储时自动分类为以下类型之一（由 LLM 在压缩管线中判断）：

| 类型 | 含义 | 示例 |
|------|------|------|
| `decision` | 架构决策、权衡推理 | "选择 SQLite 而非 PostgreSQL，因为单用户场景不需要客户端-服务器开销" |
| `bugfix` | Bug 修复的根本原因和方案 | "登录超时的根因是 token 刷新间隔配置错误" |
| `feature` | 新功能的实现模式 | "添加了 OAuth2 流程，使用 PKCE 扩展" |
| `refactor` | 代码重构决策 | "将 UserService 拆分为 AuthService + ProfileService" |
| `discovery` | 关于代码库的新发现 | "中间件层在路由之前执行，所以认证逻辑应放在中间件" |
| `change` | 其他值得记录的修改 | "更新了依赖版本到最新 LTS" |

分类在 AI 压缩管线中自动完成（参见 6.1.3），并在检索时用于过滤和排序（如用户搜索 "关于认证架构的决策" 时仅匹配 `decision` 类型）。

#### 6.1.2 MEMORY.md + SQLite + 向量数据库 三层存储

借鉴 claude-mem 的互补存储策略，Harness 采用三层存储：

| 层 | 存储介质 | 容量 | 用途 |
|----|---------|------|------|
| **1. MEMORY.md** | 文件系统 (Markdown) | ~200 行, ~25KB (遵循 Claude Code 限制) | 人类可读、跨会话持久化、Git 版本控制 |
| **2. SQLite + FTS5** | `~/.harness/memory.db` | 无限制 | 结构化查询、全文搜索、元数据过滤、关系追踪 |
| **3. ChromaDB** (可选) | `~/.harness/chroma/` | 无限制 | 语义相似度搜索、跨项目记忆关联 |

**互补而非竞争**: MEMORY.md 是用户可见的索引（Agent 和用户都能直接阅读），SQLite 是结构化的后端存储（程序化访问），ChromaDB 提供语义召回（"上周修复的那个认证 bug"）。三层各有侧重，互不替代。

#### 6.1.3 AI 压缩管线（借鉴 claude-mem 的 Compression Pipeline）

每次 Session 结束时，Harness 运行压缩管线将原始工具调用记录转化为可搜索的压缩记忆：

```
Session 结束触发
    │
    ├── 1. 原始捕获
    │   └── PostToolUse hook 已记录每次工具调用:
    │       tool_name, input, output, duration, error status
    │       原始数据量: 每次调用 500~10,000+ tokens
    │
    ├── 2. 批量分组
    │   └── 将相关的工具调用归组为 "episodes"（如：同一个 bug 修复的所有文件编辑）
    │       批量分组减少 LLM 调用次数 7-10x
    │
    ├── 3. AI 压缩
    │   └── 调用廉价模型 (Haiku) 对每个 episode:
    │       - 提取结构化事实 (JSON 数组)
    │       - 生成简洁叙述 (~200 tokens)
    │       - 自动分类: decision / bugfix / feature / refactor / discovery / change
    │       - 评估重要性 (0-1)
    │
    ├── 4. 双写存储
    │   ├── SQLite: 压缩后的叙述 + 结构化事实 (FTS5 索引)
    │   ├── ChromaDB: 向量嵌入 (用于语义搜索)
    │   └── MEMORY.md: 仅写入 importance >= 0.7 的条目 (用户可见)
    │
    └── 5. 原始数据归档
        └── 完整的原始 tool output 写入 JSONL transcript (Section 6.7)
            只在通过 memory_get (Layer 3) 明确请求时加载
```

**Token 效率**: 原始 tool output (~5,000 tokens) → 压缩叙述 (~200 tokens) = **~25x 压缩比**。压缩后内容进入 FTS5 索引和向量数据库，原始内容仅按需加载。

#### 6.1.4 生命周期钩子驱动的记忆注入

记忆系统通过五个生命周期钩子与 Agentic Loop 交互（借鉴 claude-mem 的 hook 体系）：

| 钩子 | 触发时机 | 行为 | 注入位置 |
|------|---------|------|---------|
| **SessionStart** | 新 Session 启动 | 查询 SQLite FTS5 + ChromaDB 语义搜索 → 在 ~2,000 token 预算内组装相关记忆 → 注入为 system prompt extension | System prompt extensions (Prompt Cache 稳定) |
| **UserPromptSubmit** | 用户发送消息 | 保存原始 prompt 文本 → 用于后续意图追踪和记忆关联 | 无注入（仅存储） |
| **PostToolUse** | 每次工具调用完成 | 捕获 observation: tool_name + input/output + status → 写入 SQLite observations 表 → 标记为 pending compression | 无注入（仅存储） |
| **SessionEnd** | Session 正常结束 | 触发 AI 压缩管线 → 批量压缩 pending observations → 双写 SQLite + ChromaDB | 无注入（仅处理） |
| **Stop** | 用户强制停止 | 生成 Session 摘要 → 标记 pending observations 等待下次压缩 | 无注入（仅处理） |

**缓存安全**: SessionStart 的记忆注入放在 system prompt extensions（静态内容块，Prompt Cache 友好），而非 per-turn 的 dynamic 部分。这样记忆注入变化时只影响新 session 的首次请求，不影响 mid-session cache。

### 6.2 Memory 写入 (memory_write tool)

```python
# harness/tools/builtin/memory_write.py

class MemoryWriteTool(Tool):
    """
    记忆写入工具——Agent 用它记录重要信息。

    借鉴 Claude Code memdir/memdir.ts:
    - 写入 MEMORY.md 文件 (项目级)
    - 也写入 SQLite (支持语义搜索)
    """

    name = "memory_write"
    description = "Write a fact or preference to persistent memory."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to remember"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "preference", "project", "feedback", "reference"],
                    "default": "fact"
                },
                "scope": {"type": "string", "default": "project"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "importance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
            },
            "required": ["content"]
        }

    async def execute(self, params: dict, ctx: ToolContext) -> ToolOutput:
        # 1. 写入 MEMORY.md (文件形式, 用户可见)
        memory_path = Path(ctx.cwd) / "MEMORY.md"
        entry = f"- {params['content']}\n"
        async with aiofiles.open(memory_path, "a") as f:
            await f.write(entry)

        # 2. 写入 SQLite (支持结构化搜索)
        memory = MemoryEntry(
            id=uuid4().hex,
            content=params["content"],
            embedding=None,  # 异步生成 (不阻塞工具调用)
            metadata=MemoryMetadata(
                memory_type=params.get("memory_type", "fact"),
                source=ctx.user_id,
                scope=f"project:{ctx.cwd}",
                tags=params.get("tags", []),
                importance=params.get("importance", 0.5),
            ),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        await memory_store.upsert(memory)

        # 3. 异步生成 embedding (后台任务)
        asyncio.create_task(self._generate_embedding(memory))

        return ToolOutput(f"Memory stored: {params['content'][:100]}...", ...)

    async def _generate_embedding(self, memory: MemoryEntry):
        """后台生成 embedding 用于语义搜索"""
        embedding = await embedding_client.embed(memory.content)
        await memory_store.update_embedding(memory.id, embedding)
```

### 6.3 Memory 检索 (RRF 融合)

```python
# harness/memory/reader.py

class MemoryReader:
    """
    混合检索: FTS5 (关键词) + 向量 (语义) → RRF 融合。

    借鉴 IronClaw workspace 的 RRF (Reciprocal Rank Fusion):
    score(doc) = sum over queries: 1 / (k + rank_in_query)

    k = 60 (经典值, 平衡高频词和长尾词)
    """

    K_RRF: int = 60

    async def search(
        self,
        query: str,
        scope: str | None = None,
        memory_type: str | None = None,
        top_k: int = 10
    ) -> list[MemoryEntry]:
        """
        两步检索 + RRF 融合:
        1. FTS5 全文搜索 → lexical_ranks
        2. 向量相似度 → semantic_ranks
        3. RRF 融合 → 最终排序
        """
        # 并行的两步检索
        lexical_results, semantic_results = await asyncio.gather(
            self._fts_search(query, scope, memory_type),
            self._semantic_search(query, scope, memory_type),
        )

        # RRF 融合
        fused = self._rrf_fuse(lexical_results, semantic_results, k=self.K_RRF)
        return fused[:top_k]

    def _rrf_fuse(
        self,
        ranked_a: list[str],  # doc IDs, best first
        ranked_b: list[str],
        k: int = 60
    ) -> list[str]:
        """ Reciprocal Rank Fusion """
        scores = defaultdict(float)
        for rank, doc_id in enumerate(ranked_a):
            scores[doc_id] += 1.0 / (k + rank + 1)
        for rank, doc_id in enumerate(ranked_b):
            scores[doc_id] += 1.0 / (k + rank + 1)
        return sorted(scores, key=scores.get, reverse=True)

    async def _fts_search(
        self, query: str, scope: str | None, memory_type: str | None
    ) -> list[str]:
        """SQLite FTS5 全文搜索"""
        conditions = ["memories_fts MATCH :query"]
        if scope:
            conditions.append("json_extract(metadata_json, '$.scope') = :scope")
        if memory_type:
            conditions.append("json_extract(metadata_json, '$.memory_type') = :type")
        # ... execute query ...

    async def _semantic_search(
        self, query: str, scope: str | None, memory_type: str | None
    ) -> list[str]:
        """向量相似度搜索"""
        query_embedding = await self.embedding_client.embed(query)
        # cosine_similarity(query_embedding, memory.embedding) for all memories
        # ... execute query ...
```

#### 6.3.1 渐进式三层检索（借鉴 claude-mem 的 Progressive Disclosure）

当前 RRF 融合检索返回完整的记忆条目（含原始 tool output）。借鉴 claude-mem 的 token 效率模式，Harness 增加 **渐进式三层检索** 策略：

```
用户查询: "上周修复的那个认证 bug 是怎么解决的？"
    │
    ├── Layer 1: memory_search → 轻量索引扫描
    │   └── 返回: ID + type + 压缩 summary (~50-100 tokens/result)
    │   └── 用户/Agent 浏览摘要列表, 锁定目标条目
    │   └── 默认返回 top-10, 可翻页
    │
    ├── Layer 2: memory_timeline → 时序上下文
    │   └── 返回: 目标 observation 前后的相关事件 (时间排序)
    │   └── 帮助理解修复的前因后果
    │   └── 紧凑列表格式, 默认前后各 3 条
    │
    └── Layer 3: memory_get → 完整详情
        └── 返回: 完整的 observation (含原始 input/output)
        └── 仅在 Agent 明确需要时调用
        └── ~500-1000 tokens per result
```

**Token 效率对比**:

| 策略 | 典型 Token 消耗 (10 条结果) | 节省比例 |
|------|--------------------------|---------|
| 传统全量加载 | ~5,000 tokens | 基准 |
| 渐进三层（只到 Layer 1） | ~500-1,000 tokens | **~80-90%** |
| 渐进三层（Layer 1 → 定位 → Layer 3 单条） | ~600-1,100 tokens | **~78%** |

**三层对应的 MCP/工具接口**:

| 工具 | Layer | 输入 | 输出 | Token/条 |
|------|-------|------|------|---------|
| `memory_search` | 1 | query, type_filter, top_k | id + type + summary | ~50-100 |
| `memory_timeline` | 2 | observation_id, context_before, context_after | 时序事件列表 (紧凑) | ~100-200 |
| `memory_get` | 3 | observation_id | 完整 observation (含 raw I/O) | ~500-1000 |

**RRF 融合与预算感知截断**: 三层检索的 Layer 1 仍使用 RRF (FTS5 + 语义向量) 进行结果排序，但增加 ~2,000 token 的注入预算限制。超过预算时从低重要性条目开始截断（而非简单地截断最后几条），确保高价值记忆优先进入上下文。

### 6.4 System Prompt 组装

```python
# harness/core/context.py

@dataclass
class ContextGatherer:
    """
    上下文组装器——为每个 turn 组装完整的 system prompt + user context。

    借鉴 Claude Code src/context.ts:
    - getUserContext() → CLAUDE.md + 日期
    - getSystemContext() → git status + branch info
    - fetchSystemPromptParts() → 三部分并行获取

    借鉴 Claude Code src/utils/queryContext.ts:
    - defaultSystemPrompt: 角色 + 所有工具描述
    - userContext: CLAUDE.md 文件递归发现 + 日期
    - systemContext: git status (truncated at 2000 chars)
    """
    config: "Config"
    llm: "LlmClient"
    tool_registry: "ToolRegistry"
    safety: "SafetyLayer"
    memory_reader: "MemoryReader"
    repo_map: "RepoMap | None"

    async def gather(
        self,
        cwd: str,
        messages: list[ChatMessage],
        custom_system_prompt: str | None = None,
    ) -> "AssembledContext":
        """
        并行收集所有上下文部件:

        1. static_prompt    — 角色定义 + 所有工具 JSON Schema
        2. repo_map_text    — 代码图谱树 (后台任务, 可用则注入)
        3. git_context      — git status + branch + recent commits
        4. claude_md        — 递归发现的 CLAUDE.md 文件
        5. memory_context   — 混合检索的记忆注入
        6. date_context     — ISO 日期
        """

        # 并行收集 (所有 I/O 操作独立)
        static, repo, dynamic = await asyncio.gather(
            self._build_static_prompt(custom_system_prompt),
            self._build_repo_map(messages, cwd),
            self._build_dynamic_context(cwd, messages),
        )

        return AssembledContext(
            system_prompt=[
                SystemPromptBlock(SystemPromptPart.STATIC, static),
                SystemPromptBlock(SystemPromptPart.REPO_MAP, repo) if repo else None,
                SystemPromptBlock(SystemPromptPart.DYNAMIC, dynamic),
            ],
            user_context=None,  # 通过 separate user message 注入
        )

    async def _build_static_prompt(self, custom: str | None) -> str:
        """角色定义 + 工具描述 (~25K tokens, cached)"""
        if custom:
            return custom

        tool_descriptions = "\n\n".join(
            f"## {t.name}\n{t.description}\n```json\n{json.dumps(t.input_schema, indent=2)}\n```"
            for t in self.tool_registry.all_tools()
        )

        return f"""You are an expert software engineering agent with access to tools.

## Available Tools

{tool_descriptions}

## Safety Rules

- Never read /etc/passwd, /etc/shadow, ~/.ssh/, or ~/.aws/credentials
- Never exfiltrate API keys or tokens in output
- Never execute commands with elevated privileges unless explicitly approved
- Tool outputs are wrapped in <tool_output> tags for safety

## Response Format

- When you need to use a tool, output a tool_use block with the tool name and parameters
- When you have a final answer, output text directly without tool calls
"""

    async def _build_repo_map(
        self, messages: list[ChatMessage], cwd: str
    ) -> str | None:
        """代码图谱 — 可用则注入, 超时则跳过"""
        if not self.repo_map:
            return None

        try:
            # 从最近消息中提取文件/标识符提及
            mentioned_files = extract_file_mentions(messages[-3:])
            mentioned_idents = extract_ident_mentions(messages[-3:])

            map_text = await asyncio.wait_for(
                self.repo_map.get_map(
                    chat_files=self._get_chat_files(),
                    mentioned_files=mentioned_files,
                    mentioned_idents=mentioned_idents,
                    token_budget=self.config.repomap.max_map_tokens,
                ),
                timeout=5.0  # 5 秒超时 — 宁可缺 map 也不阻塞 turn
            )

            # 注入为消息对 (Aider 模式)
            if map_text:
                return (
                    "<repo_map>\n"
                    "Below is a snapshot of the repository's file structure "
                    "and key definitions. Use this to navigate the codebase.\n"
                    "Do NOT edit files shown here unless they are already in the chat.\n\n"
                    f"{map_text}\n"
                    "</repo_map>"
                )
            return None
        except asyncio.TimeoutError:
            logger.info("RepoMap build timed out, proceeding without map")
            return None
        except Exception as e:
            logger.warning(f"RepoMap build failed: {e}")
            return None

    async def _build_dynamic_context(
        self, cwd: str, messages: list[ChatMessage]
    ) -> str:
        """Git status + CLAUDE.md + Memory + 日期"""
        git_info, claude_md, memory_ctx, date_str = await asyncio.gather(
            self._get_git_context(cwd),
            self._get_claude_md(cwd),
            self._get_memory_context(cwd, messages),
            asyncio.to_thread(lambda: datetime.now().isoformat()),
        )

        parts = []
        if git_info:
            parts.append(f"## Git Status\n{git_info[:2000]}")
        if claude_md:
            parts.append(f"## Project Context (CLAUDE.md)\n{claude_md[:3000]}")
        if memory_ctx:
            parts.append(f"## Relevant Memories\n{memory_ctx}")
        parts.append(f"Current date: {date_str}")

        return "\n\n".join(parts)

    async def _get_git_context(self, cwd: str) -> str | None:
        """获取 git 状态 (2000 char 截断)"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", cwd, "status", "--short",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            status = stdout.decode()[:2000]
            if not status.strip():
                return None

            # 同时获取 branch 和 recent commits
            branch_proc = await asyncio.create_subprocess_exec(
                "git", "-C", cwd, "branch", "--show-current",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            branch_out, _ = await branch_proc.communicate()
            branch = branch_out.decode().strip()

            return f"Branch: {branch}\n\nChanged files:\n{status}"
        except Exception:
            return None

    async def _get_claude_md(self, cwd: str) -> str | None:
        """递归发现 CLAUDE.md (从 cwd 向上到 repo root)"""
        content = []
        current = Path(cwd).resolve()
        repo_root = self._find_repo_root(current)

        while current >= repo_root:
            claude_md = current / "CLAUDE.md"
            if claude_md.exists():
                content.append(f"<!-- {current / 'CLAUDE.md'} -->\n{claude_md.read_text()}")
            current = current.parent
        return "\n\n".join(reversed(content)) if content else None

    async def _get_memory_context(
        self, cwd: str, messages: list[ChatMessage]
    ) -> str | None:
        """Memory 混合检索 + 注入"""
        # 从最近消息提取搜索 query
        query = " ".join(
            msg.text_content()
            for msg in messages[-3:]
            if msg.role == "user"
        )[:500]

        results = await self.memory_reader.search(
            query=query,
            scope=f"project:{cwd}",
            top_k=5
        )

        if not results:
            return None

        items = "\n".join(
            f"- [{m.metadata.memory_type}] {m.content[:200]}"
            for m in results
        )
        return (
            "The following memories may be relevant to this conversation.\n"
            "Use memory_read to get full details, or memory_write to add new facts.\n\n"
            f"{items}"
        )
```

### 6.5 完整消息序列示例

最终的 API request messages 序列:

```
[
  # ─── System Prompt ───────────────────────────────
  {"role": "system", "content": [
    {"type": "text", "text": "<STATIC> 角色 + 工具描述 (~25K tokens)</STATIC>",
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "<REPO_MAP> 代码图谱树 (~4K tokens)</REPO_MAP>",
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "<DYNAMIC> git status + CLAUDE.md + memory + date</DYNAMIC>"},
  ]},

  # ─── RepoMap 消息对 (Aider 模式) ──────────────────
  {"role": "user", "content": "Repo-map:\n<repo_map>...</repo_map>"},
  {"role": "assistant", "content": "Ok, I'll use this to navigate."},

  # ─── Conversation History ─────────────────────────
  {"role": "user", "content": "add a login endpoint"},
  {"role": "assistant", "content": [
    {"type": "text", "text": "I'll create the login endpoint."},
    {"type": "tool_use", "id": "call_1", "name": "file_read", "input": {...}},
  ]},
  {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "call_1",
     "content": "<tool_output name=\"file_read\">\n...file contents...\n</tool_output>"},
  ]},
  {"role": "assistant", "content": [
    {"type": "text", "text": "Now I'll write the login handler."},
    {"type": "tool_use", "id": "call_2", "name": "file_write", "input": {...}},
  ]},
  # ... more turns ...
]
```

### 6.6 System Prompt 哈希校验与组装算法

Prompt Cache 是成本优化的核心 — 一个 30K token 的 system prompt, 命中缓存可节省 ~$0.09/request (Sonnet 定价)。为此需要精确控制缓存的**何时失效**和**为何失效**。

#### 6.6.1 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│              PROMPT ASSEMBLY & CACHE PIPELINE                 │
│                                                               │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐ │
│  │ Gather   │ → │ Normalize │ → │  Hash    │ → │ Assemble │ │
│  │ Parts    │   │ (sort,    │   │ (BLAKE3  │   │ + Annotate│ │
│  │          │   │  dedup)   │   │  per-    │   │ cache_ctrl│ │
│  │          │   │           │   │  segment)│   │           │ │
│  └──────────┘   └───────────┘   └──────────┘   └──────────┘ │
│                                                    │          │
│                               ┌────────────────────┘          │
│                               ▼                               │
│                    ┌─────────────────────┐                    │
│                    │ Compare vs Session  │                    │
│                    │ Cache → Detect Diff │                    │
│                    └─────────┬───────────┘                    │
│                              │                                │
│              ┌───────────────┼───────────────┐                │
│              ▼               ▼               ▼                │
│         Full Hit       Partial Hit         Miss               │
│     (no change)    (STATIC same,        (log diff              │
│                     DYNAMIC changed)     for debug)            │
└──────────────────────────────────────────────────────────────┘
```

#### 6.6.2 PromptAssembler — 确定性组装管线

```python
# harness/llm/prompt_assembler.py

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable

@dataclass
class PromptSegment:
    """一个 prompt 片段, 携带 hash 和缓存策略"""
    kind: str                       # "static" | "repo_map" | "dynamic" | "memory" | "tools"
    text: str
    cacheable: bool                 # 是否标记 cache_control
    blake3_hash: str | None = None  # 内容 BLAKE3 hash (用于缓存失效检测)

@dataclass
class PromptFingerprint:
    """
    一次 LLM 请求的完整 prompt 指纹 — 用于检测缓存是否命中。

    包含所有影响缓存键的因素:
    - 每个 segment 的 BLAKE3 hash
    - 工具 schema 的排序列表 hash
    - 模型 ID
    - beta headers (如果启用)
    - fast_mode 状态
    - global_cache_strategy

    借鉴 Claude Code promptCacheBreakDetection.ts 的 hash 追踪。
    """
    segments: dict[str, str] = field(default_factory=dict)  # kind → blake3_hash
    tools_hash: str = ""
    model: str = ""
    fast_mode: bool = False
    betas: tuple[str, ...] = ()
    global_cache_strategy: str = "ephemeral"

    def to_blake3(self) -> str:
        """所有字段的稳定序列化 → BLAKE3"""
        payload = json.dumps({
            "segments": dict(sorted(self.segments.items())),
            "tools_hash": self.tools_hash,
            "model": self.model,
            "fast_mode": self.fast_mode,
            "betas": sorted(self.betas),
            "global_cache_strategy": self.global_cache_strategy,
        }, sort_keys=True)
        return hashlib.blake2b(payload.encode(), digest_size=16).hexdigest()


class PromptAssembler:
    """
    确定性 prompt 组装器 — 保证相同输入产生相同输出 (相同 prompt text
    + 相同 cache_control 断点位置)。

    Pipeline:
    1. gather: 收集所有 SystemPromptPart
    2. normalize: 排序工具 schema keys, 去重, 规范化空白
    3. hash: BLAKE3 每个 segment + 整体 fingerprint
    4. compare: 与会话缓存比对, 检测变更
    5. assemble: 组装 text block 数组, 标记 cache_control
    6. collect: 返回 blocks + breakpoints + fingerprint
    """

    # 静态/动态边界标记 — 此标记前的所有内容可缓存
    DYNAMIC_BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"

    def __init__(self, tool_registry: "ToolRegistry"):
        self._registry = tool_registry
        # 会话级缓存
        self._last_fingerprint: PromptFingerprint | None = None
        self._segment_cache: dict[str, PromptSegment] = {}

    def assemble(
        self,
        parts: list["SystemPromptBlock"],
        model: str,
        fast_mode: bool = False,
        betas: tuple[str, ...] = (),
    ) -> tuple[list[dict], list[int], PromptFingerprint]:
        """
        主入口 — 组装 system prompt 并计算指纹。

        Returns:
            blocks: Anthropic API text blocks (含 cache_control 注解)
            breakpoints: cache_control 断点位置索引
            fingerprint: 完整 prompt 指纹 (用于请求后验证缓存命中)
        """
        # Step 1 & 2: 收集 + 规范化
        segments = self._build_segments(parts)

        # Step 3: Hash
        tools_hash = self._hash_tool_schemas()
        fingerprint = PromptFingerprint(
            segments={s.kind: s.blake3_hash for s in segments if s.blake3_hash},
            tools_hash=tools_hash,
            model=model,
            fast_mode=fast_mode,
            betas=betas,
        )

        # Step 4: 比较 — 检测相比于上次的变化
        if self._last_fingerprint:
            diff = self._diff_fingerprints(self._last_fingerprint, fingerprint)
            if diff:
                logger.info(f"Prompt cache will miss — changed: {diff}")

        # Step 5: 组装 + cache_control 注解
        blocks = []
        breakpoints = []
        for i, seg in enumerate(segments):
            block = {"type": "text", "text": seg.text}
            if seg.cacheable:
                block["cache_control"] = {"type": "ephemeral"}
                breakpoints.append(i)
            blocks.append(block)

        # Step 6: 更新缓存
        self._last_fingerprint = fingerprint

        return blocks, breakpoints, fingerprint

    def _build_segments(self, parts: list["SystemPromptBlock"]) -> list[PromptSegment]:
        """收集 + 规范化每个 segment"""
        segments = []
        for part in parts:
            text = part.text

            # 规范化: 统一换行, 去除尾部空白
            text = text.replace("\r\n", "\n").rstrip()

            cacheable = part.kind in (
                SystemPromptPart.STATIC,
                SystemPromptPart.REPO_MAP,
            )

            blake3_hash = hashlib.blake2b(
                text.encode(), digest_size=16
            ).hexdigest()

            seg = PromptSegment(
                kind=part.kind.value,
                text=text,
                cacheable=cacheable,
                blake3_hash=blake3_hash,
            )
            segments.append(seg)

            # 检查缓存 — 相同 hash 的 segment 可复用
            if (part.kind.value in self._segment_cache
                and self._segment_cache[part.kind.value].blake3_hash == blake3_hash):
                logger.debug(f"Segment '{part.kind.value}' unchanged (cache hit)")
            self._segment_cache[part.kind.value] = seg

        return segments

    def _hash_tool_schemas(self) -> str:
        """
        对所有工具 schema 做稳定 hash。

        ORDER MATTERS: 工具列表的排列顺序影响 Anthropic cache key。
        使用 ToolRegistry.all_tools() 的稳定排序 (内置→MCP, 字母)。

        缓存策略:
        - 会话级 schema_cache: mid-session flag flip 不改变 schema
        - 只在 register/unregister 时失效
        """
        schemas = self._registry.get_schemas()  # 会话缓存
        canonical = json.dumps(schemas, sort_keys=True, separators=(",", ":"))
        return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()

    def _diff_fingerprints(
        self, old: PromptFingerprint, new: PromptFingerprint
    ) -> list[str]:
        """比较两个指纹, 返回变更列表 (用于调试)"""
        changes = []
        if old.model != new.model:
            changes.append(f"model: {old.model} → {new.model}")
        if old.fast_mode != new.fast_mode:
            changes.append(f"fast_mode: {old.fast_mode} → {new.fast_mode}")
        if old.tools_hash != new.tools_hash:
            changes.append("tools schema changed")
        if old.betas != new.betas:
            changes.append(f"betas: {old.betas} → {new.betas}")
        for kind in set(list(old.segments.keys()) + list(new.segments.keys())):
            old_h = old.segments.get(kind, "")
            new_h = new.segments.get(kind, "")
            if old_h != new_h:
                changes.append(f"segment '{kind}' changed")
        return changes

    @staticmethod
    def split_at_dynamic_boundary(
        blocks: list[dict], boundary_index: int
    ) -> tuple[list[dict], list[dict]]:
        """
        在 DYNAMIC_BOUNDARY 处分割 prompt blocks。

        boundary 之前的 blocks (STATIC + REPO_MAP) → cacheable
        boundary 之后的 blocks (DYNAMIC + MEMORY) → non-cacheable

        借鉴 Claude Code splitSysPromptPrefix() 函数。
        """
        prefix = blocks[:boundary_index]
        suffix = blocks[boundary_index:]
        return prefix, suffix
```

#### 6.6.3 CacheBreakDetector — 两阶段检测

```python
# harness/llm/cache_break_detection.py

@dataclass
class CacheBreakDetector:
    """
    两阶段 prompt cache 失效检测。

    Phase 1 (请求前): 记录当前 PromptFingerprint
    Phase 2 (响应后): 检查 response.usage.cache_read_input_tokens
        - 如果 cache_read_input_tokens == 0 且 fingerprint 与上次相同
          → cache break (服务器端缓存被清除了)
        - 如果 cache_read_input_tokens > 0
          → 记录为命中

    借鉴 Claude Code promptCacheBreakDetection.ts 的双阶段检测。

    输出: cache break 日志写入 .harness/cache_breaks/ 以便调试
    """

    def __init__(self, log_dir: str = ".harness/cache_breaks/"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._pending_fingerprint: PromptFingerprint | None = None
        self._break_count: int = 0

    # ─── Phase 1 ──────────────────────────────────────

    def record_request(self, fingerprint: PromptFingerprint):
        """API 调用前 — 记录预期指纹"""
        self._pending_fingerprint = fingerprint

    # ─── Phase 2 ──────────────────────────────────────

    def check_response(self, response: "LlmResponse") -> "CacheCheckResult":
        """
        API 响应后 — 检查缓存命中情况。

        返回 CacheCheckResult:
        - hit: cache_read_input_tokens > 0
        - miss_expected: fingerprint 变了 → 预期 miss
        - miss_unexpected: fingerprint 没变但 cache miss → cache break!
        """
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0)

        result = CacheCheckResult(
            cache_hit_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

        if cache_read > 0:
            result.hit = True
            result.hit_ratio = cache_read / (cache_read + cache_creation + usage.input_tokens)
            return result

        # Cache miss — 判断原因
        if self._pending_fingerprint and self._last_successful_fingerprint:
            if self._pending_fingerprint.to_blake3() == self._last_successful_fingerprint.to_blake3():
                # 指纹相同但缓存 miss → 服务器端 cache eviction
                result.hit = False
                result.break_detected = True
                result.break_reason = "server_eviction"
                self._break_count += 1
                self._log_break(self._pending_fingerprint)
        else:
            result.hit = False
            result.break_reason = "fingerprint_changed"

        return result

    def record_hit(self):
        """缓存命中 — 保存成功的指纹"""
        if self._pending_fingerprint:
            self._last_successful_fingerprint = self._pending_fingerprint
        self._pending_fingerprint = None

    def _log_break(self, fingerprint: PromptFingerprint):
        """将 cache break 详情写入调试文件"""
        timestamp = datetime.now().isoformat()
        break_file = self.log_dir / f"break_{timestamp}.json"
        break_file.write_text(json.dumps({
            "timestamp": timestamp,
            "fingerprint": fingerprint.to_blake3(),
            "segments": fingerprint.segments,
            "tools_hash": fingerprint.tools_hash,
            "model": fingerprint.model,
            "break_count": self._break_count,
        }, indent=2))

@dataclass
class CacheCheckResult:
    hit: bool = False
    hit_ratio: float = 0.0
    cache_hit_tokens: int = 0
    cache_creation_tokens: int = 0
    break_detected: bool = False
    break_reason: str = ""
```

#### 6.6.4 Sticky Latch 注册表

```python
# harness/llm/sticky_latches.py

class StickyLatchRegistry:
    """
    粘性锁存器 — 一旦在 Session 内设置, 值就 "粘住" 不变,
    直到 /clear 或 /compact。

    动机:
    Anthropic API 的 beta headers (如 fast mode, cache-editing)
    会影响服务端 cache key。如果这些值在 mid-session 被 toggle,
    服务端会以为 prompt 变了, 导致 ~50-70K tokens 的缓存全部失效。

    解决方案:
    首次 API 调用后锁定这些值, 后续 toggle 请求被忽略。

    借鉴 Claude Code claude.ts 的 sticky-on latches:
    - fast_mode: 首次请求后锁定
    - cache_editing: 首次请求后锁定
    - model: Session 内不允许切换 (除非显式 /model 命令 + 用户确认)

    生命周期:
    - Created: Session 启动时
    - Latched: 首次 API 调用后 (值锁定)
    - Cleared: /clear 命令 (重置, 允许用户切换模型)
    - Destroyed: Session 结束
    """

    def __init__(self):
        self._latches: dict[str, tuple[Any, bool]] = {}  # name → (value, locked)

    def set(self, name: str, value: Any):
        """设置值 — 如果已锁存则忽略"""
        if name in self._latches and self._latches[name][1]:
            logger.debug(f"Latch '{name}' is locked, ignoring new value: {value}")
            return
        self._latches[name] = (value, False)

    def latch(self, name: str, value: Any):
        """锁存值 — mark as locked"""
        self._latches[name] = (value, True)
        logger.debug(f"Latch '{name}' locked at: {value}")

    def get(self, name: str) -> Any | None:
        entry = self._latches.get(name)
        return entry[0] if entry else None

    def is_locked(self, name: str) -> bool:
        entry = self._latches.get(name)
        return entry[1] if entry else False

    def clear_all(self):
        """清除所有锁存器 (/clear 或 /compact 触发)"""
        self._latches.clear()

    # ─── 标准锁存器名称 ──────────────────────────────

    FAST_MODE = "fast_mode"
    CACHE_EDITING = "cache_editing"
    MODEL = "model"
    MAX_TOKENS = "max_tokens"
```

#### 6.6.5 缓存预热

```python
# harness/llm/cache_warmer.py

class CacheWarmer:
    """
    后台任务 — 定期 ping API 以维持 prompt cache 热度。

    Anthropic prompt cache 的 TTL 是 5 分钟 (ephemeral)。
    如果用户 idle 超过 5 分钟, 下次请求将 cache miss,
    需要重新发送整个 system prompt (付费)。

    策略:
    - 每 4 分钟发送一个 max_tokens=1 的空请求
    - 使用与主请求相同的 system prompt (确保 cache key 匹配)
    - API 返回 1 token 的输出 (几乎免费)
    - 用户恢复操作时, cache 仍然是热的

    借鉴 Aider cache_warming 模式 (5 min interval, max_tokens=1)。

    配置:
    [cache]
    warm_enabled = true
    warm_interval_seconds = 240   # 4 分钟 (留 1 分钟余量, TTL=5min)
    warm_max_pings = 1000          # ~83 小时 (防止无限 ping)
    """

    def __init__(
        self,
        llm: "LlmClient",
        interval: float = 240.0,
        max_pings: int = 1000,
    ):
        self.llm = llm
        self.interval = interval
        self.max_pings = max_pings
        self._task: asyncio.Task | None = None
        self._ping_count = 0
        self._enabled = True

    async def start(self):
        """启动后台缓存预热"""
        self._task = asyncio.create_task(self._warm_loop())

    async def stop(self):
        """停止缓存预热"""
        self._enabled = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _warm_loop(self):
        while self._enabled and self._ping_count < self.max_pings:
            await asyncio.sleep(self.interval)
            if not self._enabled:
                break
            try:
                await self.llm.complete(
                    messages=[],  # API 会自动注入 system prompt
                    max_tokens=1,
                    temperature=0,
                )
                self._ping_count += 1
                logger.debug(f"Cache warm ping {self._ping_count}/{self.max_pings}")
            except Exception as e:
                logger.warning(f"Cache warm ping failed: {e}")
                # 失败不停止 — 继续尝试下次
```

#### 6.6.6 缓存指标暴露

```python
# 所有缓存相关指标通过 OpenTelemetry 暴露 (参见 Section 13):

# 每次 API 调用的缓存元数据:
# - prompt_cache.hit_ratio: gauge (cache_read_tokens / total_input_tokens)
# - prompt_cache.hit_tokens: counter
# - prompt_cache.miss_tokens: counter
# - prompt_cache.break_events: counter (服务器端缓存被清除的次数)
# - prompt_cache.warm_pings: counter (缓存预热 ping 次数)
# - llm.tokens.input: counter (总输入 tokens)
# - llm.tokens.cache_read: counter (缓存命中的 tokens)
# - llm.tokens.cache_write: counter (新写入缓存的 tokens)
```

### 6.7 Transcript 格式

所有对话事件以 JSONL (每行一个 JSON) 格式持久化到 `~/.harness/transcripts/<session_id>.jsonl`。

```python
# harness/core/transcript.py

from enum import Enum
from pydantic import BaseModel

class TranscriptEventType(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    USER_MESSAGE = "user_message"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPACTION = "compaction"
    ERROR = "error"
    SIGNAL = "signal"
    METRIC = "metric"

class TranscriptEvent(BaseModel):
    """单条 transcript 记录"""
    type: TranscriptEventType
    timestamp: str  # ISO 8601
    session_id: str
    thread_id: str | None = None
    turn_id: str | None = None
    data: dict = {}
```

**JSONL 示例**:

```jsonl
{"type":"session_start","timestamp":"2026-06-05T10:00:00Z","session_id":"a1b2c3d4e5f6","data":{"config_hash":"abc123","model":"claude-sonnet-4-6","cwd":"/home/user/project"}}
{"type":"turn_start","timestamp":"2026-06-05T10:00:05Z","session_id":"a1b2c3d4e5f6","thread_id":"t1","turn_id":"u1","data":{"user_input":"add login endpoint"}}
{"type":"llm_request","timestamp":"2026-06-05T10:00:05Z","session_id":"a1b2c3d4e5f6","turn_id":"u1","data":{"messages_count":6,"tokens_estimate":35000,"fingerprint":"cafe1234"}}
{"type":"llm_response","timestamp":"2026-06-05T10:00:08Z","session_id":"a1b2c3d4e5f6","turn_id":"u1","data":{"model":"claude-sonnet-4-6","stop_reason":"tool_use","tokens_in":35000,"tokens_out":150,"cache_hit_tokens":28000}}
{"type":"tool_call","timestamp":"2026-06-05T10:00:08Z","session_id":"a1b2c3d4e5f6","turn_id":"u1","data":{"tool_name":"file_read","params":{"path":"/home/user/project/app.py"},"safe_params":{"path":"/home/user/project/app.py"}}}
{"type":"tool_result","timestamp":"2026-06-05T10:00:09Z","session_id":"a1b2c3d4e5f6","turn_id":"u1","data":{"tool_name":"file_read","is_error":false,"duration_ms":150,"output_length_chars":2048,"truncated":false}}
{"type":"turn_end","timestamp":"2026-06-05T10:00:15Z","session_id":"a1b2c3d4e5f6","thread_id":"t1","turn_id":"u1","data":{"status":"completed","llm_calls":3,"tool_calls":6,"tokens_used":45000,"duration_ms":10000}}
{"type":"session_end","timestamp":"2026-06-05T10:30:00Z","session_id":"a1b2c3d4e5f6","data":{"status":"completed","total_tokens":250000,"total_cost_usd":1.25,"turns":15}}
```

**Transcript 作用**:
- **审计**: 所有模型输入/输出完整记录, 用于安全审查
- **恢复**: 崩溃后从 transcript 重建 Session 状态
- **回放**: 重放 transcript 可以复现 bug
- **计费**: 精确的 token 计数和成本追踪
- **训练**: 用于 fine-tune 或 RLHF 的数据源 (需用户 opt-in)

### 6.8 Resource Limits 资源限制

```python
# harness/core/limits.py

@dataclass
class ResourceLimits:
    """
    硬性资源限制 — 防止资源耗尽。

    这些是安全上限, 即使自定义配置也不能超越。
    区别于 Config 中的可调参数 (如 max_turns, compaction_threshold)。
    """

    # ─── Token 限制 ──────────────────────────────────
    max_input_tokens_per_call: int = 200_000       # 单次 API 调用的最大输入
    max_output_tokens_per_call: int = 64_000        # 单次 API 调用的最大输出
    max_total_tokens_per_session: int = 10_000_000  # 整个 session 的总 token 预算

    # ─── Turn 限制 ──────────────────────────────────
    max_turns_per_session: int = 500                # 每个 session 的最大 turns
    max_tool_calls_per_turn: int = 100               # 单个 turn 内的最大工具调用数

    # ─── 工具限制 ────────────────────────────────────
    max_tool_output_chars: int = 100_000             # 单次工具输出的最大字符数
    max_file_read_size_bytes: int = 10 * 1024 * 1024 # 10MB — 文件读取硬限制
    max_bash_timeout_seconds: int = 300              # 5 分钟 — Shell 命令硬超时
    max_concurrent_bash_jobs: int = 5                # 同时运行的 shell 命令

    # ─── 子 Agent 限制 ──────────────────────────────
    max_subagents_per_session: int = 20              # 每个 session 最大子 Agent 数
    max_subagent_depth: int = 2                      # 嵌套深度 (Agent→SubAgent, 不能再深)

    # ─── Memory 限制 ────────────────────────────────
    max_memory_entries_per_scope: int = 10_000       # 每个 scope 的最大记忆条数
    max_memory_embedding_dim: int = 1536             # text-embedding-3-small 维度

    # ─── 缓存限制 ───────────────────────────────────
    max_tracked_cache_sources: int = 10              # CacheBreakDetector 追踪的源数
    cache_warm_max_pings: int = 1_000                # ~83 小时 (4 分钟间隔)

    # ─── 成本限制 ───────────────────────────────────
    max_cost_per_session_usd: float = 100.0          # 每个 session 最大费用
    max_cost_per_turn_usd: float = 5.0               # 单个 turn 最大费用

    # ─── 并发限制 ───────────────────────────────────
    max_concurrent_llm_calls: int = 3                # 同时进行的 LLM API 调用
    max_concurrent_mcp_servers: int = 10             # 同时连接的 MCP server

    # ─── 时间限制 ───────────────────────────────────
    max_session_duration_seconds: int = 6 * 3600     # 6 小时 — Session 最大存活时间
    max_turn_duration_seconds: int = 300             # 5 分钟 — 单个 turn 最大耗时

    def check(self, current_value: int | float, limit_name: str) -> bool:
        """检查是否超出限制 — 超出则 raise ResourceLimitExceededError"""
        limit = getattr(self, limit_name, None)
        if limit is not None and current_value > limit:
            raise ResourceLimitExceededError(
                f"{limit_name}: {current_value} > {limit}"
            )
        return True
```

---

## 七、结构化校验、重试与降级策略

### 7.1 整体容错架构

```
                        ┌──────────────────────┐
                        │    API CALL           │
                        └──────────┬───────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
                    ▼              ▼              ▼
            ┌─────────────┐ ┌───────────┐ ┌──────────────┐
            │ 成功         │ │ 可重试错误 │ │ 不可重试错误   │
            │ → 正常流程   │ │ → retry   │ │ → degrade    │
            └─────────────┘ └─────┬─────┘ └──────┬───────┘
                                  │               │
                    ┌─────────────┼──────┐        │
                    ▼             ▼      ▼        ▼
              ┌──────────┐ ┌────────┐ ...  ┌──────────────┐
              │ RateLimit│ │Context │      │ Auth Error    │
              │ → backoff│ │Overflow│      │ → refresh     │
              │ + retry  │ │→compact│      │   + retry 1x  │
              └──────────┘ │→ retry │      └──────────────┘
                           └────────┘
```

### 7.2 结构化输出校验

```python
# harness/core/validation.py

class StructuredOutputValidator:
    """
    JSON Schema 结构化输出校验。

    借鉴 Claude Code SyntheticOutputTool + registerStructuredOutputEnforcement:
    - 模型被要求输出 JSON(通过 tool_use 的 input 字段)
    - 校验失败 → 注入 retry message (最多 5 次)
    - 成功 → 提取 structured_output attachment
    """

    MAX_RETRIES = 5

    def __init__(self, schema: dict[str, Any]):
        self.schema = schema
        self.validator = jsonschema.Draft202012Validator(schema)
        self.retry_count = 0

    def validate(self, output: dict) -> "ValidationResult":
        """校验并返回结果或 retry prompt"""
        errors = list(self.validator.iter_errors(output))

        if not errors:
            self.retry_count = 0
            return ValidationResult(
                valid=True,
                output=output
            )

        self.retry_count += 1

        if self.retry_count > self.MAX_RETRIES:
            return ValidationResult(
                valid=False,
                terminal=True,
                error=f"Max retries ({self.MAX_RETRIES}) exceeded. Last errors: {errors}"
            )

        # 构建 retry prompt
        error_messages = "\n".join(
            f"- {e.json_path}: {e.message}" for e in errors[:5]
        )
        return ValidationResult(
            valid=False,
            terminal=False,
            retry_prompt=(
                f"Your previous output did not match the required schema. "
                f"Please fix these errors:\n{error_messages}\n\n"
                f"Output the corrected JSON via the synthetic_output tool."
            ),
            errors=errors
        )
```

### 7.3 多层级重试策略

```python
# harness/llm/retry.py

from dataclasses import dataclass, field
from enum import Enum
import asyncio
import random

class RetryDecision(Enum):
    RETRY = "retry"                       # 指数退避后重试
    RETRY_WITH_CREDENTIAL_REFRESH = "refresh"  # 刷新凭证后重试
    RETRY_WITH_COMPACTION = "compact"     # 压缩上下文后重试
    RETRY_WITH_MODEL_DOWNGRADE = "downgrade"  # 降级到更便宜模型
    FATAL = "fatal"                       # 不可恢复

@dataclass
class RetryConfig:
    max_retries: int = 5
    base_delay: float = 1.0          # 初始延迟 (秒)
    max_delay: float = 60.0          # 最大延迟
    jitter: float = 0.1              # 抖动 (10%)
    retryable_statuses: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504, 529})
    total_timeout: float = 300.0     # 总超时 (5 分钟)

class LlmRetryHandler:
    """
    LLM API 调用的多层重试处理器。

    借鉴 Claude Code src/services/api/withRetry.ts:
    - withRetry() AsyncGenerator
    - 529 overloaded → MAX_529_RETRIES → FallbackTriggeredError
    - 429 rate limited → retry-after header → exponential backoff + jitter
    - 413 context overflow → auto compact → retry
    - 401 auth → credential refresh → retry
    - 连续 3 次同类型失败 → escalate

    Persistent retry (CLAUDE_CODE_UNATTENDED_RETRY):
    - 无人值守模式下无限重试
    - 每 30s heartbeat yield (保持连接)
    - 总上限 6 小时
    """

    def __init__(self, config: RetryConfig = RetryConfig()):
        self.config = config
        self._consecutive_529 = 0
        self._consecutive_413 = 0

    async def execute(
        self,
        call_fn: Callable[[], Awaitable[LlmResponse]],
        context: "RetryContext",
    ) -> LlmResponse:
        """
        执行带重试的 LLM 调用。

        Retry 决策表:
        ┌──────────────────┬──────────┬─────────────────────────────────┐
        │ Error            │ Decision │ Action                           │
        ├──────────────────┼──────────┼─────────────────────────────────┤
        │ RateLimit (429)  │ RETRY    │ retry-after || exp backoff+jitter│
        │ Overloaded (529) │ RETRY    │ max 3x → fallback model           │
        │ Context (413)    │ COMPACT  │ auto-compact → retry              │
        │ Auth (401)       │ REFRESH  │ OAuth refresh → retry 1x          │
        │ Network timeout  │ RETRY    │ exp backoff                      │
        │ Server 5xx       │ RETRY    │ exp backoff + jitter             │
        │ Bad Gateway(502) │ RETRY    │ exp backoff                      │
        │ Unknown/4xx      │ FATAL    │ propagate error                  │
        └──────────────────┴──────────┴─────────────────────────────────┘
        """
        start = time.monotonic()
        last_error = None

        for attempt in range(self.config.max_retries):
            try:
                return await call_fn()
            except LlmError as e:
                last_error = e
                decision = self._classify(e, attempt)

                if decision == RetryDecision.FATAL:
                    raise

                if decision == RetryDecision.RETRY_WITH_COMPACTION:
                    if self._consecutive_413 >= 3:
                        raise  # circuit break — 别无限 compact
                    self._consecutive_413 += 1
                    await context.compact()
                    continue

                if decision == RetryDecision.RETRY_WITH_CREDENTIAL_REFRESH:
                    await context.refresh_credentials()
                    continue

                if decision == RetryDecision.RETRY_WITH_MODEL_DOWNGRADE:
                    await context.switch_to_fallback_model()
                    continue

                # RETRY: 计算延迟
                delay = self._compute_delay(attempt, e)

                if time.monotonic() - start + delay > self.config.total_timeout:
                    raise LlmError(f"total retry timeout ({self.config.total_timeout}s) exceeded")

                await asyncio.sleep(delay)
                continue

        raise LlmError(f"max retries ({self.config.max_retries}) exceeded: {last_error}")

    def _classify(self, error: LlmError, attempt: int) -> RetryDecision:
        """将错误映射到重试策略"""
        if isinstance(error, RateLimitError):
            return RetryDecision.RETRY
        if isinstance(error, OverloadedError):
            self._consecutive_529 += 1
            if self._consecutive_529 >= 3:
                return RetryDecision.RETRY_WITH_MODEL_DOWNGRADE
            return RetryDecision.RETRY
        if isinstance(error, ContextOverflowError):
            return RetryDecision.RETRY_WITH_COMPACTION
        if isinstance(error, AuthError):
            if attempt == 0:
                return RetryDecision.RETRY_WITH_CREDENTIAL_REFRESH
            return RetryDecision.FATAL  # 刷新后仍然 401 → 不重试
        if isinstance(error, (NetworkError, TimeoutError, ServerError)):
            return RetryDecision.RETRY
        return RetryDecision.FATAL

    def _compute_delay(self, attempt: int, error: LlmError | None = None) -> float:
        """指数退避 + jitter"""
        if hasattr(error, 'retry_after') and error.retry_after:
            return min(error.retry_after, self.config.max_delay)

        delay = self.config.base_delay * (2.0 ** attempt)
        delay = min(delay, self.config.max_delay)
        jitter = delay * self.config.jitter * (2 * random.random() - 1)
        return delay + jitter
```

### 7.4 降级策略

```python
# harness/llm/degradation.py

class DegradationManager:
    """
    多层降级策略—当主路径失败时优雅降级。

    借鉴 Claude Code 的:
    - FallbackTriggeredError (529 过载 → 自动切换备选模型)
    - 3P fallbacks (降级到可用模型)
    - fast mode cooldown (长时间 429/529 → 禁用 fast mode)
    """

    def __init__(self, config: "Config"):
        self.config = config
        self.fast_mode_cooldown_until: float = 0.0
        self._degradation_level = 0  # 0=正常, 1=降级模型, 2=更小上下文, 3=无工具

    async def degrade(self, error: LlmError) -> "DegradedConfig":
        """根据错误类型选择降级路径"""
        self._degradation_level += 1

        match error:
            case OverloadedError():
                return self._switch_to_fallback_model()
            case ContextOverflowError():
                return self._reduce_context()
            case _:
                # Generic degradation: try with fewer tools
                return self._reduce_tools()

    def _switch_to_fallback_model(self) -> "DegradedConfig":
        """切换到备选模型 (如 Claude Opus → Sonnet)"""
        fallback = self.config.llm.fallback_model
        if not fallback:
            raise LlmError("no fallback model configured")
        return DegradedConfig(
            model=fallback,
            max_tokens=min(self.config.llm.max_tokens, 4096),
        )

    def _reduce_context(self) -> "DegradedConfig":
        """减少上下文窗口使用"""
        return DegradedConfig(
            model=self.config.llm.model,
            max_tokens=self.config.llm.max_tokens // 2,
            reduced_context=True,
        )

    def _reduce_tools(self) -> "DegradedConfig":
        """减少可用工具集 (只保留最基本的读工具)"""
        return DegradedConfig(
            model=self.config.llm.model,
            max_tokens=self.config.llm.max_tokens,
            allowed_tools={"file_read", "glob_search", "grep"},
        )
```

### 7.5 工具层面的校验

```python
# harness/tools/executor.py (补充)

class ToolExecutor:
    # ... (之前的代码) ...

    async def execute_with_retry(
        self,
        tool_name: str,
        params: dict,
        ctx: ToolContext,
        max_retries: int = 2
    ) -> ToolOutput:
        """
        工具执行带重试—对于可恢复错误自动重试。

        Retry 规则:
        - TimeoutError → NO retry (已经超时了)
        - ExecutionFailedError → retry 1x (可能是临时故障)
        - RateLimitedError → retry after delay
        - NotAuthorizedError → NO retry (权限不会自动改变)
        - SandboxError → retry 1x (Docker 可能暂时不稳定)
        """
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await self.execute(tool_name, params, ctx)
            except (RateLimitedError) as e:
                if attempt < max_retries:
                    delay = e.retry_after or 2.0 ** attempt
                    await asyncio.sleep(delay)
                    continue
                raise
            except (ExecutionFailedError, SandboxError) as e:
                if attempt < max_retries:
                    logger.warning(f"Retrying {tool_name} after: {e}")
                    last_error = e
                    continue
                raise
            except (TimeoutError, NotAuthorizedError):
                raise  # 不可重试

        raise last_error  # unreachable if max_retries=0
```

### 7.6 全局容错总结

```
┌───────────────────────────────────────────────────────────────┐
│                  ERROR → RECOVERY TABLE                        │
├─────────────────────┬──────────────────┬──────────────────────┤
│ 错误类型             │ 恢复策略          │ 降级路径              │
├─────────────────────┼──────────────────┼──────────────────────┤
│ RateLimit (429)     │ 退避 + 重试       │ → 取消 fast mode     │
│ Overloaded (529)    │ 3x 后 fallback   │ → Sonnet (from Opus) │
│ Context Overflow    │ auto-compact     │ → reduce context     │
│                     │   → retry        │   window             │
│ Auth Error (401)    │ credential refresh│ → 提示用户重新登录   │
│                     │   → retry 1x     │                      │
│ Network Timeout     │ 退避 + 重试       │ → retry with less   │
│ Server 5xx          │ 退避 + 重试       │ → retry with less   │
│ Tool Exec Failed    │ retry 1x         │ → 返回错误给 LLM     │
│ JSON Schema Invalid │ retry prompt     │ → 5x 后终止          │
│ Tool Dup Fail 3x    │ inject warning   │ → 5x 后 force_text  │
│ Compaction Fail 3x  │ circuit break    │ → reactive compact   │
│ Fast Mode 429/529   │ cooldown 10min   │ → disable fast mode  │
│ Budget Exceeded     │ 停止, 返回摘要   │ → 无降级             │
│ Max Turns Reached   │ 停止, 返回摘要   │ → 无降级             │
└─────────────────────┴──────────────────┴──────────────────────┘
```

### 7.7 错误分类体系 (Error Taxonomy)

完整的错误类型层次结构 — 用于错误恢复路由、日志记录和用户反馈。

```python
# harness/core/errors.py

class HarnessError(Exception):
    """所有 Harness 错误的基础类"""
    code: str                    # 错误码 (如 "LLM_RATE_LIMITED")
    message: str                 # 用户可读的消息
    recoverable: bool = False    # 是否可自动恢复
    retryable: bool = False      # 是否可以重试
    details: dict = {}           # 额外调试信息

# ─── LLM 错误 ───────────────────────────────────────

class LlmError(HarnessError):
    """LLM API 调用错误"""
    pass

class RateLimitError(LlmError):
    """429 — 速率限制"""
    code = "LLM_RATE_LIMITED"
    retry_after: float | None = None
    retryable = True

class OverloadedError(LlmError):
    """529 — 服务过载"""
    code = "LLM_OVERLOADED"
    retryable = True

class ContextOverflowError(LlmError):
    """413 — 上下文超出窗口"""
    code = "LLM_CONTEXT_OVERFLOW"
    current_tokens: int = 0
    max_tokens: int = 0
    recoverable = True  # auto-compact

class AuthError(LlmError):
    """401 — 认证失败"""
    code = "LLM_AUTH_ERROR"
    recoverable = True  # credential refresh

class ServerError(LlmError):
    """5xx — 服务端错误"""
    code = "LLM_SERVER_ERROR"
    retryable = True

class BadGatewayError(ServerError):
    """502 — 网关错误"""
    code = "LLM_BAD_GATEWAY"
    retryable = True

class NetworkError(LlmError):
    """网络超时/连接失败"""
    code = "LLM_NETWORK_ERROR"
    retryable = True

class LlmTimeoutError(LlmError):
    """LLM 调用超时 (非网络超时)"""
    code = "LLM_TIMEOUT"
    retryable = True

class LlmResponseParseError(LlmError):
    """LLM 响应解析失败 (JSON/XML 格式错误)"""
    code = "LLM_PARSE_ERROR"

# ─── Tool 错误 ──────────────────────────────────────

class ToolError(HarnessError):
    """工具执行错误"""
    tool_name: str = ""

class ToolNotFoundError(ToolError):
    code = "TOOL_NOT_FOUND"

class InvalidParametersError(ToolError):
    code = "TOOL_INVALID_PARAMS"

class ToolExecutionError(ToolError):
    code = "TOOL_EXECUTION_FAILED"
    retryable = True

class ToolTimeoutError(ToolError):
    code = "TOOL_TIMEOUT"

class NotAuthorizedError(ToolError):
    code = "TOOL_NOT_AUTHORIZED"

class ApprovalRequiredError(ToolError):
    """需要用户审批 — 不是错误, 是控制流信号"""
    code = "TOOL_APPROVAL_REQUIRED"
    tool_name: str
    params: dict = {}
    reason: str = ""

class RateLimitedToolError(ToolError):
    code = "TOOL_RATE_LIMITED"
    retryable = True
    retry_after: float | None = None

# ─── Safety 错误 ────────────────────────────────────

class SafetyError(HarnessError):
    """安全相关错误"""
    pass

class PromptInjectionDetectedError(SafetyError):
    code = "SAFETY_PROMPT_INJECTION"
    pattern: str = ""
    severity: str = "critical"

class LeakDetectedError(SafetyError):
    code = "SAFETY_LEAK_DETECTED"
    secret_type: str = ""  # "aws_key", "github_token", etc.

class ToolOutputBlockedError(SafetyError):
    code = "SAFETY_OUTPUT_BLOCKED"
    tool_name: str = ""

# ─── Config 错误 ────────────────────────────────────

class ConfigError(HarnessError):
    """配置错误"""
    pass

class ConfigValidationError(ConfigError):
    code = "CONFIG_VALIDATION_ERROR"

class ConfigNotFoundError(ConfigError):
    code = "CONFIG_NOT_FOUND"

# ─── Sandbox 错误 ───────────────────────────────────

class SandboxError(HarnessError):
    """沙箱执行错误"""
    pass

class DockerUnavailableError(SandboxError):
    code = "SANDBOX_DOCKER_UNAVAILABLE"

class WasmCompileError(SandboxError):
    code = "SANDBOX_DOCKER_UNAVAILABLE"

class SandboxExecutionError(SandboxError):
    code = "SANDBOX_EXECUTION_FAILED"

class SandboxTimeoutError(SandboxError):
    code = "SANDBOX_TIMEOUT"

class SandboxMemoryExceededError(SandboxError):
    code = "SANDBOX_MEMORY_EXCEEDED"

# ─── 资源/预算错误 ──────────────────────────────────

class BudgetError(HarnessError):
    """成本/资源预算超限"""
    pass

class CostBudgetExceededError(BudgetError):
    code = "BUDGET_COST_EXCEEDED"
    limit_usd: float = 0.0
    current_usd: float = 0.0

class TokenBudgetExceededError(BudgetError):
    code = "BUDGET_TOKEN_EXCEEDED"

class ResourceLimitExceededError(BudgetError):
    code = "BUDGET_RESOURCE_LIMIT"

class SubAgentLimitExceededError(BudgetError):
    code = "BUDGET_SUBAGENT_LIMIT"

class SubAgentDepthExceededError(BudgetError):
    code = "BUDGET_SUBAGENT_DEPTH"

# ─── Loop/状态错误 ──────────────────────────────────

class LoopError(HarnessError):
    """Agentic Loop 错误"""
    pass

class MaxTurnsReachedError(LoopError):
    code = "LOOP_MAX_TURNS"

class CircuitBreakerTrippedError(LoopError):
    code = "LOOP_CIRCUIT_BREAK"
    component: str = ""

# ─── 错误恢复路由 (连接 Section 3.2 recovery.py) ────

ERROR_RECOVERY_MAP: dict[str, str] = {
    "LLM_RATE_LIMITED":         "retry_with_backoff",
    "LLM_OVERLOADED":           "retry_then_fallback_model",
    "LLM_CONTEXT_OVERFLOW":     "auto_compact_then_retry",
    "LLM_AUTH_ERROR":           "credential_refresh_then_retry",
    "LLM_SERVER_ERROR":         "retry_with_backoff",
    "LLM_BAD_GATEWAY":          "retry_with_backoff",
    "LLM_NETWORK_ERROR":        "retry_with_backoff",
    "LLM_TIMEOUT":              "retry_with_backoff",
    "LLM_PARSE_ERROR":          "inject_retry_prompt",
    "TOOL_EXECUTION_FAILED":    "retry_1x_then_return_error_to_llm",
    "TOOL_TIMEOUT":             "return_error_to_llm",
    "TOOL_NOT_AUTHORIZED":      "return_error_to_llm",
    "TOOL_RATE_LIMITED":        "retry_after_delay",
    "SAFETY_PROMPT_INJECTION":  "block_and_log",
    "SAFETY_LEAK_DETECTED":     "redact_or_block",
    "SAFETY_OUTPUT_BLOCKED":    "block_tool_result",
    "BUDGET_COST_EXCEEDED":     "stop_session",
    "BUDGET_RESOURCE_LIMIT":    "stop_session",
    "LOOP_CIRCUIT_BREAK":       "stop_with_partial_result",
    "CONFIG_VALIDATION_ERROR":  "use_defaults_or_exit",
}
```

---

## 八、核心抽象接口

***(Tool ABC, LlmClient ABC, LoopDelegate ABC 定义 — 参见前述章节的完整实现)***

### 8.1 插件系统

```python
# harness/plugins/manager.py

from enum import Enum
from abc import ABC, abstractmethod

class PluginType(Enum):
    TOOL = "tool"            # 注册自定义工具
    HOOK = "hook"            # 注册生命周期钩子
    PROVIDER = "provider"    # 添加 LLM provider
    COMMAND = "command"      # 添加自定义 / 命令
    SKILL = "skill"          # 添加自定义 skill

class HookPoint(Enum):
    """10 个生命周期钩子点"""
    ON_SESSION_START = "on_session_start"
    ON_SESSION_END = "on_session_end"
    ON_TURN_START = "on_turn_start"
    ON_TURN_END = "on_turn_end"
    ON_PRE_LLM_CALL = "on_pre_llm_call"
    ON_POST_LLM_CALL = "on_post_llm_call"
    ON_PRE_TOOL_EXECUTE = "on_pre_tool_execute"
    ON_POST_TOOL_EXECUTE = "on_post_tool_execute"
    ON_COMPACTION = "on_compaction"
    ON_ERROR = "on_error"


class HarnessPlugin(ABC):
    """插件基类"""
    name: str
    version: str
    plugin_type: PluginType

    @abstractmethod
    async def on_load(self, ctx: "PluginContext"):
        """插件加载时调用"""
        ...

    @abstractmethod
    async def on_unload(self):
        """插件卸载时调用"""
        ...


class PluginManager:
    """
    插件加载和管理。

    加载优先级 (后加载覆盖先加载的钩子):
    1. 内置插件 (~/.harness/plugins/builtin/)
    2. 用户插件 (~/.harness/plugins/)
    3. 项目插件 (.harness/plugins/)
    """

    def __init__(self):
        self._plugins: dict[str, HarnessPlugin] = {}
        self._hooks: dict[HookPoint, list[callable]] = {
            hp: [] for hp in HookPoint
        }
        self._tool_contributors: list[callable] = []

    def register(self, plugin: HarnessPlugin):
        """注册插件"""
        self._plugins[plugin.name] = plugin

    def register_hook(self, point: HookPoint, callback: callable):
        """注册生命周期钩子"""
        self._hooks[point].append(callback)

    def register_tool_provider(self, provider: callable):
        """注册工具提供者 (返回 list[Tool])"""
        self._tool_contributors.append(provider)

    async def emit(self, point: HookPoint, **kwargs):
        """触发钩子 — 按注册顺序依次调用"""
        for hook in self._hooks[point]:
            try:
                await hook(**kwargs)
            except Exception as e:
                logger.error(f"Hook {point.value} failed: {e}")
                # 钩子失败不应该中断主流程

    def discover_tools(self) -> list[Tool]:
        """收集所有插件的工具"""
        tools = []
        for provider in self._tool_contributors:
            try:
                tools.extend(provider())
            except Exception as e:
                logger.error(f"Tool provider failed: {e}")
        return tools

    @staticmethod
    def discover_plugins(plugin_dirs: list[str]) -> list[HarnessPlugin]:
        """
        自动发现插件 — 扫描目录中的 .py 文件,
        查找继承 HarnessPlugin 的类。
        """
        plugins = []
        for d in plugin_dirs:
            path = Path(d)
            if not path.exists():
                continue
            for py_file in path.glob("*.py"):
                # importlib 动态加载
                spec = importlib.util.spec_from_file_location(
                    py_file.stem, str(py_file)
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for attr in dir(module):
                    obj = getattr(module, attr)
                    if (isinstance(obj, type)
                        and issubclass(obj, HarnessPlugin)
                        and obj is not HarnessPlugin):
                        plugins.append(obj())
        return plugins
```

---

## 九、配置系统

### 9.1 配置验证与热加载

```python
# harness/config/validator.py

class ConfigValidator:
    """
    配置验证器 — 在加载配置后立即验证所有约束。

    验证规则:
    - 模型名在已知模型列表中
    - API key 存在 (如果使用云端 LLM)
    - compaction_threshold 在合理范围 (0.5-0.95)
    - max_turns >= 1
    - 工具超时值 >= 1s
        # Sandbox 限制
        if not config.sandbox.runtime:
            issues.append("WARN: sandbox runtime not set, defaulting to docker")
    - 端口范围合法 (MCP server)
    """

    @staticmethod
    def validate(config: "Config") -> list[str]:
        """返回警告/错误列表"""
        issues = []

        # 模型检查
        if not config.llm.model:
            issues.append("ERROR: llm.model is required")

        # API key (cloud providers)
        if config.llm.provider == "anthropic" and not config.llm.api_key:
            # 检查环境变量
            if not os.environ.get("ANTHROPIC_API_KEY"):
                issues.append("ERROR: Anthropic API key not found")

        # 压缩阈值
        if not 0.5 <= config.loop.compaction_threshold <= 0.95:
            issues.append("WARN: compaction_threshold should be 0.5-0.95")

        # max_turns
        if config.loop.max_turns < 1:
            issues.append("ERROR: max_turns must be >= 1")

        if config.loop.max_turns > 500:
            issues.append("WARN: max_turns > 500 may cause excessive costs")

        # Sandbox
        if not config.sandbox.runtime:
            issues.append("WARN: sandbox runtime not set, defaulting to docker")
        if config.sandbox.runtime == "firecracker" and not self._has_kvm():
            issues.append("WARN: firecracker runtime requires KVM, which is not available")

        # MCP server
        for server_name, server_cfg in config.mcp_servers.items():
            if server_cfg.transport == "sse":
                port = _extract_port(server_cfg.url)
                if port and not 1024 <= port <= 65535:
                    issues.append(f"ERROR: MCP server '{server_name}' port {port} invalid")

        return issues


class ConfigWatcher:
    """
    配置热加载 — 监听 harness.toml 变化, 自动重载。

    使用 watchfiles (Rust-backed, 低开销) 监听文件系统事件。
    变更时:
    1. 重新解析 harness.toml
    2. 验证新配置
    3. 通知所有注册的 listener (通过 asyncio.Event)
    4. 部分配置可以热应用 (如 log_level)
       部分配置需要重启 (如 model, loop engine)

    借鉴 Claude Code 的 config hot-reload 模式。
    """

    def __init__(self, config: "Config", config_path: str):
        self.config = config
        self.config_path = config_path
        self._listeners: list[callable] = []
        self._watcher_task: asyncio.Task | None = None
        self._changed_event = asyncio.Event()

    async def start(self):
        """启动文件监听"""
        from watchfiles import awatch
        self._watcher_task = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self):
        from watchfiles import awatch
        async for changes in awatch(self.config_path):
            for change_type, path in changes:
                if change_type in (1, 2):  # modified or created
                    await self._reload()
            self._changed_event.set()
            self._changed_event.clear()

    async def _reload(self):
        """重新加载配置"""
        try:
            new_config = Config.load(self.config_path)
            issues = ConfigValidator.validate(new_config)
            errors = [i for i in issues if i.startswith("ERROR:")]
            if errors:
                logger.error(f"Config reload blocked due to errors: {errors}")
                return  # 不应用无效配置

            # 三类变更处理
            hot_reloadable = {"log_level", "debug", "cache_warm_interval"}
            requires_restart = {"model", "loop_engine", "provider"}

            old = self.config
            self.config = new_config

            for listener in self._listeners:
                await listener(old, new_config)

        except Exception as e:
            logger.error(f"Config reload failed: {e}")

    def add_listener(self, callback: callable):
        self._listeners.append(callback)
```

---

## 十三、可观测性（Trace-Observation-Score 模型，借鉴 Langfuse + OpenTelemetry）

Harness 的可观测性系统结合 **OpenTelemetry** (分布式追踪标准) 和 **Langfuse** (langfuse/langfuse, YC W23, 开源 LLM 可观测性平台) 的数据模型设计，提供 Traces、Metrics 和 Logs 的统一采集与分析。

### 13.1 数据模型: Trace → Observation → Score

借鉴 Langfuse 的三层嵌套数据模型（自身受 OpenTelemetry 启发并扩展了 LLM 专用字段）：

```
Trace (root container — 一个 Session 或一个 Turn)
├── trace_id, name, user_id, session_id
├── tags, version, release, environment
├── input, output, metadata
├── timestamp, public (可分享链接)
│
├── Observation: Span (有持续时间的工作单元)
│   ├── observation_id, trace_id, parent_observation_id (嵌套)
│   ├── name, start_time, end_time
│   ├── input, output, metadata
│   ├── level (DEBUG/DEFAULT/WARNING/ERROR)
│   ├── status_message
│   ├── version, environment
│   │
│   ├── Observation: Generation (LLM 调用专用 Span)
│   │   ├── model, model_parameters
│   │   ├── usage (input_tokens, output_tokens, total_tokens)
│   │   ├── usage_details (cache_read_tokens, cache_write_tokens, thinking_tokens)
│   │   ├── cost_details (input_cost, output_cost, total_cost)
│   │   ├── prompt_name, prompt_version (关联到 Prompt 管理)
│   │   ├── completion_start_time (计算 TTFT — Time To First Token)
│   │   └── input/output (prompt messages 和 completion)
│   │
│   └── Observation: Event (时间点事件，无持续时间)
│       ├── name, timestamp
│       ├── input, output, metadata
│       └── level
│
└── Score (附加到 Trace、Observation、Session 或 Dataset Run)
    ├── id (可作幂等键), name
    ├── value (数值) 或 string_value (分类/文本)
    ├── data_type (NUMERIC | CATEGORICAL | BOOLEAN | TEXT)
    ├── source (API | EVAL | ANNOTATION)
    ├── comment
    └── trace_id, observation_id, config_id
```

**Naming 约定（对齐 Langfuse / OTel 语义）**:

| Harness 实体 | Langfuse 等价 | OpenTelemetry 等价 |
|-------------|-------------|-------------------|
| Session | Session | Root Span (with session.id attribute) |
| Turn | Trace | Trace |
| LLM Call | Generation | Span (kind=CLIENT, gen_ai.* attributes) |
| Tool Execution | Span | Span (kind=INTERNAL) |
| Compaction | Span | Span (kind=INTERNAL) |
| Error | Event | Event |
| Cache Hit Ratio | Score (NUMERIC) | Gauge |

### 13.2 摄入管线: 异步缓冲架构

借鉴 Langfuse 的 events-first 异步摄入模式，Harness 将事件采集和事件处理解耦：

```
┌───────────────────────────────────────────────────────────────┐
│                   ASYNC INGESTION PIPELINE                      │
│                                                                │
│  Harness Agent ──→ /api/telemetry ──→ S3/MinIO (raw blob)    │
│       │                                    │                   │
│       │  HTTP 207 (ack) ←─────────────────┘                   │
│       │                                                        │
│       │  后台:                                                  │
│       │  S3 Reference ──→ Redis Queue (BullMQ job)             │
│       │       │                                                │
│       │       └──→ Worker (enrich + tokenize + cost)           │
│       │               │                                        │
│       │               ├──→ ClickHouse (OLAP analytics)         │
│       │               └──→ PostgreSQL (ACID metadata)          │
│       │                                                        │
│  ─── 关键设计:                                                  │
│  • Events-first: S3 中的 raw events 是源数据; ClickHouse 是派生视图│
│  • HTTP 207 同步返回 — 不等待处理完成                             │
│  • Redis 作为弹性缓冲 — 流量 spike 不会超时                        │
│  • Worker 可以追赶积压，不会丢数据                                 │
└───────────────────────────────────────────────────────────────┘
```

**双数据库架构**:

| 数据库 | 职责 | 数据类型 |
|--------|------|---------|
| **PostgreSQL** | ACID 元数据（用户、项目、API Keys、Prompt 版本、Eval 配置） | 低容量，关系型 |
| **ClickHouse** | OLAP 分析（Traces、Observations、Scores、Events） | 高基数时序数据，列式存储 |
| **S3/MinIO** | Raw 事件不可变存储 | 源数据，可重建 ClickHouse |

**ClickHouse ReplacingMergeTree**: 支持对不可变列式存储的 upsert 语义 — Observation 在完成前可能多次更新（如 token 计数在 stream 完成后才确定），FINAL 修饰符用于去重。

### 13.3 仪表化金字塔（借鉴 Langfuse 三层集成）

```
Tier 1: 零代码 (Drop-in)
     from harness.llm.anthropic import Anthropic  # 代替 from anthropic import AsyncAnthropic
     # 所有 LLM 调用自动追踪: prompt, response, tokens, latency, cost
     └── 适用: 95% 的用户场景

Tier 2: 装饰器 (Decorator)
     @observe(name="my_agent_flow")
     async def complex_agent_workflow(input):
         # 函数内所有 LLM 调用自动成为子 Generation
         # 返回值自动记录
         ...
     └── 适用: 自定义 Agent 流程, 多步推理链

Tier 3: 手动 (Manual)
     trace = harness.trace(name="my_trace", user_id="...")
     span = trace.span(name="custom_processing")
     generation = span.generation(name="llm_call", model="...", input=..., output=...)
     generation.end()
     span.end()
     harness.flush()
     └── 适用: 完全自定义的仪表化需求
```

**各层集成要求**:

| 层 | LLM 调用 | Tool 执行 | Agent Loop | 自定义代码 |
|----|---------|----------|-----------|----------|
| Tier 1 | 自动 | 自动 | 自动 | 不支持 |
| Tier 2 | 自动 | 自动 | 自动 | 通过装饰器 |
| Tier 3 | 手动 | 手动 | 手动 | 完全控制 |

### 13.4 Prompt 版本管理与 Trace 关联

借鉴 Langfuse 的 Prompt 管理深度链接模式，Harness 将 Prompt 版本与执行 Trace 关联:

- **版本管理**: System Prompt 每次变更自动生成新版本（v1, v2, v3...），标签管理（`production`, `staging`, `latest`）
- **变更检测**: BLAKE3 hash 比较 — string templates 用 UTF-8 编码，chat messages 用 sorted JSON 序列化
- **Trace 关联**: 每次 Generation (LLM 调用) 携带 `prompt_name` 和 `prompt_version` 字段
- **A/B 对比**: 按 prompt 版本筛选 Traces → 比较延迟、成本、评分 → 验证 prompt 改进效果
- **Playground**: 交互式调试环境 — 使用不同模型/参数测试 prompt，与 trace 数据联动
- **回滚**: 标签切换 (`production` → v3) 无需重新部署

**Prompt 缓存架构（多层降级）**:

| 缓存层 | TTL | API 失败时的行为 |
|--------|-----|----------------|
| SDK 内存 | 60s (可配置) | 回退到缓存版本 |
| Redis 服务端 | 可配置 | 回退到数据库查询 |
| Epoch-based 失效 | Prompt 更新时 | 轮转 epoch token → 原子性失效所有缓存 |

### 13.5 评分系统

**评分类型**:

| 类型 | 来源 | 示例 | 附加到 |
|------|------|------|--------|
| API Score | Eval Runner | 评估结果 (FULL/PARTIAL/NO) | Trace (评估 run) |
| Annotation Score | 人工标注 | 代码质量评分 (1-10) | Observation |
| LLM-as-Judge Score | LLM 评分器 | 响应的正确性、安全性评分 | Trace / Generation |
| Auto Score | Harness 系统 | Cache 命中率、Token 效率 | Session |

**ScoreConfig 模式约束**:
- NUMERIC: `min` / `max` 范围约束
- CATEGORICAL: 预定义标签集合
- BOOLEAN: true/false
- TEXT: 自由文本（用于注释性评分）

**评测数据集运行**: 每次评估产生一个 Dataset Run Trace，所有评分附加为 Scores，可在 Dashboard 按模型/配置筛选对比。

### 13.6 可观测性配置

```toml
# harness.toml [observability]

[observability]
backend = "harness"          # "harness" (内置) | "langfuse" (外部 Langfuse 实例) | "none"

# 数据存储 (harness backend)
[observability.storage]
events_bucket = "s3://harness-telemetry/events"  # 或 "file://~/.harness/telemetry/"
clickhouse_url = "http://localhost:8123"
postgres_url = "postgresql://localhost:5432/harness_meta"
redis_url = "redis://localhost:6379"

# 采样策略
[observability.sampling]
error_sample_rate = 1.0         # 错误 100% 采样
success_sample_rate = 0.10      # 成功 10% 采样
slow_threshold_ms = 5000        # >5s 强制采样

# 导出器
[observability.exporters]
otlp_endpoint = "http://localhost:4317"   # OpenTelemetry Collector
console_export = false                     # 终端输出 (debug)
jsonl_export_path = "~/.harness/telemetry/"  # 离线分析

# 保留策略
[observability.retention]
trace_retention_days = 30       # ClickHouse TTL
raw_event_retention_days = 90   # S3 lifecycle
```

### 13.7 启用/禁用

- **全局开关**: `harness.toml` → `[observability] backend = "none"` 完全禁用
- **Per-session**: `--no-telemetry` CLI flag
- **后台任务**: 异步 Worker 进程 — 不阻塞 Agent Loop
- **性能开销**: 仪表化本身 < 1ms/span（采样关闭时 0 开销）
- **隐私**: 所有事件本地处理，不上传到外部服务（除非配置 `backend = "langfuse"` + 自建实例 URL）

---


## 十、测试策略

| 层级 | 工具 | Mock 策略 | 关键断言 |
|------|------|----------|---------|
| **Unit** | `pytest + pytest-asyncio` | `MockDelegate(response_queue)` | 状态机转换正确性, compaction 触发条件 |
| **Integration** | `pytest (+ Docker)` | `TestRig + StubLlm + TraceLlm` | 端到端管线, Docker 沙箱执行, RepoMap 构建 |
| **E2E** | `pytest + VCR.py` | HTTP 录制回放 / 真实 API | 多轮对话, tool use, sub-agent |

**MockDelegate 测试模式**:
```python
class MockDelegate(LoopDelegate):
    """可配置 mock — 用响应队列控制多轮交互"""
    def __init__(self, responses: list[MockLlmResponse]):
        self.responses = responses
        self.call_count = 0
        self.signals_seen = []

    async def call_llm(self, ctx, iter) -> LlmResponse:
        self.call_count += 1
        return self.responses.pop(0).to_response()

# 测试: text response 立即返回
async def test_text_response_returns_immediately():
    delegate = MockDelegate([MockLlmResponse.text("Hello!")])
    outcome = await run_agentic_loop(delegate, ctx(), config())
    assert outcome.kind == "completed"
    assert delegate.call_count == 1

# 测试: tool calls 后跟 text 的两轮交互
async def test_tool_then_text():
    delegate = MockDelegate([
        MockLlmResponse.tool_calls([ToolCall("file_read", {"path": "test.py"})]),
        MockLlmResponse.text("File contents: ..."),
    ])
    outcome = await run_agentic_loop(delegate, ctx(), config())
    assert delegate.call_count == 2

# 测试: 重复失败 5 次触发 force_text
async def test_duplicate_failures_force_text():
    calls = [MockLlmResponse.tool_calls([ToolCall("bad_tool", {})])] * 6
    delegate = MockDelegate(calls)
    outcome = await run_agentic_loop(delegate, ctx(), config())
    assert outcome.force_text_triggered
```

---

## 十一、与 Rust 版的关键差异

| 维度 | Rust 版 | Python 版 |
|------|---------|----------|
| 异步模型 | `tokio` (work-stealing) | `asyncio` (cooperative) |
| 类型系统 | 编译期泛型 + trait | ABC + Pydantic runtime |
| 图算法 | `petgraph` | `networkx` |
| LLM SDK | 自实现 + reqwest | Anthropic/OpenAI 官方 SDK |
| Sandbox 安全模型 | Capability-based (WASM) | 三层防御 (容器加固 + 网络隔离 + 可选 MicroVM) |
| 部署 | `cargo install` 单二进制 | `pip install` / `uv tool install` |
| 开发迭代 | ~5s compile | ~0s (interpreted) |
| 生态复用 | 较少 | 直接复用 Aider 组件 |
| 内存 | ~20MB baseline | ~80MB baseline |
| 冷启动 | ~10ms | ~100ms |

---

## 十二、实施路线图

| Phase | 模块 | 预估 |
|-------|------|------|
| 1 | config + safety + llm + tools (4 子包, ~30 文件) | 2 周 |
| 2 | core (loop + compaction + subagent) + cli (REPL) | 2 周 |
| 3 | repomap (tree-sitter + PageRank + cache) | 1.5 周 |
| 4 | sandbox (Docker + execd/egress sidecars) | 1.5 周 |
| 5 | subagent 完善 + MCP + textual TUI + E2E tests | 2 周 |
| 6 | eval harness (SWE-bench 适配 + 评分管线 + CLI) | 1.5 周 |

---

## 十四、评估系统（Evaluation Harness — SWE-bench 对齐）

Harness 内置评估系统，遵循 **SWE-bench** (Princeton-NLP, ICLR 2024) 标杆的评估方法论，支持标准 SWE-bench 数据集和自定义 benchmark。

### 14.1 评估管线（8 步流程）

借鉴 SWE-bench `swebench.harness.run_evaluation` 的评估架构：

```
EvalRunner.run_benchmark(config, agent_delegate)
    │
    ├── 1. LOAD: 从 HuggingFace / 本地 JSONL 加载实例
    │   └── SWEbenchInstance: instance_id, repo, base_commit, problem_statement,
    │       FAIL_TO_PASS, PASS_TO_PASS, patch (gold), test_patch
    │
    ├── 2. PREPARE: 构建/拉取 Docker 镜像
    │   └── 三层缓存 (参见 14.2)
    │
    ├── FOR EACH INSTANCE (并行, ThreadPoolExecutor):
    │   │
    │   ├── 3. SANDBOX: 从镜像创建 Docker 容器
    │   │   └── 复用 Section 5.3 的三层防御体系
    │   │   └── network=none (评估期间无网络访问)
    │   │   └── read-only rootfs + tmpfs /tmp
    │   │
    │   ├── 4. AGENT-RUN: EvalDelegate → Agent 自主求解
    │   │   └── 输入: problem_statement + base_commit checkout
    │   │   └── 行为: 导航 repo, 定位文件, 编辑代码
    │   │   └── 超时: 默认 1800s / instance
    │   │   └── 无用户交互 (完全自主)
    │   │
    │   ├── 5. PATCH: 提取 git diff
    │   │   └── git diff > prediction.patch
    │   │   └── 验证: git apply --check (patch 可应用性)
    │   │
    │   ├── 6. TEST: 在容器内运行测试套件
    │   │   └── 框架特定命令: pytest, django test, unittest, tox 等
    │   │   └── stdout/stderr → test_output.txt
    │   │
    │   ├── 7. PARSE: 框架特定日志解析器 → per-test 状态
    │   │   └── 参见 14.3
    │   │
    │   ├── 8. GRADE: 比较 gold annotations → Resolution
    │   │   └── 参见 14.4
    │   │
    │   └── 9. CLEANUP: 删除容器, 持久化结果
    │
    └── AGGREGATE: 生成 Report (resolution_rate, f2p_rate, p2p_rate, cost)
```

### 14.2 三层 Docker 镜像缓存

借鉴 SWE-bench 的分层镜像策略，减少 90% 的重建时间：

```
INSTANCE IMAGES    (特定 commit checkout + test patch)
    └── ENVIRONMENT IMAGES   (repo 特定依赖安装)
        └── BASE IMAGES           (OS + Python tooling)
```

**缓存级别**:

| Level | 保留 | 存储量 | 适用场景 |
|-------|------|--------|---------|
| `none` | 全部清除 | 最小 | CI 环境, 磁盘受限 |
| `base` | Base images only | ~20 GB | 开发迭代 |
| `env` | Base + environment images | ~120 GB | 持续评估 |
| `instance` | 全部保留 | ~2 TB+ | 长期分析, 复现研究 |

**CLI 指定**: `harness eval swebench --cache-level env`

### 14.3 框架特定日志解析器

参考 SWE-bench 的 per-repository log parser 设计：

| 解析器 | 测试框架 | 解析方式 |
|--------|---------|---------|
| `parse_pytest` | pytest | 匹配 `FAILED`, `PASSED`, `ERROR` 行 |
| `parse_pytest_v2` | pytest (新) | 处理 ANSI 转义序列, 双向状态匹配 |
| `parse_django` | Django | 多行输出解析 + 特殊 case 处理 |
| `parse_unittest` | unittest | 标准 Python unittest 输出 |
| `parse_tox` | tox | 子进程输出聚合 |
| `parse_sympy` | SymPy | Regex 匹配文件路径 + 单字符状态后缀 |
| `parse_seaborn` | Seaborn | 空格分隔状态指示器 |

所有解析器返回统一的数据结构: `{test_case_name: TestStatus(PASSED|FAILED|ERROR|SKIPPED)}`。

新增自定义解析器只需实现 `LogParser ABC` 并在 `MAP_REPO_TO_PARSER` 中注册。

### 14.4 评分与分级

**测试分类**（借鉴 SWE-bench 的 test annotation 体系）:

| 分类 | 含义 | 评分规则 |
|------|------|---------|
| `FAIL_TO_PASS` | 修复前失败的测试 | **必须全部 PASSED** → 衡量修复能力 |
| `PASS_TO_PASS` | 修复前通过的测试 | **必须全部保持 PASSED** → 衡量回归防护 |
| `FAIL_TO_FAIL` | 预期保持失败的测试 | 可选检查（严格模式） |
| `PASS_TO_FAIL` | 不应新失败的测试 | 任何新增失败 → 回归检测 |

**Resolution 判定**:

| Status | 条件 | 含义 |
|--------|------|------|
| `RESOLVED_FULL` | 所有 FAIL_TO_PASS 通过 + 所有 PASS_TO_PASS 保持 | 完全修复，无回归 |
| `RESOLVED_PARTIAL` | 部分 FAIL_TO_PASS 通过 + 所有 PASS_TO_PASS 保持 | 部分修复 |
| `RESOLVED_NO` | 至少一个 FAIL_TO_PASS 未通过 或 任一 PASS_TO_PASS 失败 | 未修复或引入回归 |

**Fine-grained 指标**:
- `f2p_rate` = FAIL_TO_PASS success / total FAIL_TO_PASS
- `p2p_rate` = PASS_TO_PASS success / total PASS_TO_PASS
- `cost_per_resolution` = total_tokens_cost / RESOLVED_FULL instances

### 14.5 Prediction 文件约定

与 SWE-bench 兼容的 JSONL 格式，并扩展 Harness 元数据:

```jsonl
{"instance_id": "django__django-10097", "model_patch": "diff --git ...", "model_name_or_path": "harness-agent", "model_config": {"model": "claude-sonnet-4-6", "max_turns": 100}, "wall_time_seconds": 234.5, "tokens_used": {"input": 50000, "output": 3000}}
```

**SWE-bench 兼容字段**: `instance_id`, `model_patch`, `model_name_or_path`
**Harness 扩展字段**: `model_config`, `wall_time_seconds`, `tokens_used`

### 14.6 EvalDelegate: 评估模式专用 Delegate

`EvalDelegate` 是 LoopDelegate 的评估模式实现（参见 Section 3.2 的 Delegate 策略模式）：

| 特性 | ChatDelegate (交互) | EvalDelegate (评估) |
|------|-------------------|-------------------|
| 用户交互 | 允许 (审批, 提问) | 完全自主 (无交互) |
| 工作目录 | 本地 workspace | Docker 容器内 checkout |
| 工具白名单 | 全部启用 | bash_exec + file_read + file_edit + file_write + grep_search + glob_search |
| 网络访问 | 允许 (web_fetch/search) | 禁止 (network=none) |
| 超时 | 无硬限制 (per turn) | 硬限制 (默认 1800s) |
| 产出 | 对话回复 | `.patch` 文件 |
| 审批 | 按工具权限 | 全部自动批准 |

### 14.7 CLI 集成

```bash
# SWE-bench 标准评估
harness eval swebench \
    --dataset princeton-nlp/SWE-bench_Lite \
    --model claude-sonnet-4-6 \
    --max-workers 8 \
    --cache-level env \
    --timeout 1800

# 自定义 benchmark
harness eval custom \
    --dataset ./my-benchmark/instances.jsonl \
    --dockerfile-dir ./my-benchmark/dockerfiles \
    --max-workers 4

# 断点续传
harness eval resume --run-id my_run_2026

# 生成报告
harness eval report --run-id my_run --format markdown --output REPORT.md

# 仅评分 (已有 predictions, 跳过 Agent 运行)
harness eval grade \
    --dataset princeton-nlp/SWE-bench_Lite \
    --predictions predictions.jsonl
```

### 14.8 可复现性保证

- **Docker 镜像固定**: 按 digest (SHA256) 引用镜像, 而非 tag
- **配置 hash**: 完整的 config hash 记录在 evaluation report 中
- **确定性评分**: 相同 predictions + 相同 dataset → 相同 score（log parser 确定性, 无 LLM 参与评分）
- **审计轨迹**: 每个 instance 的完整日志保存在 `logs/{run_id}/{instance_id}/`: `run_instance.log`, `patch.diff`, `eval.sh`, `test_output.txt`, `report.json`

### 14.9 成本追踪

每次评估运行记录:
- Per-instance: `tokens_used`, `llm_calls`, `duration_seconds`
- Per-run: `total_cost_usd`, `cost_per_resolution` (总成本 / 完全修复数)
- 指标暴露为 OpenTelemetry Scores (参见 Section 13.5), 可在 Dashboard 按模型/配置筛选对比
