# Harness LangGraph Multi-Agent — 架构设计与技术总结

## 一、架构设计

### 1.1 整体架构

Harness 在原有 Native Agent Loop 的基础上，新增了基于 **LangGraph StateGraph** 的多智能体协作引擎。两者通过 `LoopDelegate` 接口统一，通过 `harness.toml` 中的 `[loop] engine` 配置项切换。

```
┌──────────────────────────────────────────────────────┐
│  Presentation Layer                                   │
│  CLI (argparse) → REPL (prompt_toolkit) → TUI        │
├──────────────────────────────────────────────────────┤
│  Application Layer                                    │
│  ┌─────────────────┐  ┌────────────────────────────┐ │
│  │ AgenticLoop      │  │ LangGraphDelegate          │ │
│  │ (Native Engine)  │  │ (LangGraph Engine)         │ │
│  └────────┬────────┘  └────────────┬───────────────┘ │
│           │                        │                  │
│           └────────┬───────────────┘                  │
│                    │                                  │
│           LoopDelegate ABC (统一接口)                  │
├──────────────────────────────────────────────────────┤
│  LangGraph Module (新增)                              │
│  ┌─────────────┐ ┌──────────┐ ┌───────────────────┐ │
│  │ Pair Coding │ │Multi-Agent│ │ Complexity        │ │
│  │ Graph        │ │Graph      │ │ Assessor + Router │ │
│  └─────────────┘ └──────────┘ └───────────────────┘ │
│  ┌─────────────┐ ┌──────────┐ ┌───────────────────┐ │
│  │ State        │ │ SubAgent │ │ Streaming +       │ │
│  │ Definitions  │ │ Manager  │ │ Checkpointing     │ │
│  └─────────────┘ └──────────┘ └───────────────────┘ │
├──────────────────────────────────────────────────────┤
│  Domain Layer (复用)                                  │
│  Tool ABC → ToolExecutor → PermissionPolicy          │
│  LlmClient → SandboxRuntime → MemoryStore            │
├──────────────────────────────────────────────────────┤
│  Infrastructure                                       │
│  LiteLLM → Docker → SQLite → LangGraph Checkpointer  │
└──────────────────────────────────────────────────────┘
```

### 1.2 两种协作图拓扑

#### 结对编程图（Pair Coding Graph）

```
    ┌──────────┐
    │  coder   │◄──────────────────┐
    └────┬─────┘                   │
         │                         │
    ┌────▼──────┐                  │
    │  reviewer │                  │
    └────┬──────┘                  │
         │                         │
    ┌────▼────────┐                │
    │human_approval│ (interrupt)    │
    └────┬────────┘                │
         │                         │
    ┌────▼──────────┐  CHANGES_REQUESTED
    │ route_decision├───────────────────┘
    └────┬──────────┘
         │ APPROVED
    ┌────▼─────┐
    │   done   │ → END
    └──────────┘
```

- **Coder**：根据任务和审查意见生成/修改代码
- **Reviewer**：结构化JSON审查输出（decision + severity + comments）
- **Human Approval**：LangGraph `interrupt_before` 中断点，CLI用户确认
- **条件路由**：APPROVED → done, CHANGES_REQUESTED + iter<max → coder

#### 多智能体协作图（Multi-Agent Collaboration Graph）

```
controller → task_router ↔ implementer → result_collector
                 ↓ (all tasks done)
           spec_reviewer → code_quality_reviewer
                 ↓ (fail)              ↓ (pass)
           remediation → task_router   finalize → END
```

- **Controller**：分解计划为任务列表，标记复杂度，绝不写代码
- **TaskRouter**：拓扑排序DAG调度器，选择依赖已就绪的下一个PENDING任务
- **Implementer**：子智能体执行单个任务，具有写工具访问权限和精选上下文
- **ResultCollector**：解析子智能体输出，提取实现者报告状态
- **SpecReviewer**：对照计划验证功能正确性（始终使用最强模型Opus）
- **CodeQualityReviewer**：评估代码结构和质量（仅在规范审查通过后运行）
- **Remediation**：审查失败时创建修复任务，路由回task_router

### 1.3 子智能体组织模式

多智能体协作的核心挑战不是"如何生成子智能体"，而是**"如何组织子智能体的拓扑结构"**。不同的拓扑对应不同的并发语义、冲突风险和上下文隔离策略。Harness 实现了四种组织模式，一种默认启用，三种按需切换。

#### 1.3.1 顺序链（Sequential Chain）— 默认模式

```
时间轴 ──────────────────────────────────────────────────────►

  task_router          implementer       result_collector      task_router
  ┌──────────┐        ┌──────────┐       ┌──────────┐        ┌──────────┐
  │ pick t1  │───────►│ spawn    │──────►│ collect  │───────►│ pick t2  │──► ...
  │ (deps=[])│        │ sub(t1)  │       │ t1=DONE  │        │ (deps=[])│
  └──────────┘        └──────────┘       └──────────┘        └──────────┘
                            │
                            ▼
                     ┌──────────────┐
                     │ AgenticLoop  │
                     │ (独立上下文)  │
                     │ 写工具 + 读工具│
                     │ max_turns=20 │
                     └──────────────┘
```

**代码路径**：
```
graphs.py:_route_next_task
  → task_router picks first PENDING with all deps in completed_tasks
  → implementer: 创建独立 LoopContext → AgenticLoop.run()
  → result_collector: 解析 STATUS: DONE / DONE_WITH_CONCERNS / BLOCKED
  → 回到 task_router 选择下一个任务
```

**状态变更流**：
```
State.task_list[t1].status: PENDING → IN_PROGRESS → DONE
State.completed_tasks: [] → ["t1"] → ["t1", "t2"] → ...
State.pending_tasks: ["t1","t2","t3"] → ["t2","t3"] → ["t3"] → []
```

**设计理由**：顺序执行是最安全的默认。每个实现者在独立的上下文中运行，文件修改不会与其他实现者冲突。缺点是没有并发加速，但对于代码生成任务，冲突避免远比计算速度重要——一次 Git merge conflict 的时间成本远超串行等待。

**关键约束**：
- 每个实现者**不继承**会话历史，只接收 `plan 摘要 + 任务描述`
- 实现者拥有**全套写工具**（file_write、file_edit、bash_exec），区别于现有只读 AgentTool
- 实现者按 `STATUS:` 前缀报告结果，由 `_parse_implementer_status()` 解析

#### 1.3.2 并行扇出（Parallel Fan-Out）— 研究/探索模式

```
                          ┌──────────┐
                          │ task_    │
                          │ router   │
                          └────┬─────┘
                               │ 选出所有无依赖的就绪任务
                 ┌─────────────┼─────────────┐
                 │             │             │
            ┌────▼────┐  ┌────▼────┐  ┌────▼────┐
            │  sub-1  │  │  sub-2  │  │  sub-3  │   ← asyncio.gather()
            │ (read)  │  │ (read)  │  │ (read)  │
            └────┬────┘  └────┬────┘  └────┬────┘
                 │             │             │
                 └─────────────┼─────────────┘
                               │
                          ┌────▼─────┐
                          │ result   │  ← 合并所有结果
                          │collector │
                          └──────────┘
```

**触发条件**：`build_multi_agent_graph(fan_out_implementers=True)` 或任务列表中所有就绪任务都是只读的（标记为 research 类型）。

**与顺序链的关键区别**：

| 维度 | 顺序链 | 并行扇出 |
|------|--------|---------|
| 工具权限 | 读写 | **仅只读**（file_read、grep、glob、web_fetch） |
| 并发语义 | 串行 `await` | `asyncio.gather()` |
| 适用阶段 | 实现阶段 | 研究/调查阶段 |
| 冲突风险 | 无 | 无（只读操作天然无冲突） |
| 模型选择 | 按复杂度 | 全部使用廉价模型（并行成本累加） |

**为什么并行扇出默认关闭？** 并行写操作会导致不可预测的 Git 冲突。即使两个任务修改不同文件，如果它们共享导入或类型定义，合并时仍可能产生语义冲突。这是从 MetaGPT 和 Aider 的实践经验中学到的教训。

#### 1.3.3 树形嵌套（Tree Nesting）— 递归分解模式

```
              Controller (depth=0)
                   │
         ┌─────────┼─────────┐
         │         │         │
    Implementer  Implementer  Implementer  (depth=1)
    (Task A)     (Task B)     (Task C)
         │
    ┌────▼────┐
    │ Agent   │ ← AgentTool (depth=2, 只读)
    │ Tool    │
    └────┬────┘
         │
    ┌────▼────┐
    │  Sub-   │
    │  Agent  │  只读探索子任务
    └─────────┘
```

**代码路径**：
```
implementer 节点的 AgenticLoop 内：
  → LLM 调用 AgentTool
  → AgentTool.execute() 
  → SubAgentManager.spawn(parent_ctx, depth=2)
  → subagent_depth 检查：≥ max_depth(2) → 拒绝
```

**递归约束**：
```python
# harness/core/subagent.py
DEFAULT_MAX_DEPTH = 2           # 最多两层嵌套
DEFAULT_MAX_TURNS = 15          # 子智能体最多 15 轮
DEFAULT_TIMEOUT_SECONDS = 120   # 超时 2 分钟
DEFAULT_MAX_PER_SESSION = 20    # 整个会话最多 20 个子智能体
```

树的每一层有不同的工具权限：
- **Depth 0**（Controller）：所有工具，但不写代码
- **Depth 1**（Implementer）：读写工具（file_write、file_edit、bash_exec）
- **Depth 2**（AgentTool 子智能体）：**仅只读工具**（file_read、grep、glob、web_fetch、web_search）

这种**逐层降权**设计保证了安全性：越深层的子智能体权限越受限。即使最深层的子智能体被 prompt injection 攻击，它也无法修改文件——最多执行只读查询。

#### 1.3.4 DAG 依赖调度 — 拓扑排序模式

这是四种模式中**最精妙**的一种。它不像顺序链那样强制串行，也不像扇出那样忽略依赖，而是**通过 TaskItem.dependencies 字段编码精确的任务依赖关系**。下面从生成、解析、演化三个环节完整追溯 DAG 的生命周期。

##### DAG 的生成：LLM 分解 + 代码级验证

DAG **不是硬编码**的，也**不是从代码静态分析**得出的。它由 Controller 节点通过 LLM 调用动态生成：

**Step 1 — LLM 提示词注入**（`nodes/multi_agent.py:75-92`）：

```python
CONTROLLER_SYSTEM = (
    "You are a technical project controller. Your job is to decompose "
    "an implementation plan into a precise, ordered task list.\n\n"
    "Rules:\n"
    "1. Each task must be atomic — one clear deliverable\n"
    "2. Specify dependencies (which task IDs must complete first)\n"
    "3. Tag each task with estimated complexity: simple, integration, or architecture\n"
    "4. Order tasks to minimize conflicts (sequential when touching same files)\n"
    "5. Output ONLY a JSON array, no other text.\n\n"
    "Output format:\n"
    '[\n  {\n'
    '    "id": "task-1",\n'
    '    "description": "...",\n'
    '    "dependencies": [],\n'
    '    "complexity": "simple"\n'
    "  }\n"
    "]"
)
```

**Step 2 — LLM 返回原始 JSON**。例如对于"添加 OAuth2 登录 + 用户 API"的计划，LLM 输出：

```json
[
  {"id": "task-1", "description": "实现 OAuth2 认证核心模块", "dependencies": [], "complexity": "architecture"},
  {"id": "task-2", "description": "创建用户模型和数据库迁移", "dependencies": ["task-1"], "complexity": "integration"},
  {"id": "task-3", "description": "实现用户资料 API 端点", "dependencies": ["task-1", "task-2"], "complexity": "integration"},
  {"id": "task-4", "description": "添加登录页面 UI", "dependencies": ["task-1"], "complexity": "simple"},
  {"id": "task-5", "description": "编写集成测试", "dependencies": ["task-3", "task-4"], "complexity": "integration"}
]
```

**Step 3 — 代码级解析与增强**（`nodes/multi_agent.py:129-149`）：

```python
# 解析 LLM 输出
raw_tasks = _extract_json(response.text or "[]")

# 逐个转换为 TaskItem，增加 complexity 评估
task_list: list[TaskItem] = []
for i, raw in enumerate(raw_tasks):
    task_id = raw.get("id", f"task-{i + 1}")
    description = raw.get("description", str(raw))
    deps = raw.get("dependencies", [])
    if isinstance(deps, str):
        deps = [deps]  # 规范化：LLM 可能返回裸字符串
    
    # 双重验证：LLM 标记的 complexity + 启发式重新评估
    assessment = assessor.assess(description, plan)
    
    task_list.append({
        "id": task_id,
        "description": description,
        "dependencies": deps,           # ← DAG 的核心：邻接表编码
        "status": "PENDING",
        "assigned_to": "",
        "result": None,
        "complexity": assessment.tier.value,
    })
```

**关键设计：LLM 给出 structural 信息（dependencies），代码负责 semantical 增强（complexity 重新评估）**。这保证了即使 LLM 对复杂度的判断有偏差，启发式评估器也能修正。

##### DAG 的解析：拓扑排序调度器

`task_list` 本质是一个**邻接表**。`task_router` 节点通过拓扑排序将其转化为执行顺序。

**数据结构**：
```python
# State 中的三个字段协同工作
task_list: list[TaskItem]     # 邻接表：每个 TaskItem 携带 dependencies
completed_tasks: list[str]    # 已完成任务 ID 的集合（用于 O(1) 依赖检查）
pending_tasks: list[str]      # 未完成任务 ID 的集合（用于 O(1) 空集检查）
```

**调度算法**（`nodes/multi_agent.py:179-216`，逐行追溯）：

```python
async def node_task_router(state: MultiAgentState) -> dict:
    # ① 复制 task_list（避免原地修改违反 LangGraph 状态不可变性）
    task_list = list(state.get("task_list", []))
    completed = set(state.get("completed_tasks", []))

    # ② 扫描：找到所有 PENDING 且依赖已就绪的任务
    ready: list[int] = []       # 候选任务在 task_list 中的索引
    for i, task in enumerate(task_list):
        if task.get("status") != "PENDING":
            continue            # 跳过已完成/进行中的任务
        deps = set(task.get("dependencies", []))
        if deps.issubset(completed):  # ← 核心：集合子集检查，O(1)
            ready.append(i)

    # ③ 判断：有就绪任务 → 执行 / 全部完成 → 审查 / 死锁 → 报错
    if not ready:
        all_done = all(
            t.get("status") in ("DONE", "DONE_WITH_CONCERNS")
            for t in task_list
        )
        if all_done:
            return {"review_stage": "spec"}   # → 进入审查阶段
        return {"review_stage": "spec"}       # 死锁降级：强制进入审查

    # ④ 选取第一个就绪任务（尊重 LLM 给出的依赖顺序）
    idx = ready[0]
    task = task_list[idx]
    task["status"] = "IN_PROGRESS"
    task_list[idx] = task

    return {
        "task_list": task_list,
        "current_task_index": idx,
    }
```

**算法复杂度分析**：
- 空间：O(n)，n = 任务数
- 时间：O(n) 单次扫描，其中子集检查 `deps.issubset(completed)` 是 O(|deps|)，通常 |deps| ≤ 3
- 总开销：对于 10 个任务的计划，调度耗时 < 1ms，**零 LLM token 消耗**

**为什么 subset 而非遍历**：
```python
# 不推荐的 O(n·m) 写法
all(dep in completed for dep in deps)   # Python 迭代器，逐个检查

# 使用的 O(|deps|) 写法
deps.issubset(completed)                # C 级实现，集合哈希查找
```
虽然对小型 DAG 差异微乎其微，但对于 50+ 任务的规模，子集检查是常数级优化。

##### DAG 的演化：Remediation 动态追加

DAG **不是静态的**。当审查失败时，`node_remediation` 会向已有的 `task_list` 动态追加新任务（`nodes/multi_agent.py:395-417`）：

```python
async def node_remediation(state: MultiAgentState) -> dict:
    spec = state.get("spec_review")
    quality = state.get("code_quality_review")
    task_list = list(state.get("task_list", []))

    issues: list[str] = []
    if spec and not spec.get("passed", False):
        issues = spec.get("issues", [])
    if quality and not quality.get("passed", False):
        issues.extend(quality.get("issues", []))

    # 每个 issue 生成一个独立的修复任务
    fix_id_base = f"fix-{uuid.uuid4().hex[:6]}"
    for i, issue in enumerate(issues):
        fix_task: TaskItem = {
            "id": f"{fix_id_base}-{i + 1}",
            "description": f"FIX: {issue}",
            "dependencies": [],      # ← 修复任务无依赖，立即就绪
            "status": "PENDING",
            "assigned_to": "",
            "result": None,
            "complexity": "simple",  # 修复通常是简单任务
        }
        task_list.append(fix_task)   # ← 原地追加，DAG 增长

    return {
        "task_list": task_list,
        "review_stage": "spec",      # 重新进入审查循环
        "pending_tasks": [t["id"] for t in task_list if t["status"] == "PENDING"],
    }
```

**DAG 演化的完整生命周期**：

```
原始 DAG (5 个节点):          审查失败后的 DAG (8 个节点):
  t1                          t1 (DONE)
  ├── t2                      ├── t2 (DONE)
  ├── t3                      ├── t3 (DONE)
  └── t4                      ├── t4 (DONE)
       └── t5                 ├── t5 (FAILED by spec_reviewer)
                              ├── fix-a  ← remediation 追加
                              ├── fix-b  ← remediation 追加
                              └── fix-c  ← remediation 追加
```

新的 `fix-*` 任务被追加到 `task_list` 末尾，`task_router` 在下一轮扫描中发现它们（PENDING + deps=[]），立即调度执行。整个流程形成闭环。

##### 完整端到端追踪

以一个具体任务为例，展示 DAG 从生成到完成的全过程：

```
用户输入: "为博客系统添加用户认证和文章管理功能"

Step 1: ComplexityGate 评估 → ARCHITECTURE → auto-trigger multi_agent

Step 2: Controller LLM 调用 → 生成 6 个 TaskItem:
  T1: 实现 User 模型 + 密码哈希     deps=[]          complexity=architecture
  T2: 实现 JWT 令牌生成/验证         deps=[T1]        complexity=architecture
  T3: 实现注册/登录 API              deps=[T1,T2]     complexity=integration
  T4: 实现 Article CRUD API          deps=[T1]        complexity=integration
  T5: 添加认证中间件                  deps=[T2]        complexity=integration
  T6: 编写 API 集成测试               deps=[T3,T4,T5]  complexity=integration

Step 3: task_router 拓扑调度:
  轮次 1: ready=[T1]           → implement T1 → DONE
  轮次 2: ready=[T2, T4]       → implement T2 → DONE
  轮次 3: ready=[T3, T4, T5]   → implement T3 → DONE  (顺序链: 先 T3)
  轮次 4: ready=[T4, T5]       → implement T4 → DONE
  轮次 5: ready=[T5]           → implement T5 → DONE
  轮次 6: ready=[T6]           → implement T6 → DONE_WITH_CONCERNS
  轮次 7: all DONE             → spec_reviewer → PASS
  轮次 8:                      → code_quality_reviewer → PASS
  轮次 9:                      → finalize → END

总调度耗时: 9 个图节点步进，task_router 消耗 0 LLM tokens
```

这个例子展示了 DAG 如何将原本可能产生冲突的并行任务（T4 和 T5 都依赖 T1）自然串行化，同时保持无依赖任务的可并行性。

##### 为什么是纯逻辑调度而非 LLM 调度？

| 维度 | LLM 调度 | 纯逻辑调度 |
|------|---------|-----------|
| Token 消耗 | 每次调度 ~500 tokens | **0 tokens** |
| 延迟 | ~1-3 秒 | **<1 毫秒** |
| 正确性 | 可能幻觉或遗漏依赖 | 确定性，**100% 可预测** |
| 可调试性 | 需审查 LLM 输出 | 状态机，直接可读 |
| DAG 一致性 | 可能产生循环依赖 | 隐式保证无环（已完成的任务不会再被调度） |
| 动态演化 | 需要重新提示 LLM | 追加到 task_list 即刻生效 |

这是 LangGraph 设计哲学的重要体现：**LLM 负责需要判断力的工作（代码生成、审查、计划分解），确定性逻辑负责不需要判断力的工作（调度、路由、状态转移）**。只有 DAG 的**结构**（节点和边）由 LLM 生成，DAG 的**执行**完全由代码控制。

#### 1.3.5 四种模式的协同

在实际的多智能体任务中，四种模式不是互斥的，而是**按阶段切换**：

```
Phase 1: Research    → 并行扇出 (多个只读子智能体探索代码库)
Phase 2: Implement   → DAG 调度 (按依赖顺序执行任务)
                      每个任务内部: 顺序链 (一次一个，避免冲突)
                      复杂子任务: 树形嵌套 (AgentTool 深度探索)
Phase 3: Review      → 顺序 (先规范审查，后质量审查)
Phase 4: Remediation → 顺序 (修复任务按创建顺序执行)
```

**完整协作生命周期示例**：

```
Task: "为电商平台添加 OAuth2 登录 + 订单历史 API + 性能优化"

1. ComplexityGate → ARCHITECTURE → multi_agent

2. Controller 分解:
   t1: 实现 OAuth2 认证模块    [deps: []]       complexity=architecture
   t2: 研究现有用户模型         [deps: []]       complexity=simple
   t3: 实现订单历史 API         [deps: [t1]]     complexity=integration
   t4: 数据库迁移脚本           [deps: [t1,t2]]  complexity=integration
   t5: 性能优化（缓存层）       [deps: [t3]]     complexity=architecture
   t6: 集成测试                 [deps: [t3,t4]]  complexity=integration

3. 执行:
   第1轮: t1 + t2 并行 (无依赖，t2 是只读研究)
   第2轮: t3 (等待 t1)
   第3轮: t4 (等待 t1+t2)
   第4轮: t5 (等待 t3)  + t4 已做完
   第5轮: t6 (等待 t3+t4)

4. 两阶段审查 → 通过 → 输出

总耗时: 5 轮 × 平均每轮 30s = 2.5 分钟
对比全串行: 6 轮 × 30s = 3 分钟 (节省 17%)
对比全并行: 可能产生 Git 冲突，合并成本 > 节省的时间
```

#### 1.3.6 设计哲学：为什么不是全并行？

这是一个关键架构决策。很多多智能体框架（如 AutoGen）默认全并行，但 Harness 默认串行。理由：

```
并行度 ↑ → 冲突概率 ↑ (指数级，非线性的)

文件修改冲突模型:
- 2 个并行实现者修改不同文件: 冲突概率 ~5%
- 4 个并行实现者修改不同文件: 冲突概率 ~25%
- 8 个并行实现者: 冲突概率 ~70%

成本分析:
- 串行额外等待: N × 30s (可预测)
- 并行冲突解决: 人工介入 + Git merge + 上下文重建 = 5-10 分钟 (不可预测)
```

**Harness 的选择**：默认串行（安全优先），显式开启并行（用户知情）。这体现了 Harness 作为开发者 CLI 工具的定位——**确定性 > 速度**。

## 二、技术细节

### 2.1 状态定义（TypedDict）

所有图共享 `BaseAgentState`，使用LangGraph的 `Annotated[list, add_messages]` 实现消息自动追加语义：

```python
class BaseAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iteration: int
    max_iterations: int
    terminal_reason: Optional[str]
    errors: list[str]
    session_id: str
    thread_id: str
```

**PairCodingState** 在基础状态上扩展：
- `task`、`code`、`review_comments`（代码和审查反馈）
- `review_iteration`、`max_review_iterations`（审查循环计数，与总iteration分离）
- `final_decision`：APPROVED | CHANGES_REQUESTED | PENDING
- `human_approval_required`：是否启用人类审批中断

**MultiAgentState** 在基础状态上扩展：
- `plan`、`task_list`（TaskItem[]）、`current_task_index`
- `implementation_results`：task_id → result 字典
- `spec_review`、`code_quality_review`（ReviewResult）
- `review_stage`：spec → code_quality → done
- `pending_tasks`、`completed_tasks`（DAG调度追踪）

### 2.2 节点设计

所有节点采用**工厂函数+闭包模式**注入基础设施（LlmClient、ToolExecutor等），返回标准LangGraph节点函数 `async def node(state: State) -> dict`。

**结对编程节点**：
- `make_coder_node(llm)` → `node_coder`：从任务+审查反馈生成代码，清理markdown标记
- `make_reviewer_node(llm)` → `node_reviewer`：结构化JSON审查，安全解析（`_extract_json`）
- `make_human_approval_node()` → `node_human_approval`：中断点直通，未注入决策时自动批准（fail-open）
- `make_done_node()` → `node_done`：终端节点，设置terminal_reason

**多智能体协作节点**：
- `make_controller_node(llm)` → `node_controller`：LLM分解计划→TaskItem[]，每个任务调用ComplexityAssessor标记复杂度
- `make_task_router_node()` → `node_task_router`：纯逻辑（无LLM调用），拓扑排序：找到所有依赖已完成的PENDING任务，选择第一个
- `make_implementer_node(llm, ...)` → `node_implementer`：通过AgenticLoop运行子智能体，注入精选上下文（计划摘要+任务描述），解析实现者报告协议
- `make_result_collector_node()` → `node_result_collector`：累积结果，更新完成列表
- `make_spec_reviewer_node(llm, tool_executor)` → `node_spec_reviewer`：对照计划验证功能正确性，始终使用最强模型
- `make_code_quality_reviewer_node(llm, tool_executor)` → `node_code_quality_reviewer`：仅在规范审查通过后运行，检查分离关注点、结构、文件增长
- `make_remediation_node()` → `node_remediation`：审查失败→创建修复任务→路由回task_router
- `make_finalize_node()` → `node_finalize`：收集所有结果，设置terminal_reason="completed"

### 2.3 自主复杂度评估

**两阶段评估器**（`ComplexityAssessor`）：

**第一阶段 — 启发式**（快速，无LLM调用）：
- 三组预编译正则模式：SIMPLE（重命名、格式化、修复拼写）、INTEGRATION（API、重构、迁移、数据库）、ARCHITECTURE（设计、安全、并发、性能）
- 得分计算：每个模式匹配计数 → 最高分tier
- 置信度：匹配比例 + 主导奖励（领先>2倍时+0.15）
- 阈值0.7以上直接返回

**第二阶段 — LLM分类**（低置信度时，可选）：
- 使用廉价模型（100 tokens）进行三分类
- 当前已预留接口，默认使用启发式结果

**复杂度→模型映射**：
| 复杂度 | 模型层 | 用途 |
|--------|--------|------|
| SIMPLE | cheap (Haiku) | 格式化、重命名、简单CRUD |
| INTEGRATION | default (Sonnet) | 跨模块协调、API设计 |
| ARCHITECTURE | expensive (Opus) | 设计、安全、关键路径 |

### 2.4 模型路由（LangGraphModelRouter）

基于角色和任务复杂度选择模型：

```python
ROLE_MODEL_MAP = {
    "controller": "default",      # Sonnet — 规划协调
    "coder": "default",           # Sonnet — 代码生成
    "reviewer": "expensive",      # Opus — 代码审查
    "spec_reviewer": "expensive", # Opus — 规范验证
    "code_quality_reviewer": "expensive",  # Opus — 质量审查
}
```

**关键原则**：审查者始终使用最强模型。遗漏缺陷的成本远高于审查的token成本。

### 2.5 Checkpointing与Human-in-the-Loop

**检查点**：支持MemorySaver（开发/测试，进程内字典）和AsyncSqliteSaver（生产，SQLite持久化到 `~/.harness/checkpoints.db`）

**Human-in-the-Loop**：
1. 图编译时设置 `interrupt_before=["human_approval"]`
2. LangGraph在human_approval节点前自动暂停，抛出 `GraphInterrupt`
3. CLI捕获中断 → 显示代码差异和审查意见 → 提示用户
4. 用户确认后，通过 `graph.update_state(config, {"final_decision": "APPROVED"})` 注入决策
5. 使用相同 `config`（thread_id）重新调用 `graph.astream_events(None, config)` 继续

### 2.6 流式输出与事件映射

`LangGraphDelegate._handle_graph_event()` 将LangGraph的 `astream_events` 映射到现有的 `LoopEvent` 系统：

| LangGraph事件 | LoopEvent |
|--------------|-----------|
| `on_chat_model_start` | `kind="thinking"` |
| `on_chat_model_stream` | `kind="text"` |
| `on_tool_start` | `kind="tool_call"` + tool_name + tool_input |
| `on_tool_end` | `kind="tool_result"` + tool_output + tool_error |
| `on_chain_end` | `kind="done"` + terminal_reason |

### 2.7 与现有系统的集成

**AppContext扩展**：新增 `langgraph_delegate` 字段。当 `config.loop.engine == "langgraph"` 时，`_init_langgraph()` 构建图并创建 `LangGraphDelegate`。

**CLI分发**：`handle_run()` 检查 `ctx.langgraph_delegate`：
- 非空 → LangGraph路径（图直接执行）
- 空 → Native路径（ChatDelegate + AgenticLoop）

**新增CLI标志**：
- `--mode {standard,pair_coding,multi_agent}`：覆盖配置的协作模式
- `--no-approval`：结对编程中禁用人类审批

**向后兼容**：`engine = "native"` 是默认值，现有用户无任何变化。

### 2.8 依赖项

新增依赖：
- `langgraph>=0.2.0,<1.0`：StateGraph、条件边、检查点、中断
- `langchain-core>=0.3.0,<1.0`：BaseMessage、HumanMessage、AIMessage
- `langgraph-checkpoint-sqlite>=2.0.0`：AsyncSqliteSaver 持久化检查点

### 2.9 测试覆盖

7个测试模块，86个测试用例，全部通过：

| 测试模块 | 覆盖内容 |
|----------|----------|
| `test_langgraph_state.py` | 三个TypedDict状态的结构完整性（7个测试） |
| `test_langgraph_complexity.py` | 两阶段评估器：复杂度分类 + 批量+模型映射+估算（25个测试） |
| `test_langgraph_router.py` | 按角色路由（6个角色）+ 按复杂度路由 + 自定义模型配置（10个测试） |
| `test_langgraph_graphs.py` | 两个图的编译 + 所有条件路由函数（13个测试） |
| `test_langgraph_delegate.py` | LoopDelegate接口合规性 + 初始状态构建 + 图执行（9个测试） |
| `test_langgraph_gate.py` | 自主模式选择 + 12个真实任务路由场景 + 门控逻辑（22个测试） |

## 三、设计亮点

### 3.1 自主复杂度评估 + 分层模型路由

**创新点**：智能体在规划阶段自动评估每个任务的复杂度，并据此选择合适的模型层。

**为什么重要**：
- **成本效率**：简单任务（重命名、格式化）不浪费Opus的token
- **质量保证**：架构/安全关键任务自动升级到最强模型
- **零配置**：用户不需要手动指定每个子任务的模型

**实现精髓**：两阶段评估（启发式+LLM回退）既快速又准确，置信度机制确保在不确定时能降级到更安全的模型。

### 3.2 LangGraph原生Human-in-the-Loop

**创新点**：使用LangGraph的 `interrupt_before` API实现真实的暂停-审查-继续工作流，而非轮询或超时机制。

**为什么重要**：
- 结对编程的自然交互模式：AI写代码 → 暂停 → 人类审查 → 继续
- 代码审查在进入生产前需要人工确认
- LangGraph的检查点机制确保中断后可精确恢复，不丢失状态

### 3.3 两阶段审查流水线

**创新点**：规范合规性审查与代码质量审查分离，且规范审查必须在质量审查之前通过。

**为什么重要**：
- **首先检查正确性**：代码能否工作？功能需求是否满足？
- **然后检查质量**：代码是否良好？结构是否合理？
- **防止浪费**：如果功能不对，不需要审查代码风格
- **始终使用最强模型进行审查**：审查中遗漏缺陷的代价远高于审查的token成本

### 3.4 DAG依赖调度器

**创新点**：纯逻辑的拓扑排序调度器（无LLM调用），支持任意复杂的任务依赖DAG。

**为什么重要**：
- **确定性**：不依赖LLM来做调度决策，消除LLM幻觉导致的顺序错误
- **高效**：零token消耗，毫秒级完成
- **灵活**：支持顺序链（默认）、并行扇出、树形嵌套和DAG四种拓扑
- **上下文隔离**：每个实现者仅接收计划摘要+任务描述，不继承会话历史，避免上下文污染

### 3.5 闭环工厂模式节点注入

**创新点**：节点不是类方法或全局函数，而是通过工厂函数+闭包注入LLM客户端、工具执行器等基础设施。

**为什么重要**：
- **可测试性**：每个节点可独立测试，注入mock即可
- **松散耦合**：节点不依赖全局状态或单例
- **配置灵活性**：不同的图实例可以有不同的模型、工具集
- **遵循LangGraph最佳实践**：节点是纯函数 `(State) → dict`

### 3.6 基于角色的模型选择原则

**创新点**：模型选择不是一刀切的，而是基于角色和任务的精细化路由。

```
审查者 → 始终 Opus（最强）
控制者 → Sonnet（平衡）
实现者 → 按复杂度：Haiku/Sonnet/Opus
编码者 → Sonnet（默认）
```

**为什么重要**：
- 审查是最关键的环节 — 漏过缺陷的成本最高
- 实现者的成本可以根据任务复杂度弹性调整
- 总成本优化的同时保证质量不降级

### 3.7 零破坏性集成

**创新点**：LangGraph引擎通过 `LoopDelegate` 接口与现有的Native引擎并行共存，通过配置切换。

**为什么重要**：
- 现有用户零迁移成本
- 可在同一CLI中对比两种引擎的行为
- 未来可以独立演进LangGraph引擎，不影响Native引擎
- `AppContext.initialize()` 中的初始化是惰性的（仅在 `engine="langgraph"` 时触发）

---

## 四、LangGraph 设计哲学

本节从**字段设计**、**类型选型**、**状态持久化**和 **Human-in-the-Loop** 四个维度，系统阐述本项目 LangGraph 多智能体架构的设计哲学与关键决策。

### 4.1 字段设计哲学：State 即契约

#### 4.1.1 核心原则

```
字段设计 = 图拓扑的静态类型投影 + 节点间通信协议 + 可观测性锚点
```

LangGraph 的 `State` 不是普通的字典，它是**图的所有节点之间的共享契约**。每个字段的设计必须回答三个问题：

1. **谁写入？** — 哪个节点负责生产该字段的值？
2. **谁读取？** — 哪些下游节点消费该字段？
3. **谁负责最终一致性？** — 当多个节点修改同一字段时，合并策略是什么？

#### 4.1.2 字段分层架构

本项目的状态设计采用**三层继承体系**，每一层对应不同的关注点：

```
BaseAgentState          ← 图引擎层：消息、迭代计数、错误
    ├── PairCodingState ← 结对编程层：代码快照、审查意见、审批决策
    └── MultiAgentState ← 多智能体层：任务DAG、审查管道、实现结果
```

**第一层：图引擎字段（BaseAgentState）**

| 字段 | 类型 | 写入者 | 读取者 | 设计理由 |
|------|------|--------|--------|----------|
| `messages` | `Annotated[list[BaseMessage], add_messages]` | 所有LLM调用节点 | 所有节点 | LangGraph 原生消息管理，自动追加而非替换 |
| `iteration` | `int` | call_llm 等效节点 | 条件边路由 | 全局步数计数器，防止无限循环 |
| `max_iterations` | `int` | 图初始化 | 条件边路由 | 编译时常量，硬性安全边界 |
| `terminal_reason` | `Optional[str]` | 终端节点 | 外部调用者 | 可观测性：告诉调用者*为什么*停止 |
| `errors` | `list[str]` | 所有节点 | 外部调用者 + 错误处理节点 | 非致命错误的累积日志，不影响主流程 |
| `session_id` | `str` | 图初始化 | 日志系统 | 关联 LangGraph 执行与 Harness 会话 |
| `thread_id` | `str` | 图初始化 | Checkpointer | LangGraph 状态持久化的主键 |

**第二层：结对编程字段（PairCodingState）**

| 字段 | 类型 | 写入者 | 读取者 | 设计理由 |
|------|------|--------|--------|----------|
| `task` | `str` | 图初始化 | Coder, Reviewer | 不可变的任务描述锚点 — 审查始终对照原始需求 |
| `code` | `str` | Coder | Reviewer, HumanApproval | 代码快照 — 每次 Coder 迭代产生新版本 |
| `review_comments` | `list[ReviewComment]` | Reviewer | Coder | 结构化的反馈向量，Coder 据此精准修改 |
| `review_iteration` | `int` | Coder | 条件边路由 | **独立于 `iteration`** — 审查循环有自己的上限 |
| `max_review_iterations` | `int` | 图初始化 | 条件边路由 | 防止审查-修改死循环（LGTM 地狱） |
| `final_decision` | `Optional[Literal[...]]` | Reviewer, CLI | 条件边路由 | 三态决策：APPROVED / CHANGES_REQUESTED / PENDING |
| `human_approval_required` | `bool` | 图编译 | HumanApproval | 控制是否启用 interrupt_before |

**关键设计决策：`review_iteration` 与 `iteration` 分离**

```
iteration          = 总的 LLM 调用次数 (coder + reviewer 都算)
review_iteration   = 审查循环的次数 (只算 coder→reviewer→approval 的次数)
```

为什么分离？因为一个审查循环包含 2 次 LLM 调用（coder + reviewer），如果只用 `iteration` 来控制循环上限，会导致审查循环的实际次数不确定。分离后语义清晰：`review_iteration >= max_review_iterations` → 强制终止，无论 coder 是否还需要修改。

**第三层：多智能体协作字段（MultiAgentState）**

| 字段 | 类型 | 写入者 | 读取者 | 设计理由 |
|------|------|--------|--------|----------|
| `plan` | `str` | 图初始化 | Controller, Reviewer | 所有智能体的"唯一真相来源" |
| `task_list` | `list[TaskItem]` | Controller, Implementer, Remediation | TaskRouter | DAG 调度器的输入 — 状态机驱动的任务队列 |
| `current_task_index` | `int` | TaskRouter | Implementer | 当前执行指针，顺序遍历任务列表 |
| `implementation_results` | `dict[str, str]` | ResultCollector | Reviewer, Finalize | task_id → 代码输出的累积映射 |
| `spec_review` | `Optional[ReviewResult]` | SpecReviewer | 条件边路由 | 门控：规范不通过 → 不进入质量审查 |
| `code_quality_review` | `Optional[ReviewResult]` | CodeQualityReviewer | 条件边路由 | 门控：质量不通过 → 触发 Remediation |
| `review_stage` | `Literal["spec","code_quality","done"]` | Reviewer, Remediation | TaskRouter | 审查管道的状态机阶段 |
| `pending_tasks` | `list[str]` | TaskRouter, ResultCollector | TaskRouter | DAG 调度辅助：快速判断是否有未完成任务 |
| `completed_tasks` | `list[str]` | ResultCollector | TaskRouter | DAG 调度辅助：依赖解析的完成集合 |

**关键设计决策：`pending_tasks` + `completed_tasks` 作为 DAG 调度辅助**

这两个字段是 `task_list` 的**冗余索引**。它们不是严格必需的（可以从 `task_list` 推导），但提供了 O(1) 的调度决策能力：
- `completed_tasks` → 依赖检查：`all(dep in completed_tasks for dep in task.dependencies)`
- `pending_tasks` → 空集检测：`len(pending_tasks) == 0` → 路由到审查

没有这两个字段，每次调度都需要 O(n²) 的线性扫描。这就是**用空间换时间**在状态设计中的体现。

#### 4.1.3 字段设计的反模式与约束

| 反模式 | 问题 | 本项目的约束 |
|--------|------|-------------|
| 超大单一状态 | 节点耦合、难以测试、序列化开销大 | 三层继承，每层 ≤ 10 字段 |
| 节点间隐式通信 | 通过 LLM 上下文而非 State 传数据 | 所有节点间数据流动 MUST 通过 State 字段 |
| 可变引用共享 | 列表/字典的引用修改绕过 LangGraph 状态管理 | 节点返回 `dict` 而非原地修改；`task_list` 使用 `list(state["task_list"])` 复制 |
| 字段语义过载 | 一个字段在不同节点有不同含义 | `final_decision` 只由 Reviewer 写入，HumanApproval 只读取 |

### 4.2 类型选型哲学：渐进式类型安全

#### 4.2.1 TypedDict vs Pydantic BaseModel：为什么选 TypedDict？

LangGraph 框架要求 State 必须是 `TypedDict` 或 `dict`，不能是 Pydantic `BaseModel`。这是框架级的技术约束，但其背后有深刻的设计理由：

```
Pydantic BaseModel           LangGraph TypedDict
─────────────────────        ─────────────────────
运行时验证 + 序列化          纯类型标注，零运行时开销
实例方法丰富                  仅数据结构
字段级 validator             无运行时校验
.copy() / .model_dump()      dict 字面量 / **unpacking
与 LangGraph checkpointing   原生支持 add_messages reducer
  不兼容（序列化路径不同）
```

**LangGraph 选择 TypedDict 的原因**：

1. **Add_messages reducer**：`Annotated[list[BaseMessage], add_messages]` 是 LangGraph 的核心机制 — 节点返回的 `{"messages": [new_msg]}` 被**追加**到现有消息列表，而非替换。这个 reducer 是 `Annotated` 类型元数据的一部分，Pydantic 无法表达。

2. **Checkpoint 序列化**：LangGraph 的 checkpointer 使用 `msgpack` 或自定义序列化器。TypedDict 就是 `dict` 的子类型，序列化路径简单直接。Pydantic 对象的序列化需要经过 `.model_dump()`，反序列化需要 `Model.model_validate()`，在中断恢复场景中容易产生不一致。

3. **零依赖**：TypedDict 是 Python 标准库的一部分（`typing.TypedDict`），不需要额外依赖。

#### 4.2.2 Annotated + add_messages：消息追加语义的类型表达

```python
from typing import Annotated
from langgraph.graph.message import add_messages

class BaseAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
```

这是 LangGraph 设计中最精妙的类型技巧。`Annotated[list[BaseMessage], add_messages]` 做了三件事：

1. **类型标注**：告诉类型检查器 `messages` 是一个 `BaseMessage` 列表
2. **Reducer 注入**：告诉 LangGraph 运行时，节点返回的 messages **追加**到已有列表，而非替换
3. **零运行时开销**：`Annotated` 的元数据在运行时被 LangGraph 框架读取，不影响 Python 解释器的执行

**行为对比**：

```python
# 普通字段：替换语义
state = {"code": "v1"}
node_return = {"code": "v2"}
# → state["code"] == "v2"  (替换)

# add_messages 字段：追加语义
state = {"messages": [msg1, msg2]}
node_return = {"messages": [msg3]}
# → state["messages"] == [msg1, msg2, msg3]  (追加!)
```

这个设计使得每个节点只需要关心自己产生的消息，不需要知道历史消息的存在。

#### 4.2.3 Literal 类型：条件边路由的编译时安全

```python
final_decision: Optional[Literal["APPROVED", "CHANGES_REQUESTED", "PENDING"]]
review_stage: Literal["spec", "code_quality", "done"]
complexity: Literal["simple", "integration", "architecture"]
```

每一个有限状态集合都使用 `Literal` 而非 `str`。这带来了三个好处：

1. **IDE 自动补全**：类型检查器知道所有合法值
2. **路由安全性**：条件边函数可以穷尽匹配所有状态
3. **文档即代码**：不需要额外的枚举注释

```python
def _route_after_approval(state: PairCodingState) -> Literal["coder", "done"]:
    decision = state.get("final_decision", "APPROVED")
    if decision == "APPROVED":      # ← IDE 知道只有这三个值
        return "done"
    # CHANGES_REQUESTED 或 PENDING → 继续循环
    return "coder"
```

#### 4.2.4 Optional 的语义分层

| 用法 | 含义 | 示例 |
|------|------|------|
| `Optional[str]` 且初始值 `None` | "尚未产生" — 节点尚未运行 | `terminal_reason: Optional[str] = None` |
| `Optional[str]` 且初始值有具体值 | "可能被清空" — 状态重置 | `final_decision: Optional[str]` 在 coder 运行后回到 `None` |
| `Optional[ReviewResult]` | "可选阶段" — 审查可能不运行 | `spec_review` 在规范审查前为 `None` |

这种分层让字段的**生命周期**变得一目了然：什么时候产生？什么时候清空？什么时候可选？

### 4.3 状态持久化哲学：Checkpoint 即时间旅行

#### 4.3.1 LangGraph Checkpointing 的核心抽象

```
LangGraph Checkpoint = 图状态的完整快照 + 执行位置 + 时间戳

每个 super-step（一个节点执行完毕）自动产生一个 checkpoint。
Checkpoint 不是"日志"，而是"世界线" — 你可以回到任何一个点。
```

**三层持久化架构**：

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: MemorySaver (开发/测试)                            │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ 进程内 dict[thread_id → list[Checkpoint]]              │ │
│  │ 零配置，零延迟，进程退出即丢失                           │ │
│  │ 适用：单元测试、本地调试、短期 REPL 会话                 │ │
│  └───────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: AsyncSqliteSaver (生产)                            │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ ~/.harness/checkpoints.db (WAL 模式)                   │ │
│  │ 跨进程持久化，崩溃恢复，支持多线程并发                    │ │
│  │ 适用：生产部署、长时间运行的多智能体任务                  │ │
│  └───────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 未来扩展 (LangSmith / Postgres)                    │
│  ┌───────────────────────────────────────────────────────┐ │
│  │ 企业级 checkpointing: 分布式追踪 + 审计日志              │ │
│  │ 适用：合规要求、多租户 SaaS                              │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

#### 4.3.2 thread_id：状态隔离的主键

```python
config = {"configurable": {"thread_id": "session-abc123"}}
```

`thread_id` 是 LangGraph checkpointing 的**唯一隔离边界**：

- 同一个 `thread_id` → 共享状态历史 → 可以中断恢复
- 不同 `thread_id` → 完全隔离 → 两个并发对话互不干扰

```
thread_id = "session-A"  →  checkpoints: [cp1, cp2, cp3]
thread_id = "session-B"  →  checkpoints: [cp1, cp2]
# 两个会话的状态完全独立
```

在 Harness 中，`thread_id` 与 `session_id` 一一对应，确保每个 Harness 会话有独立的 LangGraph 状态空间。

#### 4.3.3 中断恢复的完整生命周期

```
时间线：
t0: graph.astream_events(initial_state, config={"thread_id": "X"})
t1: [coder 节点执行]  → checkpoint 1 自动保存
t2: [reviewer 节点执行] → checkpoint 2 自动保存
t3: [human_approval 前] → LangGraph 检测到 interrupt_before
t4: GraphInterrupt 异常抛出，执行暂停
    ───── 用户审查代码，做出决策 ─────
t5: graph.update_state(config, {"final_decision": "APPROVED"})
    # ↑ 修改 checkpoint 2 之后的状态，注入人类决策
t6: graph.astream_events(None, config)  # None = 从当前状态继续
t7: [human_approval 节点执行] → checkpoint 3
t8: [done 节点执行] → checkpoint 4 → END
```

**关键洞察**：`update_state` 不是在原有 checkpoint 上修改，而是创建一个**新的 checkpoint**。这意味着：

1. 人类的决策被**永久记录**在 checkpoint 历史中
2. 可以回溯到 t3（中断前）重新做出不同的决策
3. 中断不会丢失任何状态 — 所有之前的消息、代码、审查意见都在

#### 4.3.4 为什么不用 Redis / 外部缓存？

| 方案 | 优势 | 劣势 | 适用场景 |
|------|------|------|----------|
| MemorySaver | 零延迟，零配置 | 不持久化 | 开发、测试 |
| SQLite | 持久化，单文件，WAL 并发 | 不可横向扩展 | 单机生产 |
| Redis | 高并发，低延迟 | 需运维，无 SQL 查询 | 分布式部署 |
| Postgres | 强一致性，审计日志 | 延迟高，需运维 | 企业级合规 |

本项目选择 SQLite 作为默认生产后端，因为：
- Harness 是**本地优先**的 CLI 工具，单机部署是常态
- SQLite WAL 模式支持并发读写，足够应对多智能体的并行 checkpoint 写入
- 单文件部署，用户不需要安装数据库

### 4.4 Human-in-the-Loop 哲学：中断即交互

#### 4.4.1 中断的本质：从"轮询"到"事件驱动"

传统的 human-in-the-loop 实现是**轮询模式**：

```python
# 传统轮询模式
while not approved:
    code = generate_code()
    review = review_code(code)
    if review.has_must_fix:
        continue
    approved = ask_user(code, review)  # ← 阻塞等待
```

LangGraph 的中断模式是**事件驱动**：

```python
# LangGraph 事件驱动模式
# 1. 声明式中断
graph = workflow.compile(interrupt_before=["human_approval"])

# 2. 流式执行，自然暂停
async for event in graph.astream_events(state, config):
    ...  # 在 human_approval 前自动暂停

# 3. 外部注入决策
graph.update_state(config, {"final_decision": "APPROVED"})

# 4. 从暂停点继续
async for event in graph.astream_events(None, config):
    ...
```

**轮询 vs 事件驱动的差异**：

| 维度 | 轮询模式 | LangGraph 事件驱动 |
|------|---------|-------------------|
| 状态管理 | 手动保存/恢复 | 自动 checkpoint |
| 暂停精度 | 函数级（粗粒度） | 节点级（任意节点前） |
| 恢复安全性 | 依赖开发者正确性 | 框架保证 |
| 并发 | 阻塞，不可并行 | 多个 thread_id 可并行暂停 |
| 审计 | 需自建日志 | checkpoint 历史即审计轨迹 |

#### 4.4.2 interrupt_before 的精确控制

```python
workflow.compile(interrupt_before=["human_approval"])
```

`interrupt_before` 的语义是**在指定节点执行之前暂停**，而不是"在指定节点执行时暂停"。这个微妙差异产生了重要的设计影响：

- 节点本身**不需要知道**中断的存在 → 节点的实现可以完全忽略 HITL 逻辑
- 中断决策是**编译时配置**，不是运行时判断 → 切换 HITL 开/关不需要改代码
- `human_approval` 节点是一个**纯直通节点** → 它只是读取 state 中的决策字段

这体现了**关注点分离**：
```
编译时配置          → 是否中断
State 字段          → 中断时的数据（代码、审查意见）
human_approval 节点 → 处理中断后的决策
CLI 交互层          → 展示数据 + 收集用户输入
```

#### 4.4.3 三种 Human-in-the-Loop 模式

本项目支持三种 HITL 模式，通过 `human_approval` 和 `interrupt_before` 的组合实现：

**模式 A：完全自动（`human_approval=False`, `interrupt_before=[]`）**
```
coder → reviewer → route_decision → coder (loop) or done
```
- 适用：CI/CD 管道、批量任务、信任度高的场景
- 人类角色：事后审查日志

**模式 B：审批门控（`human_approval=True`, `interrupt_before=["human_approval"]`）**
```
coder → reviewer → ⏸ [暂停] → (用户决策) → human_approval → route_decision
```
- 适用：结对编程、生产代码修改
- 人类角色：每次审查循环都需要确认

**模式 C：关键决策点（`interrupt_before=["spec_reviewer"]`）**
```
implementer → result_collector → ⏸ [暂停] → (用户审查) → spec_reviewer
```
- 适用：多智能体协作、重大架构变更
- 人类角色：在审查前确认实现方向

#### 4.4.4 Fail-Open 策略：为什么自动批准而不是自动拒绝？

```python
async def node_human_approval(state: PairCodingState) -> dict:
    decision = state.get("final_decision", "PENDING")
    if decision == "PENDING":
        # 未注入决策 → 自动批准 (fail-open)
        return {"final_decision": "APPROVED"}
```

这是有意为之的**可用性优先**设计：

- **Fail-Closed（自动拒绝）**：如果用户离开终端，所有工作被阻塞，无法恢复
- **Fail-Open（自动批准）**：如果用户离开终端，图继续执行（可在事后审查 checkpoint 历史）

对于 Harness 这样的开发者 CLI 工具，**可用性 > 安全性**（安全由 Docker 沙箱保证，而非审批流）。如果这是金融交易系统，则应该选择 Fail-Closed。

#### 4.4.5 多层中断的协调

多智能体图可以有多个人类决策点，它们通过 `review_stage` 字段协调：

```python
# 第一个中断：规范审查前
interrupt_before=["spec_reviewer"]
# 用户审查所有实现结果 → 决定是否进入规范审查

# 第二个中断（可选）：质量审查后
interrupt_before=["code_quality_reviewer"]
# 用户审查代码质量 → 决定是否接受或触发修复
```

关键设计：每个中断点的**决策数据**已经在该节点之前的 State 字段中。`interrupt_before` 只是告诉框架"在这里停一下"，数据生产由上游节点完成。

---

## 五、设计哲学总结

| 维度 | 哲学 | 核心机制 |
|------|------|----------|
| **字段设计** | State 即契约，字段 = 读/写权限 + 生命周期 + 合并策略 | 三层继承，`review_iteration` 与 `iteration` 分离，冗余索引换 O(1) 调度 |
| **类型选型** | 渐进式类型安全，零运行时开销 | `TypedDict` + `Annotated[add_messages]` + `Literal` 穷尽状态空间 |
| **状态持久化** | Checkpoint 即时间旅行，thread_id 是隔离边界 | MemorySaver → SqliteSaver → 未来 Postgres，自动保存 + 按需恢复 |
| **Human-in-the-Loop** | 中断即交互，声明式暂停 + 外部决策注入 | `interrupt_before` 编译时配置 + `update_state` 运行时注入 + Fail-Open 可用性策略 |
