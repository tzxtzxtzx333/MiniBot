# MiniBot V1 Resume-Complete 需求文档

## 1. 当前目标更新

MiniBot 当前目标不再是“本地 mock 架构闭环”，而是完成一个能够完整支撑简历项表述的 **V1 Resume-Complete 版**。

当前项目已经完成：

1. AgentLoop Harness 主流程；
2. CLI / HTTP / Feishu Mock 多渠道入口；
3. ToolRegistry / ToolDispatcher；
4. HookManager / HookActionRegistry；
5. MEMORY.md / HISTORY.md / archives；
6. ContextBuilder / PlaceholderCleaner；
7. RetryManager / PartialSuccessHandler；
8. JSON Benchmark Runner；
9. ReportWriter / ReportComparator；
10. 70+ benchmark case；
11. README、architecture、resume_mapping、demo_script、decisions 等文档。

但这些还不足以称为“完整复刻简历项”。接下来需要将关键能力从 mock / 接口预留升级为真实可运行能力。

## 2. 简历项原始目标

MiniBot 简历项核心表述包括：

1. 基于 nanobot 架构思想设计并实现 MiniBot；
2. 以 Harness 为核心；
3. 通过 5 层插件化架构统一管理多渠道接入、工具调用与结构化记忆；
4. Hook 机制：PreToolUse、PostToolUse、SessionStart 等节点；
5. exact / regex 匹配；
6. 审批、日志等模块灵活注入；
7. MEMORY.md 存储长期记忆；
8. HISTORY.md 存储近期对话；
9. 对话达到阈值或用户输入 `/new` 时，自动触发 LLM 对旧对话进行压缩归档；
10. 上下文治理：工具调用治理、历史消息截断、记忆压缩、占位清理、硬截断、子代理摘要固化；
11. 工具安全与运行治理：白名单、灰名单、黑名单、上限参数校验、沙箱隔离、敏感信息脱敏、重复调用去重；
12. Partial Success 识别与自动重试降级；
13. 70+ Benchmark；
14. 6 项指标自动汇总；
15. 版本回归对比。

本阶段目标是让这些能力不仅有架构和接口，而且能通过真实运行证明。

## 3. P0 必须真实完成

P0 是 Resume-Complete 版必须完成的核心能力。P0 不允许只停留在 mock 或占位接口。

### 3.1 真实模型接入：首选 DeepSeek

必须实现：

1. 完善 OpenAI-compatible ModelClient；
2. 默认支持 DeepSeek；
3. 从 `.env` 读取模型配置；
4. fake model 仅用于测试；
5. 真实运行模式下必须调用真实模型；
6. trace 中记录 `model_provider`、`model_name`、`fake_model`。

推荐配置：

```env
MINIBOT_MODEL_PROVIDER=deepseek
MINIBOT_BASE_URL=https://api.deepseek.com
MINIBOT_API_KEY=
MINIBOT_MODEL_NAME=deepseek-chat
MINIBOT_USE_FAKE_MODEL=false
```

验收命令：

```bash
python -m minibot chat --message "请总结 MiniBot 的核心架构"
```

如果未配置 key，必须明确返回：

```text
deepseek_config_missing
```

不能假装调用成功。

### 3.2 真实模型参与 Tool Calling 决策

Tool Calling 不能只依赖 fake ModelClient 的关键词触发。

必须实现：

1. 真实模型参与工具调用决策；
2. 如果模型支持原生 tool_calls，则解析原生 tool_calls；
3. 如果模型不稳定或不支持原生 tool_calls，则要求模型输出统一 JSON tool_plan；
4. ToolAgent 解析 tool_plan；
5. ToolDispatcher 执行工具；
6. tool_call / tool_result 写入 trace；
7. fake keyword trigger 只能作为测试路径。

统一 tool_plan 格式示例：

```json
{
  "type": "tool_plan",
  "tool_calls": [
    {
      "tool_name": "calculator",
      "arguments": {
        "expression": "128 * 64"
      }
    }
  ]
}
```

至少真实可用工具：

```text
calculator
file_read
file_write
memory_search
memory_write
doc_summarize
python_exec
shell_exec
web_fetch
```

其中：

1. calculator / file / memory / doc_summarize 必须真实可用；
2. python_exec / shell_exec 必须走 Docker；
3. web_fetch 至少真实抓取 URL 内容；
4. web_search / weather / map_route 可以保留 provider 接口，但不能作为简历核心证明。

验收命令：

```bash
python -m minibot chat --message "计算 128 * 64，并把结果写入 notes/result.txt"
python -m minibot chat --message "读取 notes/result.txt 并总结"
```

### 3.3 真实 LLM 历史压缩归档

简历明确写“自动触发 LLM 对旧对话进行压缩归档”，因此 `/new` 不能只用 fake summarizer。

必须实现：

1. `/new` 触发 SummarizerAgent；
2. SummarizerAgent 调用真实 DeepSeek；
3. 生成压缩摘要；
4. 固化到 `.minibot/archives/`；
5. 文件头记录模型信息；
6. trace 记录 `compression_trigger`、`model_provider`、`token_before`、`token_after`。

archives 文件头至少包含：

```text
summary_by: SummarizerAgent
model_provider:
model_name:
source_session_id:
created_at:
token_before:
token_after:
compression_trigger:
```

验收命令：

```bash
python -m minibot chat --message "我们讨论 MiniBot 的记忆系统。请记住我喜欢中文回答。"
python -m minibot chat --message "/new"
```

### 3.4 真实 Docker 沙箱

简历明确写“沙箱隔离”，因此高风险执行工具必须真实进 Docker。

必须实现：

1. python_exec 真实进入 Docker 执行；
2. shell_exec 真实进入 Docker 执行；
3. 不允许直接用宿主机执行 python/shell；
4. 限制工作目录为 `.minibot/sandbox_workspace`；
5. 限制挂载路径；
6. 限制超时；
7. 限制输出长度；
8. 不注入宿主机敏感环境变量；
9. Docker 不可用时返回 `docker_unavailable`；
10. trace 中记录 `sandbox=docker`、`docker_available`、`timeout`、`output_truncated`。

验收命令：

```bash
python -m minibot chat --message "运行 python 代码 print(1+1)"
python -m minibot chat --message "执行 shell 命令 echo hello"
python -m minibot chat --message "执行shell命令 rm -rf /"
```

预期：

1. 正常 python/shell 在 Docker 中执行；
2. 危险命令被 Hook 或 Policy 阻断；
3. trace 中记录 Docker 沙箱信息。

### 3.5 Core-Real Benchmark 报告

Benchmark 必须区分 fake 和 real。

必须支持：

```bash
python -m minibot benchmark --mode fake --report reports/run_fake_v1.json
python -m minibot benchmark --mode real --scope core --report reports/run_real_v1.json
```

real mode 报告必须包含：

```json
{
  "run_mode": "real",
  "benchmark_scope": "core",
  "model_provider": "deepseek",
  "fake_model": false,
  "docker_available": true,
  "mock_tools_used": [],
  "real_tools_used": []
}
```

简历指标只能从 real mode 报告中提取。

## 4. P1 必须具备真实接入能力，但允许因缺少配置标记 missing

P1 能力不允许只有空接口，但可以因为外部账号、API key 或平台配置缺失而返回明确 missing 状态。

### 4.1 Feishu WebSocket Bot

必须实现：

1. Feishu WebSocket Bot 接入边界代码路径（真实联通需飞书平台配置）；
2. 启动命令：

```bash
python -m minibot feishu
```

3. 从 `.env` 读取：

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BOT_NAME=
FEISHU_BOT_MODE=websocket
FEISHU_WS_ENABLED=true
```

4. 保留可配置的真实连接入口；
5. 支持消息事件转换为 ChannelMessage；
6. 支持进入统一 AgentLoop；
7. 支持回复飞书消息；
8. 无配置时返回 `feishu_config_missing`；
9. mock 仅作为本地回归模式。

如果未提供真实配置，必须明确说明未联通真实飞书，不能假装成功。

### 4.2 外部 provider

以下工具可以保留 mock，但必须具备真实 provider 替换能力：

```text
web_search
weather
map_route
model_verifier
human_review
```

文档中必须区分：

```text
mock provider
real provider
missing config
```

## 5. 完成标准

MiniBot V1 Resume-Complete 只有满足以下条件，才算完整复刻简历项：

1. DeepSeek 真实模型可用；
2. fake model 仅用于测试；
3. 真实模型参与 Tool Calling 决策；
4. `/new` 使用真实 LLM 生成压缩归档；
5. python_exec / shell_exec 真实走 Docker；
6. 危险命令被 Hook / Policy 阻断；
7. Feishu WebSocket Bot 接入边界代码完成，配置缺失时返回 `feishu_config_missing`；
8. CLI、HTTP、Feishu 三类入口共用 AgentLoop；
9. 70+ Benchmark 能在 real mode 下运行；
10. report 明确记录 model_provider、fake_model、docker_available、mock_tools_used；
11. resume_mapping.md 中每个简历点都有代码、命令、测试、报告来源；
12. 简历数字只从 `reports/run_real_v*.json` 中提取。

## 6. 当前项目状态重新判定

当前项目状态：

```text
已完成：V1 架构闭环版
未完成：V1 Resume-Complete 版
```

已完成部分：

```text
Harness 主循环
Channel 抽象
HTTP / CLI / Feishu Mock
ToolRegistry / ToolDispatcher
HookActionRegistry
MEMORY.md / HISTORY.md / archives
Benchmark Runner
Report Comparator
文档映射
```

仍需补强部分：

```text
DeepSeek 真实模型接入
真实模型驱动 Tool Calling
真实 LLM 历史压缩
真实 Docker 执行 python_exec / shell_exec
Feishu WebSocket Bot 接入边界完善
真实 web_fetch
Benchmark 区分 fake / real 运行模式
真实 report 作为简历指标来源
```
