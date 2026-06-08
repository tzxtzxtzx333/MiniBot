# MiniBot

MiniBot 是一个从零实现的本地智能助手项目，不是 `nanobot` 的 fork。项目参考了 `nanobot` 的 Agent Runtime / Tool / Memory / 插件化分层思路，也参考了本地 CLI Agent 的 Harness、Hook、上下文治理与审计链路，但所有代码都在当前仓库内独立实现。

## 项目定位

- 本地优先的个人智能助手
- Harness-first 的统一执行链
- 支持 CLI / HTTP / Feishu 边界接入
- 支持 Tool Calling、Memory、治理、评测与报告
- 重点是”可运行、可审计、可验证”，不是大而全平台
- 支持预算受控多轮工具规划的真实 Agent Harness

## 核心结论

- DeepSeek / OpenAI-compatible 已接入真实模型路径
- fake model 仅用于测试、开发回归和 fake benchmark
- `/new` 已支持真实 LLM 压缩归档
- `python_exec` / `shell_exec` 已通过 Docker 沙箱执行
- `web_fetch` 已支持真实 HTTP provider
- 支持 DeepSeek、Tavily、QWeather、AMap MCP、Feishu WebSocket 等真实 provider 接入；真实联通依赖环境变量配置，同时保留 mock / fake 模式用于本地回归测试
- `weather` 当前是 API provider 边界，缺 key 返回 `weather_config_missing`
- `map_route` 当前是 AMap MCP adapter 边界，缺配置返回 `amap_mcp_config_missing`
- MCP 不是所有工具的统一入口，只用于外部 MCP provider 场景
- 简历数字指标只能来自 real report，例如 `reports/run_real_final.json`、`reports/run_real_with_key_v1.json`
- fake report 只用于开发回归，不作为简历指标来源

## 分层结构

```text
1. Channel Layer
2. Harness Layer
3. Tool Layer
4. Memory / Context Layer
5. Governance / Evaluation Layer
```

对应目录：

- `minibot/channels/`
- `minibot/harness/`
- `minibot/tools/`
- `minibot/memory/` 与 `minibot/context/`
- `minibot/governance/`、`minibot/sandbox/`、`minibot/evals/`

## 主要能力

### 1. Harness

统一入口是 `AgentLoop`。所有渠道消息先标准化为 `ChannelMessage`，再进入统一执行链：

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

### 2. 模型

配置字段：

```env
MINIBOT_MODEL_MODE=fake
MINIBOT_MODEL_PROVIDER=deepseek
MINIBOT_MODEL_BASE_URL=https://api.deepseek.com
MINIBOT_MODEL_API_KEY=
MINIBOT_MODEL_NAME=deepseek-chat
```

兼容旧字段：

```env
MINIBOT_BASE_URL=
MINIBOT_API_KEY=
```

规则：

- 默认运行是 `fake`
- `MINIBOT_MODEL_MODE=real` 时必须读取真实模型配置
- 缺关键配置时返回 `deepseek_config_missing`
- real 模式绝不 fallback 到 fake

### 3. 工具与治理

当前工具：

- 真实工具：`calculator`、`file_read`、`file_write`、`memory_search`、`memory_write`、`doc_summarize`
- 外部 provider：`web_fetch`、`web_search`、`weather`、`map_route`
- Docker 沙箱工具：`python_exec`、`shell_exec`

治理能力 — 三层治理机制（白名单自动执行 / 灰名单审批确认 / 黑名单阻断并审计）：

- schema 校验
- 白名单（whitelist）：低风险工具自动执行（calculator、file_read、web_fetch 等）
- 灰名单（graylist）：file_write / memory_write / python_exec / shell_exec 需进入 Pending Approval Queue
- 黑名单（blacklist）：支持工具级 blacklist 和高风险 shell 命令黑名单，命中后返回 `blocked_by_policy` 并写入 tool_trace / run record
- 审批（Pending Approval Queue + JSONL 审计存储）
- 重试与降级
- duplicate 去重
- 敏感信息脱敏
- partial success 聚合
- Docker 沙箱路由

### 4. 记忆与压缩 — MEMORY.md / HISTORY.md 两层主记忆架构 + Archives 压缩归档层

工作区：

```text
.minibot/
  MEMORY.md   ← 长期偏好 / 长期事实（全量注入系统提示）
  HISTORY.md  ← 近期对话（按 token 预算截断）
  archives/   ← 旧对话 LLM 压缩摘要归档，控制上下文长度并保留历史连续性
  sessions/
  runs/
  sandbox_workspace/
```

`/new` 会触发 `SummarizerAgent` 做归档压缩：

- fake mode：规则式摘要
- real mode：真实 LLM 摘要

### 5. 渠道

- CLI：`python -m minibot chat`
- HTTP：`python -m minibot http`
- Feishu WebSocket Bot 边界：`python -m minibot feishu`
- Feishu mock 回归：`python -m minibot feishu-mock examples/mock_feishu_event.json`

Feishu 当前结论：

- Feishu WebSocket 已完成真实联调，但默认 `status` 中可能因为未配置环境变量而显示 `feishu_config_present=false`
- 缺配置时返回 `feishu_config_missing`
- 缺 SDK 时返回 `feishu_sdk_not_installed`
- `feishu-mock` 仅用于本地回归，不代表已经真实联通飞书

### 6. 外部 provider 边界

- `web_fetch`：真实 HTTP provider
- `web_search`：支持 mock provider 和 Tavily real provider，并写入 provider status
- `ModelVerifier`：支持 fake verifier 和 real DeepSeek/OpenAI-compatible verifier
- `weather`：当前默认 mock，已保留 QWeather real API provider 边界
- `map_route`：当前默认 mock，已保留 AMap MCP adapter 边界

tool trace / report metadata 会记录：

```json
{
  "provider_status": "real|mock|missing|failed",
  "mock_provider": false,
  "real_provider": true,
  "mcp_provider": false
}
```

## 快速开始

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -e .[dev]
python -m minibot --help
python -m minibot status
```

## 常用命令

```bash
python -m minibot chat --message "calculate 128 * 64"
python -m minibot chat --message "计算 128 * 64"
python -m minibot chat --message "run python code print(1+1)"
python -m minibot chat --message "shell_exec echo hello"
python -m minibot chat --message "/new"
python -m minibot feishu
python -m minibot feishu-mock examples/mock_feishu_event.json
python -m minibot benchmark --mode fake --report reports/run_fake_final.json
python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json
python -m minibot compare reports/run_real_final.json reports/run_real_final.json
```

## 文档

- [架构说明](docs/architecture.md)
- [简历映射](docs/resume_mapping.md)
- [Demo 脚本](docs/demo_script.md)
- [设计决策](docs/decisions.md)
- [Feishu 接入说明](docs/feishu_setup.md)
- [最终验收](docs/final_acceptance.md)
- [Resume-Complete 缺口说明](docs/resume_complete_gap.md)

## 中文 trace 读取说明

JSON trace 与 report 统一使用 UTF-8 写入，并通过 `json.dump(..., ensure_ascii=False)` 落盘。PowerShell 查看 trace 时建议使用：

```powershell
Get-Content <path> -Raw -Encoding UTF8
```

## Human Review Queue

MiniBot now includes a local pending approval queue for graylisted tool calls.

- Pending approvals are stored in:
  - `.minibot/approvals/pending.jsonl`
  - `.minibot/approvals/resolved.jsonl`
- This is a local auditable review loop, not an enterprise approval system.
- High-risk shell commands still return `blocked_by_policy` and cannot be bypassed through approval.

CLI commands:

```bash
python -m minibot approvals list
python -m minibot approvals approve <approval_id>
python -m minibot approvals reject <approval_id>
```

## Context Benchmark

MiniBot supports context-governance benchmark profiles for estimated token analysis:

```bash
python -m minibot benchmark --mode fake --profile context-baseline --report reports/run_context_baseline.json
python -m minibot benchmark --mode fake --profile context-optimized --report reports/run_context_optimized.json
python -m minibot compare reports/run_context_baseline.json reports/run_context_optimized.json
```

These reports use `estimated tokens`, not provider-billed tokens. The fixed estimator is:

```text
estimated_tokens = ceil(len(text) / 4)
```

Benchmark reports now include:

```json
{
  "avg_dynamic_context_chars": 0,
  "avg_dynamic_context_tokens": 0,
  "avg_tool_specs_chars": 0,
  "human_review": {
    "pending_count": 0,
    "approved_count": 0,
    "rejected_count": 0
  }
}
```

`dynamic_context_tokens` excludes the fixed tool schema block and focuses on mutable context payload such as history, memory, archives, recalled snippets, and tool results.

Do not use token reduction percentages as resume numbers. The resume-safe statement is that MiniBot implements history truncation, memory compaction, placeholder cleanup, tool-output compression, hard truncation, and subagent-backed summary persistence.

Benchmark profiles (100+ cases, real-agent 12/12, safety 8/8, multiround 2/2):

```bash
python -m minibot benchmark --mode fake --scope core --profile approval --report reports/run_fake_approval.json
python -m minibot benchmark --mode fake --scope core --profile execution --report reports/run_fake_execution.json
python -m minibot benchmark --mode real --scope core --profile all-integrations --report reports/run_real_all_integrations.json
python -m minibot benchmark --mode fake --profile multiround --report reports/run_fake_multiround.json
```

### Multiround Profile

MiniBot 支持预算受控的多轮 observe → re-plan loop。多轮能力不再用整个 reasoning category 证明，而是用独立 `multiround` profile 证明。

`multiround` profile 当前包含：
- `multi_round_search_fetch_001`：验证 web_search → web_fetch 两轮工具链路；
- `multi_round_budget_stop_001`：验证重复工具调用触发 `duplicate_loop_detected` 停止机制。

Report 输出新增：
- `multiround_case_count`
- `multiround_passed_count`
- `multiround_pass_rate`

简历口径：支持预算受控的多轮 observe → re-plan loop，可按 default / real-agent / long-task profile 配置工具轮次、总工具调用数、运行时间和重复调用上限；每轮工具调用均经过审批、黑名单、去重、Docker 沙箱与 trace 审计。

## TaskStore

MiniBot 支持本地 JSONL 任务状态管理。task 可以通过 `tasks resume` 重新进入 AgentLoop，run trace 中自动记录 `task_id`。

```bash
python -m minibot tasks create --goal "计算 128 * 64"
python -m minibot tasks list
python -m minibot tasks show <task_id>
python -m minibot tasks cancel <task_id>
python -m minibot tasks resume <task_id>
```

task 状态生命周期：

```text
pending → running → completed
                  → waiting_approval
                  → failed
                  → cancelled
```

task.status 根据 AgentLoop run 结果自动更新：正常完成 → `completed`，触发审批 → `waiting_approval` + `pending_approval_id`，阻断/预算超限/工具失败 → `failed`。

存储位置：`.minibot/tasks/tasks.jsonl`（JSONL 追加写入，同一 task_id 以最后一条为准）。

## HTTP Approval API

MiniBot 在 HTTP 服务中内建了审批 API，与 CLI `approvals` 命令共享同一 JSONL store。

启动 HTTP 服务：

```bash
python -m minibot http --host 127.0.0.1 --port 8000
```

端点：

```http
GET  /approvals                          # 列出 pending 审批
POST /approvals/{approval_id}/approve    # 批准
POST /approvals/{approval_id}/reject     # 拒绝
```

关键语义：

- approve / reject **只改状态，不自动执行工具**；
- 用户需重新发送同一请求或通过 `tasks resume <task_id>` 才会继续执行；
- 黑名单命令不进入 approval，直接返回 `blocked_by_policy`；
- 不存在的 approval_id 返回结构化 404 错误。

### HTTP Auth（可选 Bearer Token）

HTTP Approval API 默认用于本地开发，建议绑定 `127.0.0.1`。如果需要局域网或远程访问，应设置 `MINIBOT_HTTP_AUTH_TOKEN`，或放在认证网关之后。

在 `.env` 或环境变量中设置：

```env
MINIBOT_HTTP_AUTH_TOKEN=your-secret-token
```

- 空值（默认）：不要求认证，向后兼容本地开发模式。
- 非空：以下端点要求 `Authorization: Bearer <token>` 头：
  - `GET  /approvals`
  - `POST /approvals/{approval_id}/approve`
  - `POST /approvals/{approval_id}/reject`
- `GET /status` 和 `POST /chat` 始终不需要认证。
- 缺少 Authorization 头返回 `401 {"error": "unauthorized"}`。
- Token 错误返回 `403 {"error": "forbidden"}`。

> **注意**：这不是生产级认证系统，仅作为最小安全边界。生产环境应使用反向代理认证网关（如 nginx + OAuth2 Proxy）。

## Status Health Check

`python -m minibot status` 和 `GET /status` 返回增强的健康检查：

```json
{
  "tasks_dir_exists": true,
  "task_count": 0,
  "pending_task_count": 0,
  "approval_pending_count": 0,
  "budget": {
    "agent_profile": "default",
    "max_tool_rounds": 3,
    "max_tool_calls_total": 10,
    "max_runtime_seconds": 60,
    "max_same_tool_calls": 2
  }
}
```

## Local Development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
python -m minibot --help
pytest -v
```

`.env.example` 包含所有可配置变量（model、provider、budget），复制为 `.env` 并按需填入真实 key。

## Real Provider Setup

从 `.env.example` 复制并填入真实配置：

```bash
cp .env.example .env
```

关键配置组：

| 配置组 | 变量前缀 |
|---|---|
| 模型 | `MINIBOT_MODEL_*` |
| Verifier | `MINIBOT_VERIFIER_*` |
| 搜索 | `MINIBOT_WEB_SEARCH_PROVIDER`, `TAVILY_*` |
| 天气 | `MINIBOT_WEATHER_*` |
| 地图 | `MINIBOT_MAP_PROVIDER`, `MINIBOT_AMAP_*` |
| 飞书 | `FEISHU_*` |
| 预算 | `MINIBOT_AGENT_PROFILE`, `MINIBOT_MAX_*` |

缺配置时各 provider 返回明确错误（如 `deepseek_config_missing`、`tavily_config_missing`），不会 fallback mock 冒充成功。

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

## Benchmark Evidence

关键 benchmark 证据（数字只来自 real report）：

| Profile | 结果 | 来源 |
|---|---|---|
| real-agent | 12/12 | `reports/run_real_agent.json` |
| safety | 8/8 | `reports/run_fake_safety_check.json` |
| multiround | 2/2 | `reports/run_fake_multiround.json` |
| execution | 5/5 | `reports/run_real_execution.json` |

固化 evidence 副本位于 `docs/evidence/`。若源报告不存在，需本地运行生成；不伪造报告。

## 最终定位

MiniBot 是一个具备真实模型、真实工具、任务状态、渠道内审批、安全治理、预算受控多轮规划与部署运行边界的真实 Agent Harness 应用雏形。

### Realistic Context Benchmark

Use the original `context-baseline/context-optimized` pair as a stress benchmark. For resume-facing token-reduction numbers, use:

```bash
python -m minibot benchmark --mode fake --profile context-realistic-baseline --report reports/run_context_realistic_baseline.json
python -m minibot benchmark --mode fake --profile context-realistic-optimized --report reports/run_context_realistic_optimized.json
python -m minibot compare reports/run_context_realistic_baseline.json reports/run_context_realistic_optimized.json
```

These reports still use `estimated_tokens = ceil(len(text) / 4)`, not provider-billed tokens.
