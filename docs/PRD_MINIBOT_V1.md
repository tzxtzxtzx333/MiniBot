# PRD MiniBot V1

## 1. 产品目标

MiniBot V1 是一个本地个人智能助手，面向：

- 个人知识管理
- 办公自动化
- 长对话任务
- 可审计的工具协作

本项目必须是新建实现，不 fork、不复制 nanobot 源码。可以参考 nanobot、pico、learn-claude-code 的架构思想，但实现必须独立。

## 2. V1 范围

V1 必须具备：

- Harness-first 统一执行链
- 五层插件化架构
- CLI / HTTP / Feishu Mock 多渠道接入
- Feishu WebSocket Bot 真实接入路径保留
- Tool Calling 闭环
- Hook 机制
- MEMORY / HISTORY / archives 结构化记忆
- 上下文治理
- 工具安全与运行治理
- 轻量 SubAgent
- JSON Benchmark 与 report compare

## 3. 五层架构

```text
1. Channel Layer
2. Harness Layer
3. Tool Layer
4. Memory / Context Layer
5. Governance / Evaluation Layer
```

## 4. 核心能力要求

### 4.1 Harness

必须实现：

- `AgentLoop`
- `ModelClient`
- `ContextBuilder`
- `ToolDispatcher`
- `RunRecorder`

生命周期必须覆盖：

- `SessionStart`
- `UserMessageReceived`
- `MemoryRecall`
- `ContextBuild`
- `PlaceholderClean`
- `ModelPlanning`
- `ToolCallDetected`
- `PreToolUse`
- `ToolGovernanceCheck`
- `ToolExecution`
- `PostToolUse`
- `ToolResultAppend`
- `VerifierCheck`
- `FinalResponseGenerate`
- `HistoryPersist`
- `RunReportPersist`
- `SessionEnd`

### 4.2 Channel

必须支持：

- `python -m minibot chat`
- `python -m minibot status`
- `python -m minibot http`
- `python -m minibot feishu-mock`

HTTP 最少提供：

- `GET /status`
- `POST /chat`
- `POST /benchmark/run`

Feishu 必须采用：

- `FeishuWebSocketChannel`
- `MockFeishuChannel`

### 4.3 Tool Calling

必须实现统一工具协议和注册调度：

- `ToolSpec`
- `ToolResult`
- `ToolRegistry`
- `ToolDispatcher`

内置工具：

- `calculator`
- `file_read`
- `file_write`
- `web_fetch`
- `web_search`
- `weather`
- `map_route`
- `python_exec`
- `shell_exec`
- `memory_search`
- `memory_write`
- `doc_summarize`

### 4.4 Hook

必须实现：

- `SessionStart`
- `UserMessageReceived`
- `MemoryRecall`
- `ContextBuild`
- `PreToolUse`
- `PostToolUse`
- `ToolError`
- `BeforeResponse`
- `AfterResponse`
- `SessionEnd`

匹配：

- `exact`
- `regex`

动作：

- `log`
- `require_approval`
- `block`
- `redact`
- `tag`

### 4.5 Memory / Context

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

必须支持：

- 首次运行自动创建工作区
- 每轮写入 HISTORY
- “记住”写入 MEMORY
- `/new` 压缩归档
- 轮次阈值触发归档
- token 预算触发截断或压缩
- PlaceholderCleaner 清理无效上下文

### 4.6 Governance

必须支持：

- 白名单自动执行
- 灰名单审批
- 黑名单阻断
- 参数上限校验
- 敏感信息脱敏
- 重复调用去重
- Docker 沙箱入口
- 工具超时
- 自动重试
- 降级执行
- Partial Success

### 4.7 SubAgent

必须实现轻量子代理：

- `MemoryAgent`
- `SummarizerAgent`
- `ToolAgent`
- `VerifierAgent`

### 4.8 Benchmark / Audit

必须支持：

- `python -m minibot benchmark`
- `python -m minibot benchmark --category memory`
- `python -m minibot benchmark --report reports/run_v1.json`
- `python -m minibot compare reports/run_v1.json reports/run_v2.json`

benchmark 目录：

```text
benchmarks/
  memory/
  context/
  tools/
  safety/
  reasoning/
  channel/
  regression/
```

case 必须不少于 70 个 JSON 文件。

## 5. 报告指标

必须由实际运行生成，而不是硬编码：

- `pass_rate`
- `tool_rounds`
- `avg_latency`
- `failure_category`
- `tool_trace`
- `verifier_reason`
- `retry_count`
- `partial_success`
- `downgrade_reason`

## 6. 文档要求

V1 必须包含：

- `README.md`
- `docs/architecture.md`
- `docs/resume_mapping.md`
- `docs/demo_script.md`
- `docs/feishu_setup.md`
- `docs/decisions.md`

## 7. 明确约束

- 不得在 README 或源码中硬编码简历数字指标
- 数字指标只能由 report 生成
- 不删改已完成模块的公开 CLI 子命令名称
- 保持项目随时可运行

## 8. V1 验收命令

```bash
python -m minibot --help
python -m minibot status
python -m minibot chat
python -m minibot http
python -m minibot feishu-mock examples/mock_feishu_event.json
python -m minibot benchmark
python -m minibot compare reports/run_v1.json reports/run_v1.json
pytest
```
