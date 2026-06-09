# MiniBot

MiniBot 是一个 **Harness-first 本地 Agent 框架原型**，重点验证以下能力：

- 多渠道接入（CLI / HTTP / Feishu WebSocket）
- 统一 AgentLoop 执行链与工具调用治理
- 结构化记忆、上下文压缩与相关性检索
- 运行审计、Benchmark 回归与报告对比

> **定位说明**：MiniBot 是个人学习项目与校招面试展示用的原型，不是生产级平台，也不是完整商业化 Agent 系统。

## 项目结构

```text
minibot/
  harness/       — AgentLoop, ModelClient, ToolDispatcher, RunRecorder
  hooks/         — 事件拦截管线（HookManager + 匹配器 + 动作注册表）
  tools/         — 工具协议（BaseTool / ToolSpec / ToolResult）+ 14 个工具
  governance/    — 三层治理（白名单/灰名单/黑名单）、审批、去重、脱敏、重试
  memory/        — MEMORY.md / HISTORY.md / Archives 压缩归档
  context/       — 上下文构建、Token 预算、截断、占位清理
  sandbox/       — Docker 沙箱执行（python_exec / shell_exec）
  planning/      — PlannerAgent / TaskExecutor / ReplannerAgent
  subagents/     — MemoryAgent, SummarizerAgent, ToolAgent, VerifierAgent
  evals/         — BenchmarkRunner, RuleVerifier, ModelVerifier, ReportWriter
  evidence/      — 大工具输出压缩存储与摘要
  tasks/         — JSONL 任务状态管理
  channels/      — CLI, HTTP, Feishu WebSocket, Feishu Mock
configs/         — minibot.json, policy.json, tools.json, hooks.json
benchmarks/      — 116 个 JSON 工程回归用例
tests/           — pytest 测试（368+ passed）
docs/            — 架构说明、设计决策、简历映射
```

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e .[dev]
python -m minibot --help
python -m minibot status
```

**无需任何外部 API key 即可运行**：默认使用 `fake` 模型模式，所有工具调用由本地规则引擎驱动。

## 三种运行模式

| 模式 | 说明 | 需要外部依赖 |
|---|---|---|
| **fake** | 规则引擎驱动，用于本地回归测试和开发 | 无 |
| **mock** | 工具返回模拟数据，用于无 key 的功能演示 | 无 |
| **real** | 接入真实 LLM 和外部 API，用于端到端验证 | DeepSeek API key 等 |

fake/mock/real 三条路径严格隔离：real 模式缺配置时返回明确错误（如 `deepseek_config_missing`），绝不会静默 fallback 到 fake 或 mock。

## 核心能力

### 1. AgentLoop / Harness

统一入口 `AgentLoop.handle_message()`。所有渠道消息标准化为 `ChannelMessage` 后进入同一执行链：

```text
SessionStart → UserMessageReceived → MemoryRecall → ContextBuild →
PlaceholderClean → ModelPlanning → [ToolCallDetected → PreToolUse →
ToolGovernanceCheck → ToolExecution → PostToolUse → ToolResultAppend]×N →
VerifierCheck → FinalResponseGenerate → HistoryPersist →
RunReportPersist → SessionEnd
```

工具循环受四维 budget 控制（`max_tool_rounds` / `max_tool_calls_total` / `max_runtime_seconds` / `max_same_tool_calls`），支持多轮 observe → re-plan。

### 2. Hook 机制

在 AgentLoop 关键生命周期节点触发 Hook，支持 exact / regex 匹配和日志、审批、阻断、脱敏等动作注入。Hook 配置独立于核心循环，可非侵入式扩展。规则定义在 `configs/hooks.json`，支持 exact / regex 匹配和日志、审批、阻断、脱敏等动作注入。

### 3. 工具调用与三层治理

14 个注册工具，分三类：

| 分类 | 工具 | 说明 |
|---|---|---|
| 本地 | `calculator`, `file_read`, `file_write`, `memory_search`, `memory_write`, `doc_summarize` | 不依赖外部服务 |
| 外部 provider | `web_fetch`, `web_search`, `weather`, `map_route`, `map_poi_search` | 真实调用需配置 key，否则返回 mock 数据 |
| Docker 沙箱 | `python_exec`, `shell_exec` | 隔离执行，Docker 不可用时返回 `docker_unavailable` |

治理链：**schema 校验 → 白/灰/黑名单判定 → 审批（灰名单） → 去重 → 沙箱路由 → 重试/降级 → 敏感信息脱敏 → partial success 聚合**。

### 4. 记忆与上下文

```text
.minibot/
  MEMORY.md   ← 长期偏好/事实（全量注入）
  HISTORY.md  ← 近期对话（相关性检索 + token budget 截断）
  archives/   ← LLM 压缩归档（/new 手动触发 或 轮次阈值自动触发）
```

相关性检索基于 token overlap + Jaccard 评分，在短文本场景下效果足够，无需向量数据库。

### 5. Benchmark / Audit

- **116 个 JSON 工程回归用例**，覆盖 memory / context / reasoning / safety / tools / channel / regression / planner 八个类别
- 定位：版本回归、trace 审计和失败归因，不是科研 benchmark，也不代表生产线上稳定性
- 双验证器：`RuleVerifier`（规则断言）+ `ModelVerifier`（LLM 审计，需配置 verifier key）
- `compare` 命令支持两个报告版本回归对比
- 每次运行生成完整 JSON run trace（`.minibot/runs/`），包含 tool_trace、lifecycle_events、context_metrics
- 输出指标：pass_rate、tool_rounds、avg_latency、failure_category、tool_trace、context_metrics 等

## 常用命令

```bash
# 状态检查
python -m minibot status

# 单轮对话
python -m minibot chat --message "calculate 128 * 64"
python -m minibot chat --message "run python code print(1+1)"
python -m minibot chat --message "shell_exec echo hello"

# 记忆与压缩
python -m minibot chat --message "remember I prefer Chinese replies"
python -m minibot chat --message "/new"

# Benchmark（fake 模式，无需外部 key）
python -m minibot benchmark --mode fake --report reports/run_fake.json

# Benchmark（real 模式，需配置 .env）
python -m minibot benchmark --mode real --scope core --report reports/run_real.json

# 报告对比
python -m minibot compare reports/run_fake.json reports/run_fake.json
```

## 外部 Provider 接入

| Provider | 配置变量 | 缺配置行为 |
|---|---|---|
| DeepSeek 模型 | `MINIBOT_MODEL_*` | 返回 `deepseek_config_missing`，不 fallback fake |
| Tavily 搜索 | `TAVILY_API_KEY` | `web_search` 使用 mock provider |
| QWeather 天气 | `MINIBOT_WEATHER_API_KEY` | `weather` 返回 `weather_config_missing` |
| AMap MCP 地图 | `MINIBOT_AMAP_MCP_*` | `map_route` / `map_poi_search` 返回 mock |
| Feishu WebSocket | `FEISHU_APP_ID`, `FEISHU_APP_SECRET` | 返回 `feishu_config_missing` |

**重要**：以上能力在未配置时为 **接入边界** 或 **mock 演示**，不应表述为生产级真实接入。所有 provider 状态均写入 tool trace / report metadata（`provider_status: real|mock|missing|failed`）。

## Benchmark 指标说明

报告中的 token 指标使用固定估算公式：

```text
estimated_tokens = ceil(len(text) / 4)
```

这是工程实验口径的 **字符数折算**，不是模型厂商实际计费 token。`dynamic_context_tokens` 排除固定 tool_specs，聚焦可变上下文载荷（history、memory、archives、tool results）。

**Token 缩减百分比不作为简历数字使用。** 报告的可靠数字来源为 real mode 下的 pass_rate、tool_rounds、avg_latency 等。

## 任务管理（TaskStore）

```bash
python -m minibot tasks create --goal "计算 128 * 64"
python -m minibot tasks list
python -m minibot tasks show <task_id>
python -m minibot tasks resume <task_id>
python -m minibot tasks cancel <task_id>
```

task 生命周期：`pending → running → completed | waiting_approval | failed | cancelled`

## HTTP 服务与 Approval API

```bash
python -m minibot http --host 127.0.0.1 --port 8000
```

端点：`GET /status`、`POST /chat`、`GET /approvals`、`POST /approvals/{id}/approve`、`POST /approvals/{id}/reject`

可选 Bearer Token 认证（`MINIBOT_HTTP_AUTH_TOKEN`），默认无认证（仅绑定 127.0.0.1）。

### Status Health Check

`python -m minibot status` 和 `GET /status` 返回增强的健康检查，包含 `task_count`、`approval_pending_count`、`budget` 等字段。

## Deployment Boundary

```text
MiniBot/
  .env.example              ← 完整环境变量模板（无真实 key）
  scripts/
    run_http.ps1            ← 启动 HTTP 服务
    run_feishu.ps1          ← 启动飞书 WebSocket
    run_real_agent_benchmark.ps1
    run_safety_benchmark.ps1
    run_multiround_benchmark.ps1
    run_status.ps1          ← 健康检查
  .minibot/
    logs/                   ← 运行日志边界
    tasks/tasks.jsonl       ← 任务状态存储
    approvals/              ← 审批队列存储
```

## 本地开发

```bash
pip install -e .[dev]
pytest -v
```

`.env.example` 包含所有可配置变量，复制为 `.env` 并按需填入真实 key。

## 文档

- [DEMO.md](DEMO.md) — 最小可复现场景
- [架构说明](docs/architecture.md)
- [设计决策](docs/decisions.md)
- [简历映射](docs/resume_mapping.md)
- [Feishu 接入说明](docs/feishu_setup.md)
- [最终验收](docs/final_acceptance.md)

## 最终定位

MiniBot 是一个 Harness-first 本地 Agent 框架原型，已打通模型调用、工具治理、任务状态、渠道内审批、安全策略、多轮工具规划与部署运行边界等核心链路；真实外部能力依赖 API key、Docker、MCP endpoint 等运行环境配置。它不是生产级平台，而是一个 Agent Harness 应用雏形。
