# MiniBot Architecture

## 目标

MiniBot 采用 Harness-first 架构。所有渠道都先进入统一 `ChannelMessage`，再由同一个 `AgentLoop` 编排模型、工具、记忆、治理和审计。

## 总体分层

```text
1. Channel Layer
2. Harness Layer
3. Tool Layer
4. Memory / Context Layer
5. Governance / Evaluation Layer
```

## 1. Channel Layer

文件：

- `minibot/channels/base.py`
- `minibot/channels/cli_channel.py`
- `minibot/channels/http_channel.py`
- `minibot/channels/feishu_ws_channel.py`
- `minibot/channels/mock_feishu_channel.py`

统一消息结构：

```python
ChannelMessage(channel, user_id, session_id, content, metadata)
```

说明：

- CLI、HTTP、Feishu mock、Feishu WebSocket 边界都复用同一个消息协议
- `feishu` 代表真实接入边界
- `feishu-mock` 代表本地回归

## 2. Harness Layer

文件：

- `minibot/harness/agent_loop.py`
- `minibot/harness/model_client.py`
- `minibot/harness/context_builder.py`
- `minibot/harness/tool_dispatcher.py`
- `minibot/harness/run_recorder.py`

生命周期：

```text
SessionStart
UserMessageReceived
MemoryRecall
ContextBuild
PlaceholderClean
ModelPlanning
ToolCallDetected
PreToolUse
ToolGovernanceCheck
ToolExecution
PostToolUse
ToolResultAppend
VerifierCheck
FinalResponseGenerate
HistoryPersist
RunReportPersist
SessionEnd
```

说明：

- `ModelClient` 产出统一 `ModelPlan`
- `ToolAgent` 解析并转发工具计划
- `ToolDispatcher` 统一执行工具与治理
- `RunRecorder` 负责 JSON trace

## 3. Tool Layer

文件：

- `minibot/tools/base.py`
- `minibot/tools/registry.py`
- `minibot/tools/*.py`

协议：

- `ToolSpec`
- `ToolResult`
- `BaseTool`

当前分类：

- 真实本地工具：`calculator`、`file_read`、`file_write`、`memory_search`、`memory_write`、`doc_summarize`
- 真实外部 provider：`web_fetch`
- mock / 边界工具：`web_search`、`weather`、`map_route`
- Docker 工具：`python_exec`、`shell_exec`

## 4. Memory / Context Layer

文件：

- `minibot/memory/store.py`
- `minibot/memory/recall.py`
- `minibot/memory/compactor.py`
- `minibot/memory/archive.py`
- `minibot/context/token_budget.py`
- `minibot/context/history_truncator.py`
- `minibot/context/placeholder_cleaner.py`
- `minibot/context/prompt_builder.py`

工作区结构：

```text
.minibot/
  MEMORY.md
  HISTORY.md
  archives/
  sessions/
  runs/
  sandbox_workspace/
```

说明：

- `MEMORY.md` 负责长期记忆
- `HISTORY.md` 负责近期对话
- `archives/` 存归档摘要
- `runs/` 存运行 trace
- `/new` 和阈值触发会调用 `SummarizerAgent`

## 5. Governance / Evaluation Layer

文件：

- `minibot/governance/*.py`
- `minibot/sandbox/*.py`
- `minibot/evals/*.py`

治理链：

1. policy check
2. approval
3. duplicate detection
4. sandbox routing
5. retry
6. downgrade
7. redaction
8. partial-success aggregation

评测链：

1. 读取 `benchmarks/**/*.json`
2. 使用同一个 `AgentLoop` 执行 case
3. 回读 `.minibot/runs/*.json`
4. `RuleVerifier`
5. `ModelVerifier`
6. metrics 汇总
7. `ReportWriter`
8. `compare`

## 模型设计

模式：

- `fake`：仅测试 / 开发回归
- `real`：DeepSeek / OpenAI-compatible

设计点：

- real 模式通过 OpenAI-compatible Chat Completions 接口
- real tool calling 走统一 `tool_plan` JSON 协议
- 不依赖厂商私有 function calling
- 缺配置时返回 `deepseek_config_missing`
- real 模式不 fallback fake

## 外部 provider 设计

- `web_fetch`：直接真实 HTTP provider
- `web_search`：mock provider，保留 real 边界
- `weather`：API provider 边界，缺 key 返回 `weather_config_missing`
- `map_route`：AMap MCP adapter 边界，缺配置返回 `amap_mcp_config_missing`

MCP 只用于外部 MCP provider 场景，不是所有工具的统一入口。

## Docker 设计

只覆盖高风险执行工具：

- `python_exec`
- `shell_exec`

说明：

- 禁止宿主机 fallback
- Docker 不可用时返回 `docker_unavailable`
- trace 中保留 `sandbox=docker`

## Feishu 设计

- `feishu`：真实 WebSocket Bot 接入边界
- `feishu-mock`：本地回归
- 缺配置：`feishu_config_missing`
- 缺 SDK：`feishu_sdk_not_installed`

## Known Issues

- PowerShell 默认编码下直接 `Get-Content` 可能出现中文显示异常
- 读取 trace / report 时建议强制 `-Encoding UTF8`
