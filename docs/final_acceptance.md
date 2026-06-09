# Final Acceptance

## 当前完成能力清单

- 真实模型接入：已完成
- 真实 Tool Calling 决策：已完成
- `/new` 真实 LLM 压缩归档：已完成（支持 `manual_new` 手动触发与 `turn_threshold` 阈值自动触发）
- HISTORY.md 相关性检索：已完成（token overlap + Jaccard 评分，token budget 下 top_k 注入上下文）
- TaskPlan 任务规划执行闭环：已完成（PlannerAgent / TaskExecutor / StepVerifier / ReplannerAgent / plan CLI）
- Planner benchmark 回归：已完成（4/4，planner_file_report / approval_resume / failure_replan / evidence_context）
- Docker 沙箱执行：已完成
- fake / real benchmark：已完成
- Feishu WebSocket Bot 接入边界：已完成
- `web_fetch` 真实 HTTP provider：已完成
- `web_search` Tavily real provider：已完成
- `ModelVerifier` fake / real verifier：已完成
- `weather` provider 边界：已完成
- `map_route` AMap MCP 边界：已完成

## P0 已完成项

1. DeepSeek / OpenAI-compatible 真实模型接入
2. 真实模型参与 tool calling 决策
3. `/new` 真实 LLM 压缩归档（含对话轮次阈值自动触发）
4. HISTORY.md 按相关性检索近期对话（token overlap + top_k 注入上下文）
5. `python_exec` / `shell_exec` Docker 沙箱执行
5. benchmark fake / real mode
6. report 记录 fake / real mode 与能力状态
7. 简历指标来源切换到 real report

## P1 已完成项

1. Feishu WebSocket Bot 接入路径边界
2. `feishu-mock` 回归
3. `web_fetch` 真实 HTTP provider
4. `weather` API provider 边界
5. `map_route` AMap MCP adapter 边界
6. provider 状态进入 trace / report

## 外部接入口径

- 支持 DeepSeek、Tavily、QWeather、AMap MCP、Feishu WebSocket 等真实 provider 接入；真实联通依赖环境变量配置，同时保留 mock / fake 模式用于本地回归测试。
- Feishu WebSocket 已完成真实联调，但默认 status 中可能因为未配置环境变量而显示 `feishu_config_present=false`；`feishu-mock` 用于回归测试。

## 仍依赖外部配置的能力

- DeepSeek real mode：缺配置时 `deepseek_config_missing`
- Feishu real mode：缺配置时 `feishu_config_missing`
- Weather real provider：缺配置时 `weather_config_missing`
- AMap MCP provider：缺配置时 `amap_mcp_config_missing`

## 当前仍为 mock 回归的能力

- `weather` 默认路径
- `map_route` 默认路径
- `feishu-mock`

## 不能写进简历的内容

- “已真实联通飞书生产 Bot”
- “weather 已真实接入第三方天气 API”
- “map_route 已真实接入 AMap MCP”
- 任何来自 fake report 的数字指标
- Token reduction percentage
- all-integrations 的单次 pass_rate

## 可以写进简历的内容

- 实现 DeepSeek / OpenAI-compatible 真实模型接入
- 实现统一 `tool_plan` JSON Tool Calling
- 实现 `/new` LLM 压缩归档与轮次阈值自动压缩归档（`manual_new` / `turn_threshold`）
- 实现 HISTORY.md 按相关性检索近期对话（token overlap + top_k 注入上下文）
- 实现 Docker 沙箱执行高风险工具
- 实现 Feishu WebSocket Bot 接入路径与 mock 回归
- 实现 `web_fetch` 真实 HTTP provider 与 provider 状态标记
- 实现 benchmark / compare / real report 审计链
- 构建 107 Benchmark 任务集
- 安全回归测试 8 个场景 100% 通过（safety profile 8/8 counted）
- real execution profile 5/5 case 通过，平均延迟约 2.60s，平均工具轮次 1.2
- real-agent profile 12/12
- multiround profile 2/2，支持预算受控多轮 observe → re-plan loop
- TaskStore 任务状态管理（create / list / show / cancel / resume）
- Task approval E2E：create → resume → waiting_approval → approve/reject → resume → completed/failed
- HTTP Approval API（GET /approvals, POST approve / reject），与 CLI approval 共享 store
- HTTP Approval API 可选 Bearer Token 认证边界（`MINIBOT_HTTP_AUTH_TOKEN`）
- Status health check 包含 tasks / approvals / budget 字段
- 部署运行边界：.env.example 完整化、scripts/ 启动脚本、.minibot/logs/ 目录

## fake report 路径

- `reports/run_fake_final.json`

## real report 路径

- `reports/run_real_final.json`
- `reports/run_real_with_key_v1.json`

## 最终验收命令结果

### CLI / 渠道

- `python -m minibot --help`：通过
- `python -m minibot status`：通过
- `python -m minibot chat --message "calculate 128 * 64"`：通过
- `python -m minibot chat --message "计算 128 * 64"`：通过
- `python -m minibot chat --message "run python code print(1+1)"`：通过
- `python -m minibot chat --message "shell_exec echo hello"`：通过
- `python -m minibot chat --message "shell_exec rm -rf /"`：通过，返回阻断结果
- `python -m minibot chat --message "/new"`：通过
- `python -m minibot feishu`：返回 `feishu_config_missing`
- `python -m minibot feishu-mock examples/mock_feishu_event.json`：通过

### benchmark / compare

- `python -m minibot benchmark --mode fake --report reports/run_fake_final.json`：通过
- `python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json`：已生成 report，当前环境缺 key，返回 `deepseek_config_missing`
- `python -m minibot compare reports/run_real_final.json reports/run_real_final.json`：通过

### pytest

- `pytest -v`：最终结果 `136 passed`

## benchmark 结果摘要

### fake

- 报告：`reports/run_fake_final.json`
- `total_cases=104`（含 pending 用例）
- `counted_cases≈100`
- `pass_rate` 取决于运行环境

### real

- 报告：`reports/run_real_final.json`
- 当前环境缺 DeepSeek 配置
- `missing_capabilities=["deepseek_config_missing"]`
- report 已生成，没有伪造通过

## compare 结果摘要

## Context Benchmark

- baseline report: `reports/run_context_baseline.json`
- optimized report: `reports/run_context_optimized.json`
- compare 输出包含：
  - `avg_prompt_tokens_before`
  - `avg_prompt_tokens_after`
  - `token_reduction_rate`
  - `avg_dynamic_context_tokens_before`
  - `avg_dynamic_context_tokens_after`
  - `dynamic_token_reduction_rate`
- token 口径为 `estimated tokens = ceil(len(text) / 4)`
- `dynamic_token_reduction_rate` 不包含固定 `tool_specs`

- `new_failures=[]`
- `fixed_failures=[]`
- 与自身比较时所有 delta 为 `0`

## 最终简历指标提取位置

只允许从以下 real report 提取数字指标：

- `reports/run_real_execution.json`
- `reports/run_real_safety.json`
- `docs/evidence/final_metrics.md`

可提取字段示例：

- `pass_rate`
- `tool_rounds`
- `avg_latency`
- `docker_available`
- `capability_status`
- `mock_tools_used`
- `real_tools_used`

## Known Issues

- PowerShell 默认编码下直接读取 trace 可能出现中文显示异常
- 建议使用：

```powershell
Get-Content <path> -Raw -Encoding UTF8
```

## Human Review Queue

- Local pending approval queue implemented for graylisted tools
- Storage:
  - `.minibot/approvals/pending.jsonl`
  - `.minibot/approvals/resolved.jsonl`
- CLI:
  - `python -m minibot approvals list`
  - `python -m minibot approvals approve <approval_id>`
  - `python -m minibot approvals reject <approval_id>`
- Approved requests execute only when the user replays the same request
- Rejected requests remain blocked
- Blacklisted commands still return `blocked_by_policy`
- Benchmark/report now include:

```json
{
  "human_review": {
    "pending_count": 0,
    "approved_count": 0,
    "rejected_count": 0
  }
}
```

## Benchmark Profiles

- `approval`: verify graylisted tools return `approval_required` and enter the pending queue
- `execution`: temporarily auto-approve graylisted tools during benchmark execution so file, memory, Docker, and partial-success paths can be validated
- `all-integrations`: validate external integrations such as DeepSeek, Tavily, QWeather, AMap MCP, and `web_fetch`
- `multiround`: 验证预算受控多轮 observe → re-plan loop；包含 web_search→web_fetch 两轮链路与 duplicate_loop_detected 预算停止机制；多轮能力不再用整个 reasoning category 证明
- In `real` mode, old fake-era `MiniBot echo:` assertions should be treated as `final_response_not_empty`

## Realistic Context Benchmark

- Stress benchmark:
  - `reports/run_context_baseline.json`
  - `reports/run_context_optimized.json`
- Resume-facing benchmark:
  - `reports/run_context_realistic_baseline.json`
  - `reports/run_context_realistic_optimized.json`
- Context benchmark is retained as governance evidence, not as a resume-number source.
- Token estimator remains `ceil(len(text) / 4)`, which is estimated tokens rather than provider-billed tokens.
