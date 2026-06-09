# MiniBot Demo — 最小可复现场景

以下场景**全部无需外部 API key**，clone 后即可运行。

---

## 场景 1：Runtime Status 检查

**命令**：

```bash
python -m minibot status
```

**预期输出示例**：

```json
{
  "version": "0.1.0",
  "config_files": {
    "minibot.json": true,
    "hooks.json": true,
    "tools.json": true,
    "policy.json": true
  },
  "workspace_exists": true,
  "docker_available": false,
  "benchmark_case_count": 116,
  "memory_exists": true,
  "history_exists": true,
  "archives_dir_exists": true,
  "archive_count": 0,
  "feishu_config_present": false,
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

**证明了什么**：

- 项目配置完整，所有 config 文件和 workspace 目录就绪
- 116 个 工程回归用例 可用
- budget 参数从 `configs/minibot.json` 正确加载
- Feishu / Docker 等外部依赖状态明确报告（当前未配置）

**依赖外部 key**：否

---

## 场景 2：简单对话（fake 模式 Echo）

**命令**：

```bash
python -m minibot chat --message "你好，MiniBot"
```

**预期输出**：

```
MiniBot echo: 你好，MiniBot
```

**证明了什么**：

- AgentLoop 正常启动和响应
- fake 模式下的 `FakeModelClient` 在没有工具调用意图时进入 chat 模式
- 对话记录写入 `HISTORY.md`，trace 写入 `.minibot/runs/`

**验证**：

```bash
# 查看对话历史
cat .minibot/HISTORY.md
# 应包含 "user: 你好，MiniBot" 和 "assistant: MiniBot echo: 你好，MiniBot"

# 查看 run trace
ls .minibot/runs/
```

**依赖外部 key**：否

---

## 场景 3：工具调用（calculator）

**命令**：

```bash
python -m minibot chat --message "calculate 128 * 64"
```

**预期输出**：

```
MiniBot tool result: 8192
```

**证明了什么**：

- `FakeModelClient` 通过正则匹配识别计算意图并生成 `tool_plan`
- `ToolDispatcher` 完成 schema 校验 → 白名单放行 → 执行 calculator
- calculator 使用 AST 安全求值，不支持任意代码执行
- 完整的 tool_trace 写入 run record

**验证**：

```bash
# 查看 trace 中的工具调用记录
ls -t .minibot/runs/ | head -1 | xargs -I {} cat .minibot/runs/{}
# 应有 "tool_calls": [{"tool_name": "calculator", "arguments": {"expression": "128 * 64"}}]
```

**依赖外部 key**：否

---

## 场景 4：Benchmark 回归（fake 模式）

**命令**：

```bash
python -m minibot benchmark --mode fake --report reports/run_demo.json
```

**预期输出**（截取关键字段，具体数值因环境而异）：

```json
{
  "run_mode": "fake",
  "benchmark_profile": "default",
  "benchmark_case_count": 116,
  "total_cases": 116,
  "counted_cases": 93,
  "passed_cases": 93,
  "pass_rate": 1.0,
  "model_provider": "fake",
  "fake_model": true,
  "docker_available": false,
  "capability_status": {
    "real_model": "missing",
    "real_tool_calling": "missing",
    "llm_archive": "missing",
    "model_verifier": "fake",
    "docker_sandbox": "unavailable"
  }
}
```

> **注意**：`total_cases` 与 `passed_cases` 的具体数值取决于当前环境的 Docker 可用性等因素。部分 case 会被标记为 `pending`（不计入 pass_rate）。当 Docker 不可用时，依赖 Docker 的 case 将被跳过。核心关注点应是 `pass_rate` 对 counted_cases 为 1.0，且 `capability_status` 正确标注了 fake 模式下的能力缺失。

**证明了什么**：

- fake 模式下所有 counted case 通过 rule verifier 验证（`pass_rate: 1.0`）
- `capability_status` 明确标注 fake 模式下哪些能力为 `missing`（真实模型、LLM 压缩归档等），不做假
- report 自动写入 `reports/run_demo.json`，可用于后续 compare

**依赖外部 key**：否

**更多 profile**：

```bash
# 安全治理 profile（白名单/黑名单/审批阻断）
python -m minibot benchmark --mode fake --profile safety --report reports/run_safety.json

# 多轮工具调用 profile
python -m minibot benchmark --mode fake --profile multiround --report reports/run_multiround.json
```

---

## 场景 5：HTTP Channel + Approval API（可选）

**终端 1 — 启动服务**：

```bash
python -m minibot http --host 127.0.0.1 --port 8000
```

**终端 2 — 调用接口**：

```bash
# 健康检查
curl http://127.0.0.1:8000/status

# 发送消息
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"content": "calculate 128 * 64"}'

# 查看审批队列（应为空）
curl http://127.0.0.1:8000/approvals
```

**预期输出**：

```
# GET /status → 与场景 1 相同的 JSON status
# POST /chat → {"response": "MiniBot tool result: 8192", ...}
# GET /approvals → []
```

**证明了什么**：

- HTTP channel 复用同一个 AgentLoop，与 CLI 共享所有能力
- Approval API 端点可用，与 CLI `approvals` 命令共享同一 JSONL store
- 无 token 配置时默认无认证（本地开发模式）

**依赖外部 key**：否

---

## 环境要求

- Python >= 3.11
- 无需 GPU
- 无需 Docker（sandbox 工具在 Docker 不可用时返回 `docker_unavailable`）
- 无需任何外部 API key（fake mode）

## 从 Demo 到 Real Mode

如需验证真实模型接入，在项目根目录创建 `.env`：

```env
MINIBOT_MODEL_MODE=real
MINIBOT_MODEL_PROVIDER=deepseek
MINIBOT_MODEL_BASE_URL=https://api.deepseek.com
MINIBOT_MODEL_API_KEY=你的key
MINIBOT_MODEL_NAME=deepseek-chat
```

然后运行：

```bash
python -m minibot benchmark --mode real --scope core --profile real-agent --report reports/run_real.json
```

缺 key 时不会 fallback fake，而是返回 `deepseek_config_missing` 并生成真实 report。
