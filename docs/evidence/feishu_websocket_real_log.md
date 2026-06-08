# Feishu WebSocket Real Log

Source trace: `docs/evidence/feishu_websocket_trace.json`

## Summary

- Channel: `feishu_ws`
- Session: `oc_7459aca1d9d7a87d60dd7b4f5fb9e960`
- User input: `计算 128 * 64`
- Final response: `MiniBot tool result: 8192`

## Evidence

- `tool_calls[0].tool_name = calculator`
- `tool_calls[0].arguments.expression = 128 * 64`
- `tool_results[0].status = success`
- `tool_results[0].output.result = 8192`
- `lifecycle_events` contains `SessionStart`, `PreToolUse`, `PostToolUse`, `SessionEnd`

## Interpretation

- This trace proves the Feishu WebSocket channel path can enter the unified `AgentLoop`.
- Real Feishu connectivity still depends on environment-variable configuration.
- `feishu-mock` remains the local regression path and should not be described as real connectivity evidence.
