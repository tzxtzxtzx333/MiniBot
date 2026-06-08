# Final Metrics

- `benchmark_case_count = 107`
- `real-agent profile = 12/12`, `pass_rate = 100%`
- `safety counted cases = 8/8`, `pass_rate = 100%`（9 total, 1 pending: `requires_memory_write_in_blacklist`）
- `multiround profile = 2/2`, `pass_rate = 100%` — 覆盖 web_search→web_fetch 两轮链路与 duplicate_loop_detected 预算停止机制
- `TaskStore = supported` — tasks create / list / show / cancel / resume
- `Task approval E2E = supported` — create → resume → waiting_approval → approve/reject → resume → completed/failed
- `HTTP Approval API = supported` — GET /approvals, POST approve / reject
- `HTTP Approval API token boundary = supported` — `MINIBOT_HTTP_AUTH_TOKEN` 可选 Bearer Token 认证
- `Status health check = includes tasks / approvals / budget / archives`
- `Deployment boundary = .env.example, scripts/, .minibot/logs/`
- `pytest = 77 passed`（channels 34 + docs 15 + tasks CLI/resume/e2e 28）

## Notes

- real-agent 12/12 来自已配置 API key 的历史运行（`docs/evidence/run_real_agent.json`），当前本地缺失 API key 不可重跑。
- safety profile 8/8 counted，1 pending case 不纳入 pass_rate 统计。
- multiround 2/2 在 fake 模式下运行，验证预算受控多轮 observe → re-plan loop。
- Context governance evidence 来自 `context-baseline/context-optimized` 和 `context-realistic-baseline/context-realistic-optimized`。
- Context benchmark tokens 为 `estimated tokens = ceil(len(text) / 4)`，非 provider-billed tokens。
- Evidence reports: `docs/evidence/run_real_agent.json`, `docs/evidence/run_fake_safety_check.json`, `docs/evidence/run_fake_multiround.json`。
- 若源报告不存在，需要本地运行生成；不伪造 JSON 报告。
