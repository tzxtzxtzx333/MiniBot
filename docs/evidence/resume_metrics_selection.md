# Resume Metrics Selection

最终推荐写入简历的数字及来源。所有数字必须从 raw report 提取，不允许手写。

## 推荐写入简历

### 1. 100+ Benchmark 任务集

- **值**: 115 cases（`benchmarks/` 下所有 JSON 文件计数）
- **来源**: 任何 `reports/run_*.json` 的 `benchmark_case_count` 字段
- **证据**: `docs/evidence/run_fake_planner.json`

### 2. 安全回归测试 8 个计入场景 100% 通过

- **值**: safety profile 8/8 counted，pass_rate 1.0
- **来源**: `reports/run_fake_safety_check.json` 的 `safety_passed_count` / `safety_case_count`
- **证据**: `docs/evidence/run_fake_safety_check.json`

### 3. 平均上下文注入长度降低 36.45%（context_robust_realistic）

- **值**: context_reduction_rate = 0.3645（baseline 974 → current 619 avg_context_chars）
- **来源**: `reports/exp_context_robust_realistic.json` 的 `summary.context_reduction_rate`
- **实验**: `context_robust_realistic`（真实长对话稳健性实验）
- **口径**: 衡量 context chars（dynamic_context_chars），不代表模型厂商计费 token
- **注意**: 当前为 fake mode 数据；real mode 报告生成后应更新此数字

## 候选补充指标

以下指标有工程证据支撑，可在需要时写入简历，但当前优先级低于上述三项：

| 指标 | 值 | 实验 | 条件 |
|---|---|---|---|
| HISTORY 检索上下文缩减 | 35.10% | `history_retrieval_robust` | 需 recall@3 / keyword_hit 补充 |
| Planner 真实路径通过率 | 4/4 | `planner` profile | 需 real mode 验证 |
| real-agent 通过率 | 12/12 | `real-agent` profile | 已有 real report 支撑 |
| multiround 通过率 | 2/2 | `multiround` profile | 已有 fake report 支撑 |

## 暂不写入简历

以下实验保留为工程诊断报告，当前不推荐写入简历数字：

| 实验 | 原因 |
|---|---|
| `context_ablation` | 构造长历史 filler text + lean_context，90.32% 不具真实泛化意义 |
| `evidence_compression_realistic` | evidence_count 需 real mode 验证 |
| `tool_governance` | safety_pass_rate 需 real mode 验证 |
| `taskplan_execution` | task_success_rate 需 real mode 验证 |

## Resume-safe 表述模板

```text
MiniBot 是一个以 Harness 为核心的多层本地智能助手。

工程指标：
- 构建 115+ Benchmark 任务集，覆盖记忆召回、上下文治理、工具安全、多步推理等维度；
- 安全回归测试 8 个计入场景保持 100% 通过率；
- 上下文管理实验：在贴近真实长对话的稳健性测试中，平均上下文注入长度降低 36.45%
  （context_robust_realistic，从 baseline 974 chars 降至 619 chars），该数字衡量
  dynamic_context_chars，不代表模型厂商计费 token。

能力边界：
- fake mode 仅用于工程回归和开发测试，不作为真实模型能力证明；
- 所有简历指标从 raw report 自动汇总，不手写、不伪造；
- real mode 报告需要有效 DeepSeek API key 配置才能生成。
```

## 约束

1. Token reduction percentage 不作为简历数字；
2. all-integrations 的单次 pass_rate 不作为简历主数字；
3. 任何未从 report 生成的手写数字不得写入简历；
4. context_reduction_rate 的 36.45% 是 fake mode 数据，real mode 后应更新；
5. 数字口径统一为 `estimated tokens = ceil(len(text) / 4)`。
