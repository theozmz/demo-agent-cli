# Schema, Transmission, and Retry Mechanism

本文档详细描述 Harness 中 tool schema 的模板结构、从定义到 LLM 的完整传递链路、以及参数校验失败时的重试与熔断机制。

---

## 1. Tool Schema Template

每个 tool 通过 `Tool.input_schema` 属性返回 [JSON Schema](https://json-schema.org/) dict。必填字段定义在 `required` 数组中。

### 模板结构

```json
{
  "type": "object",
  "properties": {
    "<param_name>": {
      "type": "<string|integer|boolean|...>",
      "description": "<human-readable description>"
    }
  },
  "required": ["<param1>", "<param2>"]
}
```

### 实际示例 — `file_edit`

```python
# src/harness/tools/builtin/file_edit.py

class FileEditTool(Tool):
    name = "file_edit"
    description = "Performs exact string replacement in a file. ..."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to replace",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }
```

### 各工具 schema 一览

| Tool | Required params | Optional params |
|------|----------------|-----------------|
| `file_read` | `file_path` | `offset`, `limit` |
| `file_write` | `file_path`, `content` | — |
| `file_edit` | `file_path`, `old_string`, `new_string` | `replace_all` |
| `glob_search` | `pattern` | `path` |
| `grep_search` | `pattern` | `path`, `glob`, `type` |
| `bash_exec` | `command` | `timeout`, `cwd` |
| `web_fetch` | `url` | `prompt` |
| `web_search` | `query` | — |
| `memory_read` | `key` | — |
| `memory_write` | `key`, `value` | — |
| `memory_delete` | `key` | — |
| `agent` | `task` | `max_turns` |

---

## 2. Schema 传递链路

完整的传递路径：

```
Tool.input_schema (JSON Schema dict)
    │
    ▼
ToolRegistry.get_schemas()          ← src/harness/tools/registry.py
    │  1. 从缓存取或调用 tool.input_schema
    │  2. _annotate_input_schema() 给必填字段 description 加 [REQUIRED] 前缀
    │  3. 组装为 Anthropic 原生格式：{name, description, input_schema}
    │
    ▼
ChatDelegate.call_llm()             ← src/harness/core/loop.py
    │  tools = ctx.tool_registry.get_schemas()
    │
    ▼
LiteLlmProvider.generate()          ← src/harness/llm/providers/litellm_provider.py
    │  request_kwargs["tools"] = tools
    │
    ▼
litellm.acompletion()               ← 第三方库
    │  provider="anthropic" → 使用 Anthropic API 格式
    │  api_base → DeepSeek 兼容端点 (https://api.deepseek.com/anthropic)
    │
    ▼
DeepSeek API                        ← 接收 Anthropic 格式请求
    │  可能正确传递 input_schema，也可能丢弃 required 数组
    │
    ▼
LLM (deepseek-v4-pro)               ← 生成 tool call
    │  tool call 参数在 function.arguments 中 (JSON string 或 dict)
    │
    ▼
LiteLlmProvider._parse_response()   ← 解析 LLM 响应
    │  _parse_tool_args() 处理 string/dict/None 三种格式
    │
    ▼
ChatDelegate.execute_tool_calls()   ← 逐条执行 tool call
    │
    ▼
ToolExecutor.execute()              ← 6 步流水线
    │  Step 2: _validate_params()  ← ★ 校验点
    │
    ▼
jsonschema.validate(params, input_schema)
    │  失败 → ValidationError
    │  成功 → 继续执行
```

### `[REQUIRED]` 标注机制

`_annotate_input_schema()` (registry.py) 在生成 schema 时，遍历 `required` 数组，
给每个必填属性的 `description` 字段添加 `[REQUIRED]` 前缀：

```python
def _annotate_input_schema(input_schema: dict) -> dict:
    required = input_schema.get("required", [])
    properties = input_schema.get("properties", {})
    annotated_props = {}
    for name, prop in properties.items():
        prop = dict(prop)
        if name in required and not prop.get("description", "").startswith("[REQUIRED]"):
            prop["description"] = f"[REQUIRED] {prop.get('description', '')}"
        annotated_props[name] = prop
    return {**input_schema, "properties": annotated_props}
```

发送给 LLM 的最终 schema 示例：

```json
{
  "name": "file_edit",
  "description": "Performs exact string replacement...",
  "input_schema": {
    "type": "object",
    "properties": {
      "file_path": {
        "type": "string",
        "description": "[REQUIRED] Absolute path to the file to edit"
      },
      "old_string": {
        "type": "string",
        "description": "[REQUIRED] The exact text to replace"
      },
      "new_string": {
        "type": "string",
        "description": "[REQUIRED] The replacement text"
      },
      "replace_all": {
        "type": "boolean",
        "description": "Replace all occurrences (default: false)"
      }
    },
    "required": ["file_path", "old_string", "new_string"]
  }
}
```

两层冗余信号：
- `required` 数组 — 标准 JSON Schema 约束（依赖 API 兼容层正确传递）
- `[REQUIRED]` 前缀 — 纯文本信号，不依赖 schema 约束传递

### API 格式转换

litellm 接受两种 tool 定义格式:

| 格式 | 键名 | 提供者 |
|------|------|--------|
| Anthropic 原生 | `input_schema` | Anthropic, Bedrock, Vertex |
| OpenAI 原生 | `function.parameters` | OpenAI, DeepSeek, Groq, etc. |

Harness 使用 Anthropic 格式。litellm 检测到 `input_schema` 键后直接透传
（不转换）。最终发给 API 的请求体中，tool 定义如下：

```json
{
  "model": "anthropic/deepseek-v4-pro",
  "messages": [...],
  "tools": [
    {
      "name": "file_edit",
      "description": "...",
      "input_schema": { "type": "object", "properties": {...}, "required": [...] }
    }
  ]
}
```

---

## 3. 参数校验与重试机制

### 3.1 校验流水线 (ToolExecutor._validate_params)

```
params → 快速预检 → jsonschema.validate() → 通过 / 失败
```

**快速预检**（新增）：
```python
required = tool.input_schema.get("required", [])
missing = [f for f in required if f not in params]
if missing:
    raise InvalidParametersError(
        f"Missing required parameter(s): {', '.join(repr(m) for m in missing)}. "
        f"Required: {required}",
        tool_name=tool.name,
    )
```

**jsonschema 校验**：如果预检通过（所有必填字段都存在），调用 `jsonschema.validate()`。
提取具体错误信息：
```python
# _extract_validation_detail() — executor.py
if validator == "required":
    → "Missing required parameter(s): 'new_string'. Received: ['file_path', 'old_string']"
if validator == "type":
    → "file_path: expected string, got integer. Schema expects: string"
if validator == "additionalProperties":
    → "Unexpected parameter(s): ['descripton']. Allowed: ['file_path', 'old_string', ...]"
```

### 3.2 错误消息格式化 (_format_schema_error)

```
executor 抛出 InvalidParametersError
    │
    ▼
ChatDelegate.execute_tool_calls()
    │  检测到 InvalidParametersError
    │  递增 _consecutive_schema_errors[tool_name]
    │
    ▼
_format_schema_error(tool_name, params, str(e), count)
    │
    ├─ count = 1 → 返回具体错误 + 提示检查参数
    │     "Error calling file_edit: Missing required parameter(s): 'new_string'.
    │      This is attempt 1. Schema errors are deterministic — the same call will
    │      always fail. Please check the required parameters for file_edit..."
    │
    └─ count ≥ 2 → 返回 [FATAL] 消息
          "[FATAL] file_edit has been called 2 times with invalid parameters.
           STOP retrying — schema errors are deterministic..."
```

### 3.3 熔断器 (Circuit Breaker)

```
AgenticLoop.run() 的每次迭代结束后:

  for tool_key, err_count in _consecutive_schema_errors.items():
      if err_count >= 2:           ← 阈值: 2 次连续失败
          return LoopOutcome(
              kind="error",
              content="Tool 'file_edit' failed parameter validation 2 consecutive
                       times. Schema validation errors are deterministic..."
          )
```

### 3.4 连续失败计数器生命周期

```
首次 schema error  → _consecutive_schema_errors["file_edit"] = 1
再次 schema error  → _consecutive_schema_errors["file_edit"] = 2  → 熔断!
成功后              → _consecutive_schema_errors.pop("file_edit", None)  → 重置
```

### 3.5 完整时序图

```
Turn N:
  LLM → tool_call: file_edit(file_path="/x", old_string="abc")  ← 缺少 new_string
  executor._validate_params() → InvalidParametersError
  _format_schema_error(count=1) → "Error calling file_edit: Missing 'new_string'..."
  LLM 收到错误消息

Turn N+1:
  LLM → tool_call: file_edit(file_path="/x", old_string="abc")  ← 仍然缺少 new_string
  executor._validate_params() → InvalidParametersError
  _format_schema_error(count=2) → "[FATAL] file_edit has been called 2 times..."
  ★ 熔断器触发 → AgenticLoop 返回 error outcome → 会话结束
```

### 3.6 为什么 2 次而非 3 次？

Schema 校验失败是**确定性的** — 相同的输入每次都会失败。这与瞬时错误
（rate limit、timeout）有本质区别：

| 错误类型 | 示例 | 重试有意义? | 熔断阈值 |
|----------|------|------------|---------|
| 瞬时错误 | HTTP 429, timeout | ✓ 可能恢复 | 3 次 |
| Schema 错误 | 缺少必填字段 | ✗ 永远不会自愈 | 2 次 |

---

## 4. LangGraph 多智能体路径

在 `multi_agent` 模式下，每个 implementer 子智能体通过 `AgenticLoop` + `ChatDelegate`
执行任务。schema 错误处理一致，但有额外的跨边界传播：

```
Implementer sub-agent (AgenticLoop)
    │  Tool 'file_edit' schema error ×2 → circuit breaker → outcome.kind="error"
    │  outcome.content 包含 "[FATAL]" 标记
    │
    ▼
_parse_implementer_status()
    │  检测 "[FATAL]" → 返回 "SCHEMA_ERROR"
    │
    ▼
Task status = SCHEMA_ERROR
    │  _TERMINAL_TASK_STATUSES = {"DONE", "DONE_WITH_CONCERNS", "SCHEMA_ERROR"}
    │  task_router 视 SCHEMA_ERROR 为终端状态 — 不重新入队
    │  remediation 不会为此任务创建修复任务
    │
    ▼
Parent graph continues with remaining tasks
```

---

## 5. 关键文件索引

| 文件 | 职责 |
|------|------|
| `src/harness/tools/tool.py` | `Tool.input_schema` 抽象属性 — 每个工具定义自己的 JSON Schema |
| `src/harness/tools/builtin/*.py` | 12 个内置工具的具体 schema 定义 |
| `src/harness/tools/registry.py` | `get_schemas()` 组装 + `_annotate_input_schema()` 标注必填字段 |
| `src/harness/tools/executor.py` | `_validate_params()` 快速预检 + jsonschema 校验 + `_extract_validation_detail()` |
| `src/harness/llm/providers/litellm_provider.py` | `_to_litellm_messages()` 消息转换 + `_parse_tool_args()` 参数解析 |
| `src/harness/core/loop.py` | `_format_schema_error()` 格式化 + `_consecutive_schema_errors` 计数器 + 熔断器 |
| `src/harness/core/errors.py` | `InvalidParametersError` 异常类型 |
| `src/harness/langgraph/nodes/multi_agent.py` | `_parse_implementer_status()` 检测 `[FATAL]` + `_TERMINAL_TASK_STATUSES` |
| `src/harness/langgraph/state.py` | `TaskItem.status` 含 `SCHEMA_ERROR` |
