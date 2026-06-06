# Credit-Assignment Framework

基于 Li et al. (2026) **"Who Gets the Credit? Prompt, Structural, and Memory Context Optimization for Agent Harnesses"** 论文，Harness 内置了一套 credit-assignment 信号发射与日志系统，让每次 agent 任务执行都能被系统性地归因分析。

## 核心概念

### 论文的 Thesis

AI Agent 的上下文优化问题可以统一为一个 **credit-assignment 问题**：

```
Context 状态 C = {P, S, M}

P (Prompt context)     — 语义控制：指令、示例、推理模式
S (Structural context)  — 编排结构：角色、工作流、路由、工具
M (Memory context)     — 运行时状态：持久化、检索、压缩
```

P、S、M 不是三个独立的研究方向，而是**同一个 credit-assignment 问题在 runtime context 上的三个投影**，由 harness（运行时层）协调——**harness 是发出反馈信号的层**，正是这些信号让 credit assignment 成为可能。

### 两轴分类法

**轴 1: 上下文变量（谁被改变？）**

| 标签 | 含义 | Harness 中的对应 |
|------|------|-----------------|
| `P` | Prompt context | system prompt、ContextGatherer 组装的上下文 |
| `S` | Structural context | 工具注册表、agent 路由、loop 结构 |
| `M` | Memory context | MemoryStore、compaction、跨 session 持久化 |

**轴 2: 反馈信号粒度（改变基于什么信息？）**

| 等级 | 含义 | Harness 中的发射源 | 可支持的优化方法 |
|------|------|-------------------|-----------------|
| **G0** | Outcome-only scalar | `task_end`：pass/fail、turns、token 总数 | 随机搜索、贝叶斯优化 |
| **G1** | Process-level text | 每个 tool call 的输入/输出、LLM 响应的文本 | 文本诊断、self-reflection、DSPy 风格优化 |
| **G2** | Component-attributed | 具体 tool 名称 + 成功/失败 + 错误信息 | 组件级策略梯度、工具选择优化 |
| **G3** | Cross-dimensional harness signal | compaction 事件、retry 事件、safety 拦截 | 端到端 harness 参数联合优化 |

**关键递进关系**: G0 ⊂ G1 ⊂ G2 ⊂ G3 —— 越高级的信号包含越多上下文信息，能支撑越精细的优化。

## 架构设计

### 信号发射链路

```
AgenticLoop.run()
  │
  ├─ call_llm() ──────► LoopEvent(kind="thinking")  → G1 / P
  │   └─ retry        → LoopEvent(kind="retry")     → G3 / M
  │                    → TaskLogger.log_attribution()
  │
  ├─ execute_tool_calls()
  │   ├─ tc.name      → LoopEvent(kind="tool_call")     → G2 / S
  │   ├─ read tools   → LoopEvent(kind="tool_result")   → G1 / S
  │   ├─ write tools  → LoopEvent(kind="tool_result")   → G2 / S
  │   └─ errors       → LoopEvent(kind="tool_result", error=True) → G2 / S
  │                    → TaskLogger.log_attribution()
  │
  ├─ _auto_compact()
  │   ├─ MICRO (>80% tokens)   → LoopEvent(kind="compact") → G3 / M
  │   └─ REACTIVE (>90% tokens)→ LoopEvent(kind="compact") → G3 / M
  │   └─ TaskLogger.log_compaction()
  │
  └─ done            → LoopEvent(kind="done") → G0 / P or S
```

### 数据流

```
LoopEvent (dataclass, 内存)
  ├─ signal_granularity: SignalGranularity enum
  └─ attribution: AttributionDimension enum
        │
        ▼  (emit 到 CLI 实时展示)
        │
        ▼  (同时写入磁盘)
TaskLogger
  ├─ log_attribution()   → JSONL: "attribution" event
  ├─ log_compaction()    → JSONL: "compaction" event
  └─ log_event_summary() → JSONL: "event_summary" event (session 结束时汇总)
        │
        ▼
logs/<session_id>.jsonl
        │
        ▼
scripts/analyze_attribution.py
  → 解析 → P/S/M × G0–G3 交叉报表
```

### JSONL 事件格式

**attribution 事件**:

```json
{
  "timestamp": "2026-06-06T14:30:00+00:00",
  "session_id": "abc123",
  "event": "attribution",
  "dimension": "S",
  "granularity": "G2",
  "event_kind": "tool_result",
  "tool_name": "bash_exec",
  "iteration": 5,
  "detail": "Tool executed successfully"
}
```

**compaction 事件** (G3 子类型):

```json
{
  "timestamp": "2026-06-06T14:31:00+00:00",
  "session_id": "abc123",
  "event": "compaction",
  "strategy": "micro",
  "tokens_before": 150000,
  "tokens_after": 120000,
  "truncated_count": 15,
  "iteration": 12,
  "dimension": "M",
  "granularity": "G3"
}
```

**event_summary 事件** (session 结束时的汇总快照):

```json
{
  "event": "event_summary",
  "p_count": 1,
  "s_count": 25,
  "m_count": 3,
  "g0_count": 1,
  "g1_count": 20,
  "g2_count": 6,
  "g3_count": 3
}
```

## 代码接口

### LoopEvent

```python
from harness.core.loop import LoopEvent, SignalGranularity, AttributionDimension

# 所有 LoopEvent 实例都携带这两个字段
ev = LoopEvent(
    kind="tool_result",
    tool_name="file_write",
    signal_granularity=SignalGranularity.G2,
    attribution=AttributionDimension.STRUCTURAL,
)
```

### TaskLogger

```python
from harness.logging.task_logger import TaskLogger

tl = TaskLogger(session_id="my-session")

# 在 agent loop 中自动调用，也可以手动调用：
tl.log_attribution(
    dimension="S",          # P | S | M
    granularity="G2",       # G0 | G1 | G2 | G3
    event_kind="tool_result",
    tool_name="bash_exec",
    iteration=5,
    detail="Tests failed: 3/42",
)

# compaction 事件（G3 跨维度信号）
tl.log_compaction(
    strategy="micro",
    tokens_before=150000,
    tokens_after=120000,
    truncated_count=15,
    iteration=12,
)

# session 结束时的汇总（可选，加速分析）
tl.log_event_summary(
    p_count=1, s_count=25, m_count=3,
    g0_count=1, g1_count=20, g2_count=6, g3_count=3,
)
```

### 分析脚本

```bash
# 分析所有 session
python scripts/analyze_attribution.py logs/

# 单个 session 详细报告
python scripts/analyze_attribution.py logs/abc123.jsonl

# 只看跨 session 汇总
python scripts/analyze_attribution.py --summary-only

# JSON 输出（供下游工具消费）
python scripts/analyze_attribution.py logs/ --json > attribution_report.json

# 远程分析多个 log 目录
python scripts/analyze_attribution.py path/to/logs1/ path/to/logs2/
```

示例输出：

```
──────────────────────────────────────────────────────────────────
  Session: abc123
  File:   logs/abc123.jsonl
──────────────────────────────────────────────────────────────────

  Outcome:    completed
  Turns:      8
  Tokens:     12,345
  Events:     35

  ┌─ Context Dimension (who gets the credit?) ─┐
  │ P (Prompt)      ██████░░░░░░░░░░░░░░░░░░░░░░  15.0% (3)
  │ S (Structure)   ██████████████████████████████  75.0% (27)
  │ M (Memory)      ████░░░░░░░░░░░░░░░░░░░░░░░░░  10.0% (5)
  └──────────────────────────────────────────────┘

  ┌─ Feedback Granularity ───────────────────────┐
  │ G0 (Outcome scalar)    ██░░░░░░░░░░░░░░░░░░░░   5.0% (1)
  │ G1 (Process text)      ████████████████████░░  50.0% (18)
  │ G2 (Component-attrib)  ████████████░░░░░░░░░░  30.0% (11)
  │ G3 (Harness cross-dim) ██████░░░░░░░░░░░░░░░░  15.0% (6)
  └──────────────────────────────────────────────┘
```

## 测试

```bash
# 运行 attribution 专项测试 (26 tests)
uv run pytest tests/test_attribution.py -v

# 包括的测试范围：
# - SignalGranularity / AttributionDimension 枚举正确性
# - LoopEvent 默认值与字段独立性
# - TaskLogger 三种新方法的 JSONL 发射
# - 模拟完整 session 的归因记录
# - analyze_attribution.py 解析逻辑
# - 空日志、损坏日志的容错
```

## 实验设计思路

基于这套 infrastructure，可以做以下实验：

### 实验 1: 建立基准反馈信号分布

```bash
# 跑 30 个编程任务，收集归因数据
for task in tasks/*.txt; do
    harness run "$(cat $task)" --config baseline.toml
done

# 分析：查看 P/S/M 分布和 G0-G3 比例
python scripts/analyze_attribution.py logs/
```

### 实验 2: P/S/M 独立 vs 联合优化的对比

通过配置切换维度组合，比较每种组合下的信号分布和任务成功率：

| 配置 | P | S | M |
|------|---|---|---|
| baseline | 默认 prompt | native loop | 无持久化 |
| P only | 优化 prompt | native loop | 无持久化 |
| S only | 默认 prompt | multi_agent | 无持久化 |
| P+S+M | 优化 prompt | multi_agent | memory on |

```bash
harness run "task" -c configs/baseline.toml    → logs/baseline/*.jsonl
harness run "task" -c configs/p_only.toml      → logs/p_only/*.jsonl
harness run "task" -c configs/s_only.toml      → logs/s_only/*.jsonl
harness run "task" -c configs/p_s_m.toml       → logs/p_s_m/*.jsonl

python scripts/analyze_attribution.py logs/baseline/
python scripts/analyze_attribution.py logs/p_only/
# ... 对比 G2 和 G3 信号的增加量与成功率的相关性
```

### 实验 3: Feedback Granularity 对优化效率的影响

```bash
# 用不同的 agent 配置跑同一个任务，比较 G0-G3 分布
# G0-only: 只记录结果
# G1: 记录 step 级日志
# G2+: 启用 attribution（当前已默认启用）

# 断言：G3 信号密度越高的配置，归因精度越高，二次优化越有效
```

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/harness/core/loop.py` | `SignalGranularity`、`AttributionDimension` 枚举定义，`LoopEvent` 信号标签 |
| `src/harness/logging/task_logger.py` | `log_attribution()`、`log_compaction()`、`log_event_summary()` |
| `scripts/analyze_attribution.py` | JSONL 解析与跨 session 归因分析 |
| `tests/test_attribution.py` | 26 个测试覆盖全链路 |
| `ATTRIBUTION.md` | 本文档 |
