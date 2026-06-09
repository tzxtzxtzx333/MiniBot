# MiniBot 简历表述指导

> **原则**：诚实、可验证、不夸大。每一项简历表述都应该能被代码、测试或 benchmark report 支撑。

## 一、可以写在简历上的能力

### 1. 架构与工程能力

| 简历表述 | 证据 | 备注 |
|---|---|---|
| 设计并实现 Harness-first 的五层本地 Agent 框架 | `minibot/harness/agent_loop.py`（~700 行）| 核心卖点 |
| 统一 AgentLoop 执行链，支持 CLI / HTTP / Feishu 多渠道接入 | `minibot/channels/` 四个 channel | 所有渠道复用同一 `ChannelMessage` 协议 |
| 实现 Hook 事件拦截管线，支持 exact / regex 匹配与动作注入 | `minibot/hooks/` — HookManager + 匹配器 + 动作注册表 | 当前 hooks.json 为空，但代码完整可演示 |
| 实现模块化工具协议（ToolSpec / ToolResult / BaseTool）与 14 个注册工具 | `minibot/tools/` | 工具可独立注册、独立测试 |
| 三层工具治理机制（白名单/灰名单/黑名单） | `configs/policy.json` + `minibot/governance/policy_manager.py` | 含审批队列 + 命令黑名单 |

### 2. 工具与外部接入

| 简历表述 | 证据 | 备注 |
|---|---|---|
| 实现 AST 安全计算器、工作区文件读写、记忆读写、文档摘要等本地工具 | `minibot/tools/calculator.py`, `file_ops.py`, `memory_tools.py`, `doc_summarize.py` | 不依赖任何外部服务 |
| 实现 Docker 沙箱隔离执行 python_exec / shell_exec | `minibot/sandbox/docker_executor.py` | Docker 不可用时返回 `docker_unavailable` |
| 使用 urllib（零第三方 HTTP 依赖）实现 DeepSeek / OpenAI-compatible 模型接入 | `minibot/harness/model_client.py:479-887` | 不绑定厂商 SDK |
| 使用 urllib 实现 web_fetch（真实 HTTP provider）和 Tavily 搜索接入 | `minibot/tools/web_fetch.py`, `web_search.py` | web_fetch 无 mock fallback |

### 3. 记忆与上下文

| 简历表述 | 证据 | 备注 |
|---|---|---|
| 设计 MEMORY.md / HISTORY.md / Archives 三层记忆结构 | `minibot/memory/store.py`, `archive.py`, `compactor.py` | 文件级实现，无数据库依赖 |
| 实现基于 token overlap + Jaccard 的相关性历史检索 | `minibot/memory/history_retriever.py` | 适合短文本场景 |
| 实现 /new 手动 + 轮次阈值自动两种压缩归档触发机制 | `minibot/harness/agent_loop.py:482-517` | 含 `manual_new` 和 `turn_threshold` 两种 trigger |
| 实现上下文 token budget 截断、占位清理、大工具输出 evidence 压缩 | `minibot/context/` + `minibot/evidence/` | 多级上下文治理策略 |

### 4. 评测与审计

| 简历表述 | 证据 | 备注 |
|---|---|---|
| 构建 116 个 JSON 工程回归用例，覆盖 8 个场景类别 | `benchmarks/` 目录 | 用于版本回归、trace 审计和失败归因，不是科研 benchmark |
| 实现 RuleVerifier + ModelVerifier 双重验证 | `minibot/evals/rule_verifier.py`, `model_verifier.py` | ModelVerifier 可选 real mode |
| 实现 benchmark report 生成与版本回归对比（compare） | `minibot/evals/benchmark_runner.py`, `compare_reports.py` | 输出 JSON + Markdown |
| 每次运行产生完整 JSON run trace，含 lifecycle_events、tool_trace、context_metrics | `minibot/harness/run_recorder.py` | `.minibot/runs/*.json` |
| real execution profile 5 个 case 通过，平均延迟 ~2.60s | `reports/run_real_execution.json` | 需要真实 API key 生成 |

### 5. 推荐简历 bullet（可直接使用）

```text
MiniBot — 本地 Agent 框架原型 | 核心开发者

• 设计并实现 Harness-first 的五层 Agent 框架（Channel / Harness / Tool /
  Memory-Context / Governance-Evaluation），统一 CLI、HTTP、Feishu
  WebSocket 多渠道接入，所有消息通过统一的 AgentLoop 执行链编排。

• 实现 14 个注册工具的模块化协议（ToolSpec / ToolResult / BaseTool），
  覆盖本地计算、文件读写、记忆检索、外部 API 调用与 Docker 沙箱执行；
  工具调用经过三层治理（白名单/灰名单/黑名单）+ schema 校验 + 审批 +
  去重 + 脱敏 + 重试降级共 8 步治理链。

• 设计 MEMORY.md / HISTORY.md / Archives 三层记忆结构，实现基于
  Jaccard 的相关性检索、token budget 截断、/new 手动与轮次阈值自动
  压缩归档，以及大工具输出的 evidence 压缩存储。

• 构建 116 个 JSON 工程回归用例（覆盖 memory / context / tools / safety /
  reasoning / planner 等 8 个类别），实现 RuleVerifier + ModelVerifier
  双重验证与 compare 版本回归对比；每次运行输出完整 JSON trace
  （含 lifecycle_events、tool_trace、context_metrics）。

• 使用纯 urllib（零第三方 HTTP SDK）接入 DeepSeek / OpenAI-compatible、
  Tavily 搜索、QWeather 天气等外部 provider，严格区分 fake/mock/real
  三种模式，缺配置返回明确错误不 fallback。
```

---

## 二、不建议写或必须降调的能力

### ❌ 不要写

| 表述 | 原因 |
|---|---|
| "已真实联通飞书生产 Bot" | Feishu WebSocket 只完成了接入边界代码，未在生产环境长期运行 |
| "weather 已真实接入第三方天气 API" | QWeather API 边界代码完成，但未持有有效 key 做端到端验证 |
| "map_route 已真实接入 AMap MCP" | AMap MCP adapter 边界代码完成，但未配置真实 MCP endpoint 验证 |
| "生产级 Agent 平台" / "完整多智能体系统" | 这不是项目定位 |
| 任何来自 fake report 的数字作为能力证明 | fake report 只证明开发回归链路存在 |

### ⚠️ 必须降调（加了限定才能说）

| 表述 | 正确口径 |
|---|---|
| Token 缩减 | 只能写 "context chars 口径的工程实验"，必须注明 `estimated_tokens = ceil(len(text)/4)` 且非真实 LLM 计费 token |
| SubAgent | 只能写 "轻量 SubAgent 协作"（MemoryAgent、SummarizerAgent、ToolAgent、VerifierAgent），不能暗示是完整多智能体系统 |
| Feishu | 只能写 "Feishu WebSocket Bot 接入边界" |
| Weather/AMap | 只能写 "provider 接入边界，默认 mock" |

---

## 三、36.45% 指标的正确口径

### 这个数字是什么

- **来源**：`context_robust_realistic` 实验（fake mode 工程稳健性测试）
- **衡量口径**：`dynamic_context_chars`（可变上下文字符数）
- **对比方式**：baseline vs optimized 条件下 `avg_dynamic_context_chars` 的变化率
- **不包括**：固定的 tool_specs（工具定义块）

### 这个数字不是什么

- ❌ 不是真实 LLM token 成本下降
- ❌ 不是 API 调用费用节省
- ❌ 不是模型计费 token 缩减
- ❌ 不是 real mode 数据

### 如果面试被问到这个数字

> "这个 36.45% 是 context chars 口径的工程实验数据，来自 fake mode 下的 context_robust_realistic 实验。具体衡量的是：在多轮对话压力测试中，开启上下文治理策略（截断、压缩、占位清理、evidence 压缩）后，动态上下文注入长度相比 baseline 减少了约 36%。这个数字用的是 `estimated_tokens = ceil(len(text)/4)` 的字符折算，不是模型厂商实际计费 token。它证明的是我的上下文治理管线在工程上有效，而不是一个生产成本指标。"

### 建议

- **简历上不写这个数字**，改为定性描述上下文治理策略
- 如果面试官问到具体数字，按上述口径解释
- 更好的替代表述："实现了历史截断、记忆压缩、占位清理、工具输出压缩、硬截断和子代理摘要固化等上下文治理策略"

---

## 四、各 Provider 的正确口径

### Feishu

```
✅ 可以写：
  "实现 Feishu WebSocket Bot 接入边界，支持本地 mock 回归测试"

❌ 不要写：
  "已接入飞书" / "已联通飞书生产环境"

面试解释：
  "Feishu WebSocket 的接入代码已完成（消息解析、事件分发、回复构建），
   但由于飞书开放平台需要企业资质和长期 token 维护，我没有做生产联调。
   本地 mock 模式可以完整验证消息处理链路。如果面试官需要，我可以演示
   feishu-mock 命令的完整执行流程。"
```

### Weather (QWeather)

```
✅ 可以写：
  "实现天气查询工具，保留 QWeather API 接入边界，默认使用 mock 数据回归"

❌ 不要写：
  "已接入 QWeather 真实天气 API"

面试解释：
  "weather 工具的 QWeather API 调用代码是完整的（城市查询 + 实时天气两个
   端点，含 gzip/deflate 解压），但因为 QWeather 需要付费 API key，当前
   默认使用 mock provider。MINIBOT_WEATHER_PROVIDER=real 时走真实路径，
   缺 key 返回 weather_config_missing，不 fallback mock。"
```

### AMap MCP (map_route / map_poi_search)

```
✅ 可以写：
  "实现路线规划和周边 POI 搜索工具，保留 AMap MCP adapter 接入边界"

❌ 不要写：
  "已接入高德地图 MCP" / "已实现实时路线规划"

面试解释：
  "map_route 和 map_poi_search 两个工具实现了完整的 MCP adapter 接入边界
   代码，但 MCP endpoint 配置需要高德开放平台的企业资质。当前默认使用
   mock provider 返回模拟数据用于回归测试。"
```

### SubAgent

```
✅ 可以写：
  "实现轻量 SubAgent 协作（MemoryAgent、SummarizerAgent、ToolAgent、
   VerifierAgent）"

❌ 不要写：
  "实现多智能体协作系统" / "完整 SubAgent 架构"

面试解释：
  "四个 SubAgent 各自负责一个明确的小任务：MemoryAgent 判断是否持久化记忆，
   SummarizerAgent 做对话压缩摘要（fake 模式规则式，real 模式调 LLM），
   ToolAgent 做工具调用提取与调度，VerifierAgent 做轻量响应验证。
   这不是完整的多智能体系统，而是把 AgentLoop 中一些独立职责抽成 Agent
   便于测试和替换。"
```

---

## 五、面试可说 / 不建议说速查

| 场景 | ✅ 可说 | ❌ 不建议说 |
|---|---|---|
| Benchmark | 构建 116 个工程回归用例，辅助验证核心链路 | 自研权威 Benchmark 评测体系 |
| Benchmark 结果 | fake mode 下 counted case 全通过；real mode 下 execution 5 个 case 通过 | 全部 case 在 real mode 下 100% 通过 |
| 上下文治理 | 上下文治理策略能降低动态上下文注入长度 | 真实 token 成本下降 36.45% |
| 上下文指标 | `dynamic_context_chars` 口径的工程实验数据 | 模型厂商计费 token 缩减 |
| SubAgent | 轻量 SubAgent 分工（MemoryAgent、SummarizerAgent、ToolAgent、VerifierAgent） | 完整多智能体平台 / 多 Agent 协作系统 |
| 测试 | 全量测试通过（387 passed, 0 errors） | 测试覆盖率 100% / 零缺陷 |
| 渠道 | 支持 CLI / HTTP / Feishu WebSocket 接入边界 | 已完成多渠道生产部署 |
| 外部 provider | 可配置的外部 provider 接入边界，fake/mock/real 严格区分 | 已接入多个生产级外部服务 |
| 安全治理 | 三层治理机制（白名单/灰名单/黑名单）+ 审批 + Docker 沙箱 | 企业级安全防护体系 |

---

## 六、面试中如何解释项目边界（常见三问）

### 当面试官问"这个项目最大的局限是什么"

```
回答方向：
1. 规模：116 个 benchmark case 是固定 JSON，不是持续更新的生产数据集。
2. 真实度：部分外部 provider（Weather、AMap、Feishu）只有接入边界代码，
   没有做过端到端的真实生产联调。
3. 模型依赖：real mode 只能证明"能通"，没有做过 prompt 优化、多模型
   对比、延迟/成本调优。
4. 部署：没有容器化、CI/CD、监控告警等生产基础设施。
5. 并发：AgentLoop 是单线程的，HTTP channel 用 ThreadingHTTPServer，
   不适合高并发场景。
```

### 当面试官问"你觉得哪些地方做得比较好"

```
回答方向：
1. fake/real 严格隔离：缺 key 返回明确错误不 fallback，这是工程纪律。
2. provider 状态全量写入 trace：每个工具结果都有 provider_status
   (real/mock/missing/failed)，审计可追溯。
3. 工具治理链完整：8 步治理不是摆设，每步都有独立模块和单测。
4. benchmark 双验证器：rule + model 双重验证，不是只看 pass/fail。
5. 零第三方 HTTP 依赖：所有 HTTP 调用都用 stdlib urllib，依赖极小。
```

### 当面试官问"如果继续做会做什么"

```
回答方向（按优先级）：
1. 用真实 API key 跑一轮完整的 real benchmark，更新简历数字。
2. 给 hooks.json 补 3-5 条实际规则，让 Hook 管线可演示。
3. 加 prompt cache / 多轮对话优化，降低真实 token 消耗。
4. 用 asyncio 重构 HTTP channel，支持并发。
5. 写一个简单的 Web UI（Gradio / Streamlit），方便面试演示。
```

---

## 七、Benchmark 数字速查

| 指标 | 数值 | 来源 | 模式 | 备注 |
|---|---|---|---|---|
| Benchmark case 总数 | 116 | `python -m minibot status` | — | JSON 文件计数 |
| Default profile | 85 passed | `reports/run_fake_final.json` | fake | rule verifier |
| Safety profile | 8/8 | `reports/run_fake_safety_check.json` | fake | 三层治理阻断链路 |
| Execution profile | 5/5 | `reports/run_real_execution.json` | real | 需 API key |
| Real-agent profile | 12/12 | `reports/run_real_agent.json` | real | 需 API key |
| Multiround profile | 2/2 | `reports/run_fake_multiround.json` | fake | web_search→web_fetch 两轮链路 |
| Planner profile | 4/4 | `reports/run_fake_planner.json` | fake | 含规划+审批恢复+失败重规划 |
| 平均延迟 | ~2.60s (execution) | `reports/run_real_execution.json` | real | 含模型调用耗时 |
| 平均工具轮次 | 1.2 | `reports/run_real_execution.json` | real | — |

---

## 八、不要编造的内容

- ❌ 不要声称任何未实际运行的 benchmark 数字
- ❌ 不要声称 real mode 下的数字除非确实用真实 API key 跑过
- ❌ 不要声称 Feishu/Weather/AMap 已"真实接入"或"生产联通"
- ❌ 不要把 token 缩减写成成本节省
- ❌ 不要把 "116 个 case" 写成 "115+" 或 "100+"（数字不一致会被追问）

**如果某个数字无法从 report JSON 文件中提取，就不要写。**
