# Resume Bullet — Final

以下是经过项目收口补强后，可以稳妥写入简历的最终版本。

## 项目描述

```text
MiniBot 任务执行型 Agent Harness｜核心开发者

技术栈：Python、Agent Harness、Tool Calling、Context Management、Docker、DeepSeek

- 针对 Agent 长对话上下文膨胀、工具调用失控、运行过程难审计等问题，参考 nanobot 的 Agent Runtime / Tool / Memory / 插件化分层思想，独立实现 Harness-first 本地智能助手。
- 设计统一 AgentLoop 执行链，将 SessionStart、MemoryRecall、ContextBuild、ModelPlanning、ToolExecution、VerifierCheck、HistoryPersist、RunReportPersist 等生命周期事件标准化，支撑多轮工具调用、trace 记录与结果回放。
- 实现 Hook Runtime，在 SessionStart、PreToolUse、PostToolUse 等节点支持 exact / regex 匹配，并提供日志、审批、阻断、脱敏等 Action 扩展点，使治理逻辑与核心循环解耦。
- 构建 MEMORY.md / HISTORY.md / Archives 三层记忆结构，支持长期事实写入、近期历史相关性检索、/new 与轮次阈值触发压缩归档，并结合历史截断、placeholder 清理、工具输出压缩与硬截断控制上下文预算。
- 实现统一 Tool Calling 与工具治理链，覆盖 schema 校验、白/灰/黑名单、Pending Approval Queue、Docker 沙箱、敏感信息脱敏、重复调用去重、retry/downgrade 与 Partial Success 识别；安全回归测试 8 个计入场景保持 100% 通过率。
- 构建 Benchmark / Report / Compare 评测框架，覆盖工具调用、记忆召回、上下文治理、工具安全、多轮推理与真实 Agent 执行等场景；沉淀 115+ 固定 Benchmark case，并在更贴近真实长对话的工程稳健性实验中，将平均上下文注入长度降低 36.45%。
```

## 口径说明

```text
- nanobot 表述：参考 nanobot 的 Agent Runtime / Tool / Memory / 插件化分层思想，独立实现 MiniBot，不是 fork；
- fake / real 区分：fake mode 用于工程链路回归，real mode 用于真实模型回答质量；
- 36.45%：来源于 context_robust_realistic fake mode 工程稳健性实验，衡量 dynamic_context_chars，不代表真实模型 token 成本；
- 8/8：来源于 safety profile counted cases，pending / skipped 不计入 denominator；
- 115+：是 MiniBot 固定工程回归 case 数，不是公开模型排行榜 benchmark；
- 外部 provider：DeepSeek、Tavily、QWeather、AMap MCP、Feishu 均为可配置接入边界，真实运行依赖环境变量配置。
```

## 不建议写入简历的内容

- `context_ablation` 的 90.32%（构造 filler text 消融实验）；
- `evidence_compression_realistic` 的压缩率（需 real mode 验证）；
- `taskplan_execution` 的 task_success_rate（需 real mode 验证）；
- `tool_governance` 新实验的 pass_rate（需 real mode 验证）；
- fake mode 的 `answer_pass_rate`（不作为真实回答质量）；
- `all-integrations` 的单次 pass_rate；
- Token reduction percentage；
- 任何未从 raw report 生成的手写数字。
