针对 Harness CLI 的 Pair Coding 场景，**LangGraph 是非常合适的实现选择**。原因在于它对 **有状态的、多步骤循环流程**、**条件分支**、**人机交互中断** 以及 **高度可观测性** 提供了原生支持，而这些恰恰是结对编程多 Agent 架构的刚需。下面给出具体的技术实现细节。

---

## 一、为什么 LangGraph 合适

- **状态图与循环**：原生支持节点间的循环边和条件边，能直接建模“生成→审查→修改→再审查”的闭环，不需要自己维护循环逻辑。
- **可中断的人机交互（Human-in-the-Loop）**：`interrupt` API 可以在任意节点暂停图执行，等待外部（Harness CLI）输入，完美对应代码审查的人工确认点。
- **共享状态**：整个图维护一个全局 `State` 对象，所有 Agent 节点都能读写，天然就是“工作区快照”，符合 Harness 的“一切皆状态”理念。
- **流式与日志**：支持节点级别的流式输出和内置回调，可以轻易地把 Coder/Reviewer 的思考过程实时推送到 CLI。
- **容错与控制**：可设置最大步数、超时、重试，避免无限修改循环，也方便单元测试单个节点。

相比之下，CrewAI 或 AutoGen 虽然也能实现，但它们对循环流程的控制不如 LangGraph 精确，且自定义中断和条件边比较繁琐，不太适合 CLI 流水线这种对确定性要求极高的场景。

---

## 2. 具体实现技术细节

### 2.1 整体状态定义 (State)

定义一个共享状态，所有节点都会接收和更新它。我们会跟踪代码、审查反馈、迭代轮次、最终审批状态等。

```python
from typing import TypedDict, List, Optional, Literal

class ReviewComment(TypedDict):
    severity: Literal["MUST_FIX", "SUGGESTION"]
    file: str
    line: int
    comment: str

class PairCodingState(TypedDict):
    task: str                    # 初始编程任务描述
    code: str                    # 当前代码快照（整个文件或多个文件用字典也可以）
    review_comments: List[ReviewComment]  # 审查反馈
    iteration: int               # 当前循环次数
    max_iterations: int          # 最大循环次数
    final_decision: Optional[Literal["APPROVED", "REJECTED"]]
    messages: List[dict]         # 可选的对话历史，用于调试
```

### 2.2 节点实现

#### Coder Agent 节点

负责根据任务和最新的审查意见生成/修改代码。使用 LLM 调用，将输出解析后更新 `code` 字段。

```python
def coder_node(state: PairCodingState) -> dict:
    task = state["task"]
    comments = state.get("review_comments", [])
    current_code = state.get("code", "")

    if not comments:
        prompt = f"Task: {task}\n\nGenerate the code:"
    else:
        # 把审查意见转化为明确修改指令
        feedback = "\n".join(
            f"  [{c['severity']}] {c['file']}:{c['line']} - {c['comment']}" 
            for c in comments
        )
        prompt = (
            f"Current code:\n```\n{current_code}\n```\n\n"
            f"Review feedback:\n{feedback}\n\n"
            f"Please revise the code to fix all MUST_FIX issues and apply SUGGESTIONs where appropriate."
        )

    # 调用 LLM（简化示例）
    new_code = llm.invoke(prompt).content
    # 清理可能的 markdown 标记
    new_code = new_code.strip().removeprefix("```python").removesuffix("```").strip()

    return {
        "code": new_code,
        "iteration": state.get("iteration", 0) + 1,
        "review_comments": [],  # 清除旧意见，等待新一轮审查
    }
```

#### Reviewer Agent 节点

审查当前代码，返回结构化的审查意见。这里强制输出 JSON，以保证下游可靠解析。

```python
def reviewer_node(state: PairCodingState) -> dict:
    code = state["code"]
    task = state["task"]

    system_prompt = """
    You are a strict code reviewer. Review the code for correctness, style, security, and adherence to the task.
    Output a JSON object with the following structure:
    {
      "decision": "APPROVED" | "CHANGES_REQUESTED",
      "comments": [
         {
           "severity": "MUST_FIX" | "SUGGESTION",
           "file": "main.py",
           "line": 12,
           "comment": "detailed feedback"
         }
      ]
    }
    """
    response = llm.invoke(
        f"{system_prompt}\n\nTask: {task}\n\nCode:\n```\n{code}\n```"
    )
    review = parse_json(response.content)  # 使用安全解析
    return {
        "review_comments": review["comments"],
        "final_decision": review["decision"]
    }
```

#### 人工审批节点（通过 interrupt 实现）

在 `reviewer_node` 之后，通过 LangGraph 的 `interrupt` 暂停，让 CLI 用户审查代码和反馈，决定是否继续。

```python
def human_approval_node(state: PairCodingState) -> dict:
    # 这个节点只负责触发中断，实际决策由外部输入提供
    # 返回的状态中标记 pending
    return {"final_decision": "PENDING"}
```

图的构建中这样使用中断：

```python
# 在构建图时，添加一个中断点
graph.add_node("human_approval", human_approval_node)
graph.add_edge("reviewer", "human_approval")
```

然后运行图时会暂停，等待外部通过 `graph.update_state` 注入决策：

```python
# 使用 stream 或 invoke 时，遇到 human_approval 会抛出 Interrupt
for event in graph.stream(initial_state, config):
    # event 会被中断
    ...
# CLI 获得中断后，展示 diff 和审查意见，收集用户输入
user_decision = input("Approve changes? (yes/no): ")
# 注入用户决策，继续图执行
graph.update_state(config, {"final_decision": "APPROVED" if user_decision.lower() == "yes" else "CHANGES_REQUESTED"})
# 再次 stream 以继续
for event in graph.stream(None, config):
    ...
```

### 2.3 条件边与循环控制

`reviewer_node` 或 `human_approval` 之后，根据 `final_decision` 决定走向：

- `APPROVED` → 前往 `done` 节点（结束）
- `CHANGES_REQUESTED` 且 `iteration < max_iterations` → 回到 `coder_node`
- `CHANGES_REQUESTED` 且 `iteration >= max_iterations` → 前往 `done` 节点，但标记未完全解决

条件路由函数：

```python
def should_continue(state: PairCodingState) -> str:
    decision = state.get("final_decision", "CHANGES_REQUESTED")
    if decision == "APPROVED":
        return "done"
    if state["iteration"] >= state["max_iterations"]:
        return "done"
    return "coder"
```

构建图的边：

```python
from langgraph.graph import StateGraph, END

workflow = StateGraph(PairCodingState)

workflow.add_node("coder", coder_node)
workflow.add_node("reviewer", reviewer_node)
workflow.add_node("human_approval", human_approval_node)
workflow.add_node("done", lambda state: state)  # 终止节点

workflow.set_entry_point("coder")
workflow.add_edge("coder", "reviewer")
workflow.add_edge("reviewer", "human_approval")
workflow.add_conditional_edges(
    "human_approval",
    should_continue,
    {"coder": "coder", "done": END}
)
```

### 2.4 编译与运行

```python
app = workflow.compile(checkpointer=MemorySaver(), interrupt_before=["human_approval"])
```

使用 `checkpointer` 保证中断后可以恢复状态。`interrupt_before` 在 `human_approval` 节点前自动暂停，无需手动写中断逻辑。这种方式更简洁。

CLI 交互流程：

1. 启动图运行 `app.stream(initial_state, config)`
2. 图在 `human_approval` 前暂停，抛出 `GraphInterrupt`
3. Harness CLI 捕获中断，展示当前代码 diff 和审查意见，提示用户操作
4. 用户确认后，CLI 调用 `app.invoke(None, config)` 或 `app.stream` 继续（注意：使用 `interrupt_before` 时，不需要手动更新状态，只需再次调用 `invoke` 并传入同一个 `config`，LangGraph 会自动继续）

### 2.5 错误处理与安全

- **沙箱执行**：如果需要在审查时运行测试，可在 `reviewer_node` 内通过 `subprocess` 在临时 Docker 中执行，禁止网络。
- **LLM 输出解析**：使用 `pydantic` 或 `json_repair` 等库来解析结构化输出，失败时有降级逻辑。
- **最大步数限制**：通过图配置 `config["recursion_limit"] = 50` 防止意外循环。
- **日志与观测**：LangGraph 内置 `langsmith` 集成，每一步输入输出均可追踪，满足企业级审计需求。

### 2.6 代码模块结构建议

```
pair_coding/
├── graph.py          # 图定义、节点、边
├── agents/
│   ├── coder.py      # Coder Agent 的 prompt 和调用逻辑
│   └── reviewer.py   # Reviewer Agent 的 prompt 和结构化输出
├── state.py          # State 定义
├── cli.py            # Harness CLI 集成，调用 graph 并处理中断
└── sandbox.py        # 安全代码执行工具
```

---

## 3. 总结

使用 LangGraph 实现 Harness CLI 的 Pair Coding 多 Agent 架构完全可行且高度匹配。具体落地时，抓住三个核心：

1. **用 `TypedDict` 定义共享状态**，包含代码、审查意见、迭代次数。
2. **用 `interrupt_before` 实现人工审批**，天然挂载到 CLI 交互。
3. **用条件边实现“生成-审查-修改”循环**，限制最大迭代次数，保证终止。

这种方案既保留了结对编程的迭代灵活性，又严格遵守 CLI 流水线对确定性、安全性和可观测性的要求，是目前业界公认的实践典范。