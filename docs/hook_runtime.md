# Hook Runtime

MiniBot 的 Hook 机制允许在不修改 AgentLoop 核心代码的情况下，在关键生命周期节点注入治理逻辑。

## 支持的事件点

| Event | 触发时机 | 匹配值 |
|---|---|---|
| `SessionStart` | 每个 AgentLoop run 开始时 | `"SessionStart"` |
| `UserMessageReceived` | 收到用户消息后 | 用户消息文本 |
| `MemoryRecall` | 记忆召回后 | 召回的文本 |
| `ContextBuild` | 上下文构建后 | 历史文本 |
| `PreToolUse` | 工具执行前 | 工具名称 |
| `PostToolUse` | 工具执行后 | 工具结果 JSON |
| `AfterResponse` | 生成最终回复后 | 回复文本 |
| `SessionEnd` | run 结束时 | `"SessionEnd"` |

## Matcher 类型

| 类型 | 示例 | 说明 |
|---|---|---|
| `exact` | `"file_write"` | 精确匹配 |
| `regex` | `"rm\\s+-rf\|shutdown"` | 正则匹配 |

## Action 类型

| Action | 效果 |
|---|---|
| `log` | 记录日志 |
| `require_approval` | 标记工具需要审批 |
| `block` | 阻断执行 |
| `redact` | 脱敏输出字段 |
| `tag` | 打标签（不改变执行） |

## Demo 配置

`configs/hooks.demo.json` 包含 4 个 demo hook：

1. **SessionStart log** — 记录每次 session 启动
2. **PreToolUse exact + require_approval** — `file_write` 触发审批
3. **PreToolUse regex + block** — `rm -rf` / `shutdown` / `format C:` 被阻断
4. **PostToolUse regex + redact** — 包含 `api_key`/`token`/`secret`/`password` 的输出被脱敏

## 启用 Demo 配置

```powershell
# 复制 demo 配置为当前 hooks 配置
cp configs/hooks.demo.json configs/hooks.json
```

## 验证

```powershell
# 阻断测试
python -m minibot chat --message "shell_exec rm -rf /"

# 审批测试
python -m minibot chat --message "write notes/test.txt content demo"
```

## 注意事项

- 默认 `configs/hooks.json` 为空（`{"hooks": []}`），表示不注入额外 Hook；
- demo 配置仅用于演示 Hook 机制的可配置性和可扩展性；
- 启用 demo 后，所有 `file_write` 操作都需要审批。
