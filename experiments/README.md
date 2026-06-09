# Experiments

可复现的实验框架。每个实验通过切换 ContextBuilder 配置，在相同 benchmark case 上跑 baseline 和 current 两次，计算指标差值。

## 结构

```text
experiments/
  README.md                          ← 本文件
  protocol.md                        ← 实验协议
  configs/                           ← baseline / current 配置
    baseline_context.json
    context_ablation.json
  cases/                             ← 实验用例
    context_ablation.json            ← 9 个 case（20/50/100 轮历史 × 3 类目）
```

## 用法

```powershell
# 列出可用实验
python -m minibot experiments list

# 运行 context_ablation 实验（fake 模式，快速验证）
python -m minibot experiments run --name context_ablation --mode fake --report reports/exp_context_ablation.json

# 生成 Markdown 摘要
python -m minibot experiments summarize --reports reports/exp_context_ablation.json --output docs/evidence/experiment_summary.md
```

## 重要说明

- fake 模式仅用于工程回归，不能写为真实模型能力
- 所有数字从 raw report 计算，不允许硬编码
- 没有真实 report 前，不要写具体提升数字
