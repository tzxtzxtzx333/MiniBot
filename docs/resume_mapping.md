# MiniBot Resume Mapping

说明：

- 每一项都包含：简历表述、实现说明、对应代码文件、演示命令、测试文件、报告来源、当前状态
- 数字指标只允许来自：
  - `reports/run_real_execution.json`
  - `reports/run_real_safety.json`
  - `reports/run_real_all_integrations.json`
  - `reports/run_real_approval.json`

## 最终简历数字口径

### 可以写进简历的数字

- 116 个 JSON 工程回归用例
- safety profile 覆盖 8 个安全场景（三层治理、阻断、审计链路回归）
- real execution profile 覆盖 5 个核心执行 case（平均延迟约 2.60s，平均工具轮次 1.2）
- real-agent profile 覆盖 12 个端到端行为样例
- multiround profile 覆盖 2 个多轮 observe → re-plan case

### 不建议写进简历的数字

- Token reduction percentage
- all-integrations 的单次 pass_rate
- 任何未从 report 生成的手写数字

### 推荐简历表述

```text
MiniBot 智能体｜核心开发者

- 背景：针对 Agent 在长对话中上下文膨胀、工具调用失控等工程痛点，参考开源框架 nanobot 的架构思想新建 MiniBot——一个以 Harness 为核心的多层本地智能助手；通过 5 层模块化 / 插件化架构，统一管理多渠道接入、工具调用、Hook 治理与结构化记忆。
- 技术栈：Python，Agent Harness，Tool Calling，Context Management，SubAgent，Docker，DeepSeek。
- 多轮 Agent Loop：支持预算受控的多轮 observe → re-plan loop，可按 default / real-agent / long-task profile 配置工具轮次、总工具调用数、运行时间和重复调用上限；每轮工具调用均经过审批、黑名单、去重、Docker 沙箱与 trace 审计。多轮能力以独立 multiround profile 验证，覆盖 web_search→web_fetch 两轮链路与 duplicate_loop_detected 预算停止机制。
- Hook 机制：在 SessionStart、PreToolUse、PostToolUse 等节点构建事件拦截管线，支持 exact / regex 模式匹配与日志、审批、阻断、脱敏等 Action 注入，与核心 AgentLoop 解耦，实现非侵入式扩展。
- 结构化记忆：设计 MEMORY.md / HISTORY.md / Archives 多层记忆结构，支持长期偏好写入、近期对话检索与 /new 触发的 LLM 压缩归档，在保持记忆连续性的同时控制上下文长度。
- 上下文管理：自建多层上下文治理策略，覆盖历史消息截断、记忆压缩、占位清理、工具输出压缩、硬截断与子代理摘要固化，在长对话场景下控制上下文窗口增长。
- 工具安全与运行治理：实现三层治理机制（白名单自动执行 / 灰名单审批确认 / 黑名单阻断并审计），落地参数校验、Pending Approval Queue、Docker 沙箱隔离、敏感信息脱敏、重复调用去重与高风险命令黑名单阻断；支持 Partial Success 识别与自动重试降级，通过 safety profile 覆盖安全阻断与审计链路回归。
- 外部工具接入：接入 DeepSeek 作为真实模型后端，支持模型输出 tool_plan 驱动工具调用；实现 Feishu WebSocket、Tavily 搜索、QWeather 天气、AMap MCP 路线规划 / 周边 POI 等外部 provider 的接入边界，保留 mock / fake 模式用于本地回归测试。
- 评测与审计闭环：构建 116 个 JSON 工程回归用例，覆盖记忆召回、上下文治理、工具安全、外部集成与多步推理等维度；支持 pass_rate / tool_rounds / avg_latency / failure_category / tool_trace / verifier_reason 等指标自动汇总与版本回归对比。execution profile 回归核心执行链路（平均延迟约 2.60s，平均工具轮次 1.2）；safety profile 回归三层治理与阻断链路；real-agent profile 验证端到端行为样例；multiround profile 验证多轮 observe → re-plan 链路。
```

## 1. 基于 nanobot 架构思想设计 MiniBot

- 简历表述：基于 nanobot 架构思想独立设计并实现本地智能助手 MiniBot
- 实现说明：参考 Runtime / Tool / Memory / 插件化思路，但不是 fork
- 对应代码文件：`README.md`、`docs/decisions.md`、`minibot/app.py`
- 演示命令：`python -m minibot --help`
- 测试文件：`tests/test_cli.py`
- 报告来源：结构性能力，无单独数字；整体验证见 real report
- 当前状态：已真实完成

## 2. Harness 核心

- 简历表述：实现 Harness-first 的统一 AgentLoop 执行链
- 实现说明：所有渠道统一进入 `AgentLoop`
- 对应代码文件：`minibot/harness/agent_loop.py`
- 演示命令：`python -m minibot chat --message "calculate 128 * 64"`
- 测试文件：`tests/test_agent_loop.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 3. 5 层模块化 / 插件化架构

- 简历表述：实现 Channel / Harness / Tool / Memory-Context / Governance-Eval 五层模块化 / 插件化架构
- 实现说明：按职责拆分目录和运行时依赖
- 对应代码文件：`docs/architecture.md`、`minibot/`
- 演示命令：`python -m minibot status`
- 测试文件：`tests/test_cli.py`
- 报告来源：结构性能力，整体验证见 real report
- 当前状态：已真实完成

## 4. 多渠道接入

- 简历表述：支持 CLI、HTTP、Feishu 接入边界与 mock 回归
- 实现说明：统一 `ChannelMessage`
- 对应代码文件：`minibot/channels/base.py`、`minibot/channels/http_channel.py`、`minibot/channels/feishu_ws_channel.py`
- 演示命令：`python -m minibot feishu-mock examples/mock_feishu_event.json`
- 测试文件：`tests/test_channels.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：可配置真实接入 / mock 回归

## 5. Tool Calling

- 简历表述：实现统一 Tool Calling 协议与模型驱动工具决策
- 实现说明：统一 `ModelPlan` / `tool_plan` / `ToolDispatcher`
- 对应代码文件：`minibot/harness/model_client.py`、`minibot/subagents/tool_agent.py`
- 演示命令：`python -m minibot chat --message "calculate 128 * 64"`
- 测试文件：`tests/test_model_client_real.py`、`tests/test_agent_loop.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 6. Context Management

- 简历表述：实现上下文预算、召回、截断与压缩
- 实现说明：`ContextBuilder`、`TokenBudget`、`HistoryTruncator`
- 对应代码文件：`minibot/harness/context_builder.py`、`minibot/context/`
- 演示命令：`python -m minibot chat --message "/new"`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 7. SubAgent

- 简历表述：实现轻量 SubAgent 协作
- 实现说明：`MemoryAgent`、`SummarizerAgent`、`ToolAgent`、`VerifierAgent`
- 对应代码文件：`minibot/subagents/`
- 演示命令：`python -m minibot chat --message "/new"`
- 测试文件：`tests/test_subagents.py`
- 报告来源：run trace / real report
- 当前状态：已真实完成

## 8. Hook 机制

- 简历表述：实现可扩展 Hook Runtime
- 实现说明：事件、匹配器、动作注册表与 HookManager
- 对应代码文件：`minibot/hooks/`
- 演示命令：`python -m minibot chat --message "shell_exec rm -rf /"`
- 测试文件：`tests/test_hooks.py`
- 报告来源：run trace
- 当前状态：已真实完成

## 9. PreToolUse / PostToolUse / SessionStart

- 简历表述：覆盖关键生命周期 Hook 事件
- 实现说明：AgentLoop 在关键节点触发 Hook
- 对应代码文件：`minibot/harness/agent_loop.py`
- 演示命令：`python -m minibot chat --message "calculate 128 * 64"`
- 测试文件：`tests/test_hooks.py`
- 报告来源：run trace
- 当前状态：已真实完成

## 10. exact / regex 匹配

- 简历表述：支持 exact / regex Hook 匹配
- 实现说明：匹配器独立模块化
- 对应代码文件：`minibot/hooks/matchers.py`
- 演示命令：`python -m minibot chat --message "calculate 128 * 64"`
- 测试文件：`tests/test_hooks.py`
- 报告来源：run trace
- 当前状态：已真实完成

## 11. 审批、日志、阻断、脱敏模块注入

- 简历表述：支持审批、日志、阻断、脱敏等治理模块注入
- 实现说明：HookActionRegistry + ToolDispatcher 治理链
- 对应代码文件：`minibot/hooks/actions.py`、`minibot/harness/tool_dispatcher.py`
- 演示命令：`python -m minibot chat --message "shell_exec rm -rf /"`
- 测试文件：`tests/test_hooks.py`、`tests/test_governance.py`
- 报告来源：run trace / real report
- 当前状态：已真实完成

## 12. MEMORY.md

- 简历表述：实现长期记忆持久化
- 实现说明：显式“记住”写入 MEMORY
- 对应代码文件：`minibot/memory/store.py`
- 演示命令：`python -m minibot chat --message "remember I prefer Chinese replies"`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：run trace
- 当前状态：已真实完成

## 13. HISTORY.md

- 简历表述：HISTORY.md 存储近期对话（按相关性检索）
- 实现说明：每轮对话写入 HISTORY 与 session；`HistoryRetriever` 基于 token overlap + Jaccard 评分，按 query 相关性检索 top_k 历史片段注入上下文
- 对应代码文件：`minibot/memory/store.py`、`minibot/memory/history_retriever.py`
- 演示命令：`python -m minibot chat --message "python deploy"`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：run trace / context_summary 包含 `history_retrieval_mode=relevance`
- 当前状态：已真实完成

## 14. 对话轮次阈值自动压缩归档 + `/new` 手动压缩

- 简历表述：当对话轮次达到阈值或用户显式输入 `/new` 时，自动触发 LLM 对旧对话进行压缩归档
- 实现说明：
  - `/new` 触发 `compression_trigger = "manual_new"`，`MemoryStore.compact_history` 调用 `SummarizerAgent` 生成 archive
  - `auto_compact_enabled=true` 且 `turn_count > history_turn_compact_threshold` 时触发 `compression_trigger = "turn_threshold"`，自动截断 HISTORY 保留最近 `history_compact_keep_recent` 轮
  - archive metadata 记录 `summary_by`、`archive_mode`、`token_before`、`token_after`、`compression_trigger`、`history_turn_count_before`、`history_turn_count_after`
  - summarizer 失败时保留原 HISTORY，不丢数据
- 对应代码文件：`minibot/harness/agent_loop.py`、`minibot/memory/store.py`、`minibot/memory/compactor.py`、`minibot/memory/archive.py`、`minibot/subagents/summarizer_agent.py`
- 演示命令：`python -m minibot chat --message "/new"`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：archive 文件、run trace 中 compression_events
- 当前状态：已真实完成

## 15. 工具调用治理

- 简历表述：实现工具调用治理链
- 实现说明：policy、approval、retry、redaction、partial success、sandbox
- 对应代码文件：`minibot/harness/tool_dispatcher.py`、`minibot/governance/`
- 演示命令：`python -m minibot chat --message "shell_exec rm -rf /"`
- 测试文件：`tests/test_governance.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 16. 历史消息截断

- 简历表述：实现历史消息截断
- 实现说明：基于 token budget 控制历史窗口
- 对应代码文件：`minibot/context/history_truncator.py`
- 演示命令：`python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 17. 记忆压缩

- 简历表述：实现历史归档压缩
- 实现说明：MemoryCompactor 调用 SummarizerAgent 固化 archive
- 对应代码文件：`minibot/memory/compactor.py`
- 演示命令：`python -m minibot chat --message "/new"`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：archive 文件与 run trace
- 当前状态：已真实完成

## 18. 占位清理

- 简历表述：实现 PlaceholderCleaner
- 实现说明：清理空 tool_result、重复 system prompt、过长工具输出
- 对应代码文件：`minibot/context/placeholder_cleaner.py`
- 演示命令：`python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：run trace
- 当前状态：已真实完成

## 19. 硬截断

- 简历表述：实现上下文硬截断保护
- 实现说明：超预算时硬截断而不是让上下文无限增长
- 对应代码文件：`minibot/context/history_truncator.py`
- 演示命令：`python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json`
- 测试文件：`tests/test_memory_context.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 20. 子代理摘要固化

- 简历表述：实现 SubAgent 摘要落盘
- 实现说明：archive 文件头含 `summary_by: SummarizerAgent`
- 对应代码文件：`minibot/subagents/summarizer_agent.py`、`minibot/memory/archive.py`
- 演示命令：`python -m minibot chat --message "/new"`
- 测试文件：`tests/test_subagents.py`
- 报告来源：archive 文件
- 当前状态：已真实完成

## 21. 白名单 / 灰名单 / 黑名单 — 三层治理机制

- 简历表述：实现三层治理机制（白名单自动执行 / 灰名单审批确认 / 黑名单阻断并审计）
- 实现说明：白名单低风险工具自动执行；灰名单需审批确认；黑名单支持工具级 blacklist 和高风险 shell 命令黑名单，命中后返回 `blocked_by_policy` 并写入 tool_trace / run record
- 对应代码文件：`configs/policy.json`、`minibot/governance/policy_manager.py`
- 演示命令：`python -m minibot chat --message "shell_exec rm -rf /"`
- 测试文件：`tests/test_governance.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 22. 参数校验

- 简历表述：实现工具 schema 参数校验
- 实现说明：ToolRegistry 在 dispatch 前统一验证
- 对应代码文件：`minibot/tools/registry.py`
- 演示命令：`python -m minibot chat --message "write notes/missing.txt"`
- 测试文件：`tests/test_tools.py`
- 报告来源：failure category / tool trace
- 当前状态：已真实完成

## 23. Docker 沙箱隔离

- 简历表述：实现高风险工具 Docker 沙箱隔离
- 实现说明：`python_exec` / `shell_exec` 真实进入 Docker
- 对应代码文件：`minibot/sandbox/docker_executor.py`
- 演示命令：`python -m minibot chat --message "run python code print(1+1)"`
- 测试文件：`tests/test_docker_executor_real.py`
- 报告来源：`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 24. 敏感信息脱敏

- 简历表述：实现敏感信息脱敏
- 实现说明：redactor 处理 key / token / password / bearer 模式
- 对应代码文件：`minibot/governance/redactor.py`
- 演示命令：`python -m minibot chat --message "summarize my api_key is sk-test-123 token is abc123"`
- 测试文件：`tests/test_governance.py`
- 报告来源：run trace
- 当前状态：已真实完成

## 25. 重复调用去重

- 简历表述：实现重复工具调用去重
- 实现说明：DuplicateCallDetector 复用相同工具结果
- 对应代码文件：`minibot/governance/duplicate_detector.py`
- 演示命令：`python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json`
- 测试文件：`tests/test_governance.py`
- 报告来源：run trace / benchmark
- 当前状态：已真实完成

## 26. Partial Success

- 简历表述：实现多工具任务 Partial Success 判断
- 实现说明：部分成功时显式标记 `partial_success=true`
- 对应代码文件：`minibot/governance/partial_success.py`
- 演示命令：测试通过 stub `ModelPlan` 验证 mixed results
- 测试文件：`tests/test_governance.py`
- 报告来源：real report / run trace
- 当前状态：已真实完成

## 27. 自动重试降级

- 简历表述：实现自动重试与降级执行
- 实现说明：RetryManager 根据 failure category 重试并记录 downgrade
- 对应代码文件：`minibot/governance/retry_manager.py`
- 演示命令：`python -m minibot chat --message "simulate failed weather and give me travel advice"`
- 测试文件：`tests/test_governance.py`
- 报告来源：run trace / benchmark
- 当前状态：已真实完成

## 28. 115+ Benchmark

- 简历表述：构建 115+ JSON benchmark case
- 实现说明：覆盖 channel、context、memory、tools、safety、reasoning、regression
- 对应代码文件：`benchmarks/`
- 演示命令：`python -m minibot benchmark --mode fake --report reports/run_fake_final.json`
- 测试文件：`tests/test_evals.py`
- 报告来源：`reports/run_real_final.json`、`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 29. 6 项指标

- 简历表述：输出 pass_rate、tool_rounds、avg_latency、failure_category、tool_trace、verifier_reason 等指标
- 实现说明：metrics + report writer + benchmark runner 汇总真实运行结果
- 对应代码文件：`minibot/evals/metrics.py`、`minibot/evals/report_writer.py`、`minibot/evals/benchmark_runner.py`
- 演示命令：`python -m minibot benchmark --mode real --scope core --report reports/run_real_final.json`
- 测试文件：`tests/test_evals.py`
- 报告来源：`reports/run_real_final.json`、`reports/run_real_with_key_v1.json`
- 当前状态：已真实完成

## 30. 版本回归对比

- 简历表述：支持 benchmark report 回归对比
- 实现说明：compare 输出新增失败、修复失败和指标 delta
- 对应代码文件：`minibot/evals/compare_reports.py`
- 演示命令：`python -m minibot compare reports/run_real_final.json reports/run_real_final.json`
- 测试文件：`tests/test_evals.py`、`tests/test_cli.py`
- 报告来源：compare 输出 + real report

## Multiround Profile

- multiround profile 是多轮 Agent loop 的主证明。
- reasoning category 是混合类目，不作为最终通过率口径。
- multiround profile 当前包含：
  - `multi_round_search_fetch_001`：验证 web_search → web_fetch 两轮工具链路；
  - `multi_round_budget_stop_001`：验证重复工具调用触发 `duplicate_loop_detected` 停止机制。
- Report 输出新增：`multiround_case_count`、`multiround_passed_count`、`multiround_pass_rate`。
- 简历口径：支持预算受控的多轮 observe → re-plan loop，可按 default / real-agent / long-task profile 配置工具轮次、总工具调用数、运行时间和重复调用上限；每轮工具调用均经过审批、黑名单、去重、Docker 沙箱与 trace 审计。
- 演示命令：`python -m minibot benchmark --mode fake --profile multiround --report reports/run_fake_multiround.json`

### 最终简历附加表述

支持 TaskStore 管理任务状态，任务可通过 tasks resume 重新进入 AgentLoop，run trace 记录 task_id；支持 Task approval E2E（create → resume → waiting_approval → approve/reject → resume → completed/failed）；支持 HTTP Approval API 在服务内查看和处理灰名单工具审批，可选 Bearer Token 认证边界；补齐 .env.example、status health check 和启动脚本等部署运行边界。MiniBot 已打通模型调用、工具治理、任务状态、审批、安全策略、多轮工具规划与部署边界等核心链路，是一个 Harness-first 本地 Agent 框架原型。

## Context Token Reduction

- 对比报告来源：`reports/run_context_baseline.json`、`reports/run_context_optimized.json`、`reports/run_context_realistic_baseline.json`、`reports/run_context_realistic_optimized.json`
- 统计口径：`estimated tokens`
- 估算规则：`ceil(len(text) / 4)`
- 已实现历史截断、记忆压缩、占位清理、工具输出压缩、硬截断和子代理摘要固化等上下文治理策略
- context profile 用于评估 estimated token 变化，但当前不作为简历数字
- 注意：这不是模型厂商实际计费 token
- 当前状态：已真实完成

## 外部 provider 边界说明

- `web_fetch`：已真实完成
- `web_search`：支持 Tavily real provider；mock 回归不作为真实外部接入证明
- `ModelVerifier`：支持 fake / real verifier；real 缺配置或错误必须写入 report
- `weather`：可配置真实接入 / 默认 mock
- `map_route`：可配置 MCP 接入 / 默认 mock
- `feishu`：Feishu WebSocket 接入边界代码已完成；真实联通依赖飞书开放平台配置，默认 status 因未配置环境变量显示 `feishu_config_present=false`
## Human Review Queue

- Resume-safe wording:
  - "Implemented a local pending approval queue for graylisted tool calls with auditable JSONL storage and replay-based execution."
- Do not describe this as an enterprise approval system.
- Evidence:
  - `.minibot/approvals/pending.jsonl`
  - `.minibot/approvals/resolved.jsonl`
  - benchmark/report top-level `human_review`

## Benchmark Profiles

- `approval` proves the human-review queue behavior itself.
- `execution` proves graylisted tools can execute after explicit allow semantics during benchmarking.
- `all-integrations` is reserved for real external integrations and should not rely on fake-era `MiniBot echo:` assertions.

## Realistic Context Benchmark

- Resume-facing context reduction numbers should come from:
  - `reports/run_context_realistic_baseline.json`
  - `reports/run_context_realistic_optimized.json`
  - compare output between those two reports
- Keep `context-baseline/context-optimized` as the stress benchmark.
- Token numbers remain estimated with `ceil(len(text) / 4)`.
