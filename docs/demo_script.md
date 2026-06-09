# MiniBot Demo Script

## 最终演示顺序（推荐）

### 1. Runtime Status

```bash
python -m minibot status
```

### 2. TaskStore — 简单计算任务

```bash
python -m minibot tasks create --goal "计算 128 * 64"
python -m minibot tasks resume <task_id>
python -m minibot tasks show <task_id>
```

### 3. TaskStore — 灰名单任务 Approval E2E

```bash
# 创建灰名单任务（file_write 需要审批）
python -m minibot tasks create --goal "write notes/approval_demo.txt content DemoOK"

# 第一次 resume → waiting_approval
python -m minibot tasks resume <task_id>
# 输出: status=waiting_approval, pending_approval_id=<id>

# 批准（通过 CLI 或 HTTP）
python -m minibot approvals approve <approval_id>

# 第二次 resume → completed
python -m minibot tasks resume <task_id>
# 输出: status=completed

# 验证文件写入
# cat .minibot/sandbox_workspace/notes/approval_demo.txt
```

### 4. HTTP Approval API（带 Bearer Token）

```bash
# 终端 1 — 启动 HTTP 服务（可选 token）
python -m minibot http --host 127.0.0.1 --port 8000

# 终端 2
curl http://127.0.0.1:8000/status
curl http://127.0.0.1:8000/approvals

# 设置 token 后需要 Authorization 头
# export MINIBOT_HTTP_AUTH_TOKEN=my-secret
curl -H "Authorization: Bearer my-secret" \
  -X POST http://127.0.0.1:8000/approvals/<approval_id>/approve
```

### 5. TaskPlan 任务规划

```bash
# 创建计划
python -m minibot plan create --goal "读取 README.md 和 docs/resume_mapping.md，总结 MiniBot 当前能力边界，并写入 realistic_roadmap.md"

# 执行计划
python -m minibot plan run <plan_id>

# 查看计划状态
python -m minibot plan show <plan_id>

# 审批灰名单工具后恢复执行
python -m minibot approvals approve <approval_id>
python -m minibot plan resume <plan_id>

# Planner benchmark 回归
python -m minibot benchmark --mode fake --profile planner --report reports/run_fake_planner.json
```

### 6. Benchmark Evidences

```bash
python -m minibot benchmark --mode fake --profile safety \
  --report reports/run_fake_safety_check.json

python -m minibot benchmark --mode fake --profile multiround \
  --report reports/run_fake_multiround.json

python -m minibot benchmark --mode real --scope core --profile real-agent \
  --report reports/run_real_agent.json
```

### 7. 其他 Chat 演示

```bash
# 验证 HISTORY 相关性检索
python -m minibot chat --message "python deployment guide"
python -m minibot chat --message "deploy python docker"
# context_summary 会包含 history_retrieval_mode=relevance

# 验证 /new 手动压缩归档（compression_trigger=manual_new）
python -m minibot chat --message "/new"

python -m minibot chat --message "计算 128 * 64"
python -m minibot chat --message "run python code print(1+1)"
python -m minibot chat --message "shell_exec rm -rf /"
python -m minibot chat --message "搜索 MiniBot Agent Harness Tool Calling"
```

## 完整命令参考

```powershell
python -m minibot status

python -m minibot chat --message "计算 128 * 64"
python -m minibot chat --message "搜索 MiniBot Agent Harness Tool Calling"
python -m minibot chat --message "查询天气 北京"
python -m minibot chat --message "规划路线 厦门大学 到 厦门站"
python -m minibot chat --message "帮我查一下厦门大学附近有什么医院"
python -m minibot chat --message "run python code print(1+1)"
python -m minibot chat --message "shell_exec rm -rf /"

# TaskStore
python -m minibot tasks create --goal "计算 128 * 64"
python -m minibot tasks resume <task_id>
python -m minibot tasks show <task_id>
python -m minibot tasks list

# HTTP + Approval API（终端 1）
python -m minibot http --host 127.0.0.1 --port 8000
# 终端 2
Invoke-RestMethod http://127.0.0.1:8000/status
Invoke-RestMethod http://127.0.0.1:8000/approvals

python -m minibot benchmark --mode fake --profile safety --report reports/run_fake_safety_check.json
python -m minibot benchmark --mode fake --profile multiround --report reports/run_fake_multiround.json
python -m minibot benchmark --mode real --scope core --profile real-agent --report reports/run_real_agent.json
python -m minibot benchmark --mode real --scope core --profile execution --report reports/run_real_execution.json
python -m minibot benchmark --mode real --scope core --profile safety --report reports/run_real_safety.json
python -m minibot benchmark --mode real --scope core --profile all-integrations --report reports/run_real_all_integrations.json

python -m minibot compare reports/run_real_execution.json reports/run_real_execution.json
```

## 演示口径

- `status` 用于展示运行时状态、benchmark case 数量、archive_count 等。
- `chat` 命令用于展示 Harness、Tool Calling、上下文治理、Docker 沙箱和高风险命令阻断（三层治理：白名单自动 / 灰名单审批 / 黑名单阻断并审计）。
- `execution` profile 是简历核心数字来源。
- `safety` profile 用于证明三层治理和阻断链路（含 shell_blacklist + tool-level blacklist）。
- `all-integrations` profile 用于展示真实 provider 接入证据。
- `real-agent` profile 用于展示端到端模型驱动行为（12/12）。
- `multiround` profile 用于展示预算受控多轮 observe → re-plan loop（2/2），覆盖 web_search→web_fetch 两轮链路与 duplicate_loop_detected 停止机制。多轮能力不再用整个 reasoning category 证明。
- `tasks` 命令用于展示 TaskStore 任务状态管理（create / list / show / cancel / resume）。
- `http` 命令用于展示 HTTP 服务与 Approval API（GET /approvals, POST approve / reject）。
- `status` 已包含 tasks_dir_exists / task_count / pending_task_count / approval_pending_count / budget 健康检查字段。

## Context Governance

```powershell
python -m minibot benchmark --mode fake --profile context-realistic-baseline --report reports/run_context_realistic_baseline.json
python -m minibot benchmark --mode fake --profile context-realistic-optimized --report reports/run_context_realistic_optimized.json
python -m minibot compare reports/run_context_realistic_baseline.json reports/run_context_realistic_optimized.json
```

- Context profile 主要用于验证 `context_metrics`、`key_facts_preserved` 和估算 token 变化。
- Token 口径始终是 `estimated tokens = ceil(len(text) / 4)`。
- Token reduction 百分比不作为简历数字。
## Human Review Demo

Commands:

```bash
python -m minibot chat --message "write notes/approval.txt content demo"
python -m minibot approvals list
python -m minibot approvals approve <approval_id>
python -m minibot chat --message "write notes/approval.txt content demo"
```

Expected:
- First run returns `approval_required`
- `approvals list` shows a pending record
- After approval, replaying the same request executes the tool
- `shell_exec rm -rf /` remains `blocked_by_policy`

## Benchmark Profiles

```bash
python -m minibot benchmark --mode real --scope core --profile approval --report reports/run_real_approval.json
python -m minibot benchmark --mode real --scope core --profile execution --report reports/run_real_execution.json
python -m minibot benchmark --mode real --scope core --profile all-integrations --report reports/run_real_all_integrations.json
```

### Realistic Context Benchmark

```bash
python -m minibot benchmark --mode fake --profile context-realistic-baseline --report reports/run_context_realistic_baseline.json
python -m minibot benchmark --mode fake --profile context-realistic-optimized --report reports/run_context_realistic_optimized.json
python -m minibot compare reports/run_context_realistic_baseline.json reports/run_context_realistic_optimized.json
```
