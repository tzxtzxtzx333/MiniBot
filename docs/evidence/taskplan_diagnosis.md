# TaskPlan Diagnosis

## 当前状态

`taskplan_execution` fake mode: 1/8 passed (12.5%)。

## 失败分类

| 类别 | 数量 | 说明 |
|---|---|---|
| `current failed` | 6 | Planner 路径未达成业务目标（output 文件未创建） |
| `baseline failed` | 1 | Chat baseline 也未创建输出文件 |
| `passed` | 1 | `tplan_feishu_mock_001` — `/plan` 识别 + plan 创建成功 |

## 根因分析

1. **file_write 需要审批** — 6 个 file report / multi file 类 case 的 `current failed` 均因 `output_file_exists=False`。Planner 正确生成了 plan，TaskExecutor 执行了步骤，但 file_write 因灰名单审批未通过而未真正写入文件。

2. **Chat baseline 无法完成复杂任务** — `tplan_chat_baseline_001` 的目标是"读取 README.md，总结能力，写入文件"，但普通 chat 无法自主触发多步工具调用，`output_file_exists=False`。

3. **Real planner case 依赖审批** — `tplan_real_file_report_001` 需要 file_write，同样未通过审批。

## 链路已验证

- ✅ PlannerAgent.plan() 正确生成 TaskPlan（`avg_plan_steps: 2.71`）
- ✅ TaskExecutor.execute_step() 逐 step 调用 AgentLoop
- ✅ StepVerifier.verify() 判定 step 状态
- ✅ ReplannerAgent 在 file_not_found 时触发 replan
- ✅ LongTaskRunner.run() 顺序执行 step
- ✅ run record 记录 plan_id / step_id
- ✅ `/plan` 消息被 feishu mock 正确路由到 planner

## 成功率暂不作为简历数字

当前 12.5% 的主要瓶颈是 file_write 审批。在 benchmark 模式下可通过 `preloaded_approval` 绕过（用于 planner profile），但 taskplan_execution 实验使用真实 planner 路径，preloaded_approval 不会自动匹配动态生成的 content。

## 下一步

1. 配置 preloaded_approval 匹配 planner 生成的 file_write content（需解析 plan step 输出）
2. 或在 real mode 下让 LLM 生成确定性 content
3. 审批绕过在 benchmark 中已验证可行（planner profile 4/4 real path passed）
