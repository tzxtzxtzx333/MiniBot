# Experiment Protocol

## context_ablation

### Goal

Measure the impact of MiniBot's full context management pipeline against a
minimal baseline.

### Setup

**Baseline**: only history truncation is active.  All other context features
(history retrieval, memory compaction, archive recall, placeholder cleaning)
are disabled.

**Current**: full pipeline — history retrieval (relevance-based), memory
compaction, archive recall, placeholder cleaning, and hard truncation.

### Cases

9 cases across 3 history lengths × 3 categories:

| History length | Category | Count |
|---|---|---|
| 20 turns | experiment_ctx_20 | 3 |
| 50 turns | experiment_ctx_50 | 3 |
| 100 turns | experiment_ctx_100 | 3 |

Each case seeds a pre-written HISTORY.md with the specified number of
user/assistant turn pairs.

### Procedure

1. Load experiment config
2. For each case:
   a. Apply baseline context config
   b. Run case through BenchmarkRunner
   c. Record baseline metrics
   d. Restore context
   e. Apply current context config
   f. Run same case through BenchmarkRunner
   g. Record current metrics
3. Compute paired metrics
4. Generate report

### Metrics

```text
avg_context_chars        — mean dynamic_context_chars per case
avg_prompt_chars         — mean prompt_tokens (estimated) per case
avg_history_chars        — mean history_chars per case
avg_evidence_chars       — mean evidence_chars (current only) per case
context_reduction_rate   — (baseline_avg - current_avg) / baseline_avg
memory_recall_pass_rate  — pass_rate across memory-category cases
long_context_pass_rate   — pass_rate across context-category cases
```

### Constraints

1. All numbers computed from raw report — zero hardcoding
2. Skipped cases excluded from aggregation
3. Fake mode results must be labelled as engineering regression only
4. No real-model capacity claims without real-mode experiment data
