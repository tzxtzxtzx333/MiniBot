# MiniBot Decisions

## 1. 为什么新建而不是 fork

- MiniBot 目标是“参考架构思想并独立实现”
- 新建项目可以围绕本地 CLI、Harness、审计和简历映射直接设计
- 避免被上游代码结构、依赖历史和兼容义务绑定

## 2. 为什么 fake / real 模式并存

- fake 适合本地回归、开发与无外部依赖测试
- real 适合端到端验证（需配置外部 API key）、工具决策和简历证明
- 两条路径必须显式区分，real 不能失败后伪装成 fake

## 3. 为什么真实模型使用 OpenAI-compatible / DeepSeek

- DeepSeek 提供稳定的 OpenAI-compatible Chat Completions 入口
- 项目无需绑定单一厂商私有 SDK
- 后续可以替换兼容后端而不重写 Harness

## 4. 为什么 Tool Calling 使用 `tool_plan` JSON

- 统一跨模型协议
- 不依赖厂商私有 function calling
- 更利于 trace、benchmark 和稳定解析

## 5. 为什么 Docker 只覆盖高风险工具

- `python_exec` / `shell_exec` 有明确执行风险
- `calculator`、`memory_search` 等低风险工具不值得承担容器启动开销
- 这样可以在安全与性能之间取平衡

## 6. 为什么 Feishu 真实边界和 mock 并存

- 真实飞书接入依赖外部平台配置和 SDK
- 本地开发与测试不能被外部平台阻塞
- 因此保留真实路径，同时使用 `feishu-mock` 做回归
- 接入边界代码已完成；未配置环境变量时，默认 status 显示 `feishu_config_present=false`，不会假装成功

## 7. 为什么 `weather` 是 API provider、`map_route` 是 AMap MCP adapter

- `weather` 更适合常规 HTTP API provider 模式，当前对应 QWeather
- `map_route` 预留给更复杂的外部地图能力，适合作为 MCP adapter 边界
- 两者不必强行统一成同一种接入方式

## 8. 为什么不把所有工具都 MCP 化

- MCP 适合外部 provider 或工具总线场景
- 本地纯函数、文件工具、记忆工具没必要增加额外抽象层
- 全部 MCP 化只会引入复杂度和调试成本

## 9. 为什么简历指标必须来自 real report

- fake report 只能证明开发回归链路存在
- 简历数字必须来自真实模式的可审计结果
- 因此只认 `reports/run_real_*.json`

## 10. 为什么 `web_fetch` 用真实 HTTP provider

- URL 抓取是最适合先落地的真实外部能力
- 不需要额外 MCP 或复杂 SDK
- 能直接证明外部 provider 分层已经成立

## 12. 为什么 `web_search` 接 Tavily 而不是继续 mock

- `web_search` 和地图/POI 搜索是两类能力，不能混到 `map_poi_search`
- Tavily 提供稳定的 HTTP 搜索接口，可在保持现有 ToolDispatcher 不变的前提下配置为真实搜索后端
- 缺 key 时显式返回 `tavily_config_missing`，不允许 fallback mock 冒充成功

## 13. 为什么 `ModelVerifier` 独立支持 fake / real 模式

- 规则校验和模型校验的职责不同，模型校验不应阻塞主工具执行
- real verifier 用于 benchmark 结果审计，而不是主 AgentLoop 推理
- real verifier 缺配置或上游错误时必须结构化记录，不能 fallback fake 冒充 real

## 11. 为什么 provider 状态必须写入 trace / report

- mock 不应冒充 real
- 缺 key / 缺 MCP 配置时要有明确 missing 状态
- 这样 benchmark、验收和简历映射才能严格对齐
## 14. 为什么工具治理使用三层分级（白名单 / 灰名单 / 黑名单）

- 白名单工具（calculator、file_read、web_fetch 等）低风险，自动执行，零摩擦。
- 灰名单工具（file_write、memory_write、python_exec、shell_exec）需要审批确认。
- 黑名单支持两维阻断：工具级 blacklist（如禁用某个 tool）和 shell 命令黑名单（如 `rm -rf`、`shutdown`）。
- 两层黑名单互相独立：工具级 blacklist 阻断整个工具实例；shell_blacklist 阻断特定危险命令。
- 命中返回 `blocked_by_policy` 并写入 tool_trace / run record，确保可审计。

## 15. 为什么多轮能力用独立 multiround profile 而不是 reasoning category

- reasoning category 是混合类目，包含单轮推理、多轮工具链、边界条件等不同性质的 case，不适合作为多轮能力的统一通过率口径。
- multiround profile 只包含明确的多轮 Agent loop case：`multi_round_search_fetch_001`（web_search → web_fetch 两轮链路）和 `multi_round_budget_stop_001`（重复调用触发 duplicate_loop_detected 停止机制）。
- 独立 profile 让多轮能力的证明更加干净、可审计，不与 reasoning category 的其他 case 混淆。
- Report 输出 `multiround_case_count`、`multiround_passed_count`、`multiround_pass_rate`，与 reasoning category 的指标完全解耦。

## 16. Human Review Queue

- MiniBot uses a local JSONL-backed approval queue instead of a database or UI-heavy workflow.
- The goal is to prove graylist confirmation and auditability, not to build an enterprise approval system.
- Only graylisted tools or tools marked `require_approval=true` enter the queue.
- Both tool-level blacklisted tools and blacklisted shell commands are blocked before execution and cannot be bypassed by approval.
- Approval updates status only; the user must replay the same request to execute an approved call.
- Context token reduction experiments use benchmark profiles instead of a separate command.
- The token metric is `estimated tokens`, with a fixed estimator: `ceil(len(text) / 4)`.
- `dynamic_context_tokens` excludes fixed `tool_specs` so context-governance gains are not hidden by static schema overhead.
- `context-baseline/context-optimized` is kept as a stress benchmark.
- `context-realistic-baseline/context-realistic-optimized` is used to validate context governance metrics and key-fact preservation.
- Token reduction percentages are intentionally not used as resume-facing numbers.

## 18. 为什么 HTTP Auth 用可选 Bearer Token 而不是用户登录 / JWT

- 当前目标是本地开发的"最小安全边界"，不是生产级认证系统。
- 空 token（默认）：完全向后兼容，本地开发零摩擦。
- 非空 token：保护 GET /approvals、POST approve、POST reject 三个敏感端点。
- GET /status 和 POST /chat 不受影响，保持调试友好。
- 返回 401（缺 token）和 403（错 token），语义清晰。
- 如果要上生产，应在反向代理层（nginx + OAuth2 Proxy）做认证，而不是在 MiniBot 内部实现。

## 19. 为什么 HISTORY 相关性检索用 token overlap + Jaccard 而不是向量数据库

- MiniBot 的 HISTORY 规模小（通常 < 50 轮对话），向量数据库的检索精度优势不显著。
- token overlap + Jaccard 是标准库即可实现的轻量方案，不引入额外依赖。
- 中文按字符 / 连续片段做简易匹配，在短文本场景下效果足够。
- 相关性评分 + top_k 注入 + token budget 截断已提供完整闭环，无需重型基础设施。

## 20. 为什么压缩归档保留最近 N 轮而不是全量清除

- 全量清除会导致最近的对话上下文突然缺失，影响用户体验。
- 保留 `history_compact_keep_recent` 轮确保压缩后仍有最近上下文可用。
- `/new` 手动触发（`manual_new`）与轮次阈值自动触发（`turn_threshold`）通过 `compression_trigger` 区分，便于审计和调试。
- summarizer 失败时保留原 HISTORY，确保数据安全。

## 17. 为什么只补 TaskStore / HTTP Approval API / Deployment Boundary，而不做 WebUI、数据库或生产级监控

- 当前目标是应用雏形和简历/面试证据，不是生产级 SaaS 平台。
- TaskStore 解决任务状态管理（create → running → completed / failed / waiting_approval），让 task 可以跨轮次追踪。
- HTTP Approval API 解决渠道内审批基础能力：在 HTTP 服务内查看和处理灰名单工具审批，与 CLI approval 共享同一 JSONL store。
- Deployment Boundary 解决运行配置和演示：.env.example 完整化、启动脚本、logs 目录边界、status health check。
- WebUI / 数据库 / 生产监控属于后续产品化范围，不在本轮。
- 本轮完成后 MiniBot 定位为：具备任务状态、渠道内审批和部署运行边界的真实 Agent Harness 应用雏形。
