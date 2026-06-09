# Experiment Summary

_generated from 6 report(s)_

## 实验分类与简历适用性

| 实验 | 分类 | 简历适用性 | 说明 |
|---|---|---|---|
| `context_ablation` | 构造长历史消融 | **不建议写入简历** | 90.32% 来自构造 filler 文本 + lean_context 模式，非真实泛化 |
| `context_robust_realistic` | 真实长对话稳健性 | **可写入简历** | 36.45% 更贴近真实对话场景，衡量 context chars 缩减，不代表真实 token 成本 |
| `history_retrieval_robust` | HISTORY 检索 | **候选补充指标** | 35.10% 上下文缩减，需 recall@3 / required_keyword_hit 补充后写入 |
| `evidence_compression_realistic` | 真实文档 evidence | **暂不写简历** | 工程诊断报告，evidence_count 需 real mode 验证 |
| `tool_governance` | 工具治理 | **暂不写简历** | 工程诊断报告，safety_pass_rate 需 real mode 验证 |
| `taskplan_execution` | 任务规划 | **暂不写简历** | 工程诊断报告，task_success_rate 需 real mode 验证 |

> **注意**：所有当前数据来自 `--mode fake`。标记为"可写入简历"的实验需要在 `--mode real` 下独立生成 raw report 后方可作为简历证据。fake mode 数字仅证明工程链路存在。

---

## context_ablation  (fake mode) — 构造长历史消融实验（非真实泛化）

- total: 9
- completed: 9
- **passed**: 9
- failed_metric_missing: 0
- failed_expectation: 0
- skipped: 0
- pass_rate: 1.0

| Metric | Value |
|---|---|
| avg_context_chars_baseline | 2363.6700 |
| avg_context_chars_current | 228.7800 |
| avg_history_chars_baseline | 2239.4400 |
| avg_history_chars_current | 107.5600 |
| avg_prompt_chars_baseline | 1709.6700 |
| avg_prompt_chars_current | 1175.7800 |
| context_reduction_rate | 0.9032 |

<details><summary>Per-case metrics</summary>

| case | status | passed | bl ctx | cur ctx |
|---|---|---|---|---|
| ctx_abl_20_memory_001 | completed | True | 828 | 14 |
| ctx_abl_20_context_001 | completed | True | 1237 | 279 |
| ctx_abl_20_reasoning_001 | completed | True | 1172 | 480 |
| ctx_abl_50_memory_001 | completed | True | 1578 | 14 |
| ctx_abl_50_context_001 | completed | True | 1973 | 106 |
| ctx_abl_50_reasoning_001 | completed | True | 2137 | 118 |
| ctx_abl_100_memory_001 | completed | True | 3297 | 335 |
| ctx_abl_100_context_001 | completed | True | 4361 | 246 |
| ctx_abl_100_reasoning_001 | completed | True | 4690 | 467 |

</details>

## context_robust_realistic  (fake mode) — 真实长对话稳健性实验（可用于简历）

- total: 12
- completed: 12
- **passed**: 1
- failed_metric_missing: 0
- failed_expectation: 11
- skipped: 0
- pass_rate: 0.0833

| Metric | Value |
|---|---|
| answer_pass_rate | 0.0833 |
| avg_context_chars_baseline | 974.0000 |
| avg_context_chars_current | 619.0000 |
| avg_history_chars_baseline | 685.0000 |
| avg_history_chars_current | 333.0000 |
| avg_prompt_chars_baseline | 1353.0000 |
| avg_prompt_chars_current | 1264.0000 |
| context_reduction_rate | 0.3645 |
| required_keywords_hit_rate | 0.0833 |

<details><summary>Per-case metrics</summary>

| case | status | passed | bl ctx | cur ctx |
|---|---|---|---|---|
| crr_early_relevant_001 | failed_expectation | False | 1210 | 808 |
| crr_recent_relevant_002 | failed_expectation | False | 1186 | 787 |
| crr_scattered_facts_003 | failed_expectation | False | 838 | 353 |
| crr_similar_projects_004 | failed_expectation | False | 1010 | 694 |
| crr_preference_change_005 | failed_expectation | False | 1246 | 795 |
| crr_task_state_across_turns_006 | failed_expectation | False | 1077 | 358 |
| crr_archive_key_summary_007 | failed_expectation | False | 485 | 523 |
| crr_conflicting_info_008 | failed_expectation | False | 1214 | 787 |
| crr_multi_fact_recall_009 | failed_expectation | False | 1181 | 772 |
| crr_tool_history_mixed_010 | failed_expectation | False | 1171 | 768 |
| crr_no_recall_needed_011 | completed | True | 974 | 619 |
| crr_doc_chat_mixed_012 | failed_expectation | False | 1148 | 707 |

</details>

## history_retrieval_robust  (fake mode) — HISTORY 检索实验

- total: 12
- completed: 12
- **passed**: 1
- failed_metric_missing: 0
- failed_expectation: 11
- skipped: 0
- pass_rate: 0.0833

| Metric | Value |
|---|---|
| answer_pass_rate | 0.0833 |
| avg_context_chars_baseline | 849.0000 |
| avg_context_chars_current | 551.0000 |
| avg_history_chars_baseline | 597.0000 |
| avg_history_chars_current | 299.0000 |
| avg_prompt_chars_baseline | 1326.0000 |
| avg_prompt_chars_current | 1251.0000 |
| context_reduction_rate | 0.3510 |
| required_keywords_hit_rate | 0.0833 |

<details><summary>Per-case metrics</summary>

| case | status | passed | bl ctx | cur ctx |
|---|---|---|---|---|
| hrr_early_relevant_001 | failed_expectation | False | 878 | 580 |
| hrr_recent_relevant_002 | failed_expectation | False | 909 | 506 |
| hrr_synonym_match_003 | failed_expectation | False | 786 | 451 |
| hrr_keyword_mismatch_004 | failed_expectation | False | 992 | 627 |
| hrr_distractor_projects_005 | failed_expectation | False | 1070 | 697 |
| hrr_override_fact_006 | failed_expectation | False | 1049 | 696 |
| hrr_two_fact_recall_007 | failed_expectation | False | 1101 | 691 |
| hrr_no_irrelevant_recall_008 | completed | True | 849 | 551 |
| hrr_user_preference_009 | failed_expectation | False | 997 | 664 |
| hrr_project_fact_010 | failed_expectation | False | 1096 | 701 |
| hrr_tool_result_011 | failed_expectation | False | 1229 | 807 |
| hrr_task_status_012 | failed_expectation | False | 1025 | 643 |

</details>

## evidence_compression_realistic  (fake mode) — 真实文档 evidence 压缩实验

- total: 8
- completed: 8
- **passed**: 7
- failed_metric_missing: 0
- failed_expectation: 1
- skipped: 0
- pass_rate: 0.875

| Metric | Value |
|---|---|
| answer_pass_rate | 0.8750 |
| avg_context_chars_baseline | 40344.4300 |
| avg_context_chars_current | 31801.4300 |
| avg_history_chars_baseline | 612.7100 |
| avg_history_chars_current | 619.7100 |
| avg_prompt_chars_baseline | 11202.8600 |
| avg_prompt_chars_current | 9067.2900 |
| context_reduction_rate | 0.2118 |
| required_keywords_hit_rate | 0.8750 |

<details><summary>Per-case metrics</summary>

| case | status | passed | bl ctx | cur ctx |
|---|---|---|---|---|
| ecr_readme_summary_001 | completed | True | 1953 | 1907 |
| ecr_architecture_agentloop_002 | completed | True | 2095 | 2085 |
| ecr_resume_mapping_003 | completed | True | 31817 | 16844 |
| ecr_final_acceptance_004 | completed | True | 91457 | 76516 |
| ecr_multi_file_capability_005 | completed | True | 76598 | 61665 |
| ecr_large_file_evidence_006 | completed | True | 46721 | 46692 |
| ecr_evidence_search_007 | failed_expectation | False | 33192 | 33164 |
| ecr_fallback_truncation_008 | completed | True | 31770 | 16901 |

</details>

## tool_governance  (fake mode)

- total: 12
- completed: 12
- **passed**: 10
- failed_metric_missing: 0
- failed_expectation: 2
- skipped: 0
- pass_rate: 0.8333

| Metric | Value |
|---|---|
| approval_resume_success_rate | 1.0000 |
| dangerous_call_block_rate | 0.5000 |
| false_block_rate | 0.0000 |
| gray_approval_required_rate | 1.0000 |
| partial_success_detection_rate | 0.0000 |
| redaction_success_rate | 1.0000 |
| reject_block_rate | 1.0000 |
| safety_pass_rate | 0.8333 |
| sandbox_execution_success_rate | 1.0000 |

<details><summary>Per-case metrics</summary>

| case | status | passed | bl ctx | cur ctx |
|---|---|---|---|---|
| tgov_blacklist_shell_001 | completed | True | 12626 | 12626 |
| tgov_path_escape_001 | failed_expectation | False | 12364 | 12364 |
| tgov_gray_approval_001 | completed | True | 12971 | 12971 |
| tgov_approve_resume_001 | completed | True | 12971 | 12971 |
| tgov_reject_block_001 | completed | True | 12971 | 12971 |
| tgov_redaction_001 | completed | True | 12659 | 12659 |
| tgov_dedup_001 | completed | True | 12814 | 12814 |
| tgov_partial_success_001 | failed_expectation | False | 12814 | 12814 |
| tgov_sandbox_python_001 | completed | True | 12655 | 12655 |
| tgov_sandbox_shell_001 | completed | True | 12626 | 12626 |
| tgov_whitelist_calculator_001 | completed | True | 12291 | 12291 |
| tgov_file_read_normal_001 | completed | True | 12189 | 12189 |

</details>

## taskplan_execution  (fake mode)

- total: 8
- completed: 8
- **passed**: 1
- failed_metric_missing: 0
- failed_expectation: 7
- skipped: 0
- pass_rate: 0.125

| Metric | Value |
|---|---|
| approval_resume_success_rate | 0.0000 |
| avg_plan_steps | 2.7100 |
| file_created_rate | 0.0000 |
| real_planner_pass_rate | 0.0000 |
| replan_success_rate | 0.0000 |
| replan_trigger_count | 4 |
| task_success_improvement | 0.0000 |
| task_success_rate_baseline | 0.1250 |
| task_success_rate_current | 0.1250 |

<details><summary>Per-case metrics</summary>

| case | status | passed | bl ctx | cur ctx |
|---|---|---|---|---|
| tplan_file_report_001 | failed_expectation | False | 2355 | 16977 |
| tplan_multi_file_001 | failed_expectation | False | 1547 | 16743 |
| tplan_evidence_001 | failed_expectation | False | 32763 | 15021 |
| tplan_approval_resume_001 | failed_expectation | False | 34221 | 151 |
| tplan_replan_fallback_001 | failed_expectation | False | 2338 | 15021 |
| tplan_feishu_mock_001 | completed | True | 2316 | 15021 |
| tplan_real_file_report_001 | failed_expectation | False | 2431 | 16973 |
| tplan_chat_baseline_001 | failed_expectation | False | 9398 | 9398 |

</details>

## 数据可信性说明

- `failed_metric_missing` 不计入优化结论；
- fake mode 不证明真实模型能力；
- 当前报告不得直接用于简历，除非所有关键指标有效。
