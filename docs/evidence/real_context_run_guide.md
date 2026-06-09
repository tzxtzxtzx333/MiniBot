# Real Context Experiment Guide

## 前提

需要配置有效的 DeepSeek API key 环境变量：

```env
MINIBOT_MODEL_MODE=real
MINIBOT_MODEL_BASE_URL=https://api.deepseek.com
MINIBOT_MODEL_API_KEY=sk-xxxxxxxx
MINIBOT_MODEL_NAME=deepseek-chat
```

## 运行命令

```powershell
python -m minibot experiments run --name context_robust_realistic --mode real --report reports/exp_context_robust_real.json
```

## 生成后验证

```powershell
python -c "
import json
d = json.load(open('reports/exp_context_robust_real.json', 'utf-8'))
print('answer_pass_rate:', d['summary'].get('answer_pass_rate'))
print('required_keywords_hit_rate:', d['summary'].get('required_keywords_hit_rate'))
print('context_reduction_rate:', d['summary'].get('context_reduction_rate'))
"
```

## 重要的口径约束

- 只有 real report 中的 `answer_pass_rate` / `required_keywords_hit_rate` 才能用于真实模型回答质量；
- `context_reduction_rate` 在 real mode 下也可计算，但仍衡量 context chars，不代表 token 成本；
- 没有 real report 时，不写真实回答质量指标；
- 不要伪造 real report。
