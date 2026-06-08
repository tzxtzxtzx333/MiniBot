# Feishu Setup

## 结论

MiniBot 当前已经具备 Feishu WebSocket Bot 接入路径边界，但不宣称本仓库默认已经真实联通飞书。

## 支持的命令

```bash
python -m minibot feishu
python -m minibot feishu-mock examples/mock_feishu_event.json
```

## 环境变量

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BOT_NAME=MiniBot
FEISHU_BOT_MODE=websocket
FEISHU_WS_ENABLED=true
```

## 行为规则

- 缺配置时：返回 `feishu_config_missing`
- 配置完整但未安装飞书 SDK 时：返回 `feishu_sdk_not_installed`
- `feishu-mock` 继续可用于本地回归

## 当前结构

`FeishuWebSocketChannel` 提供：

- `load_config()`
- `validate_config()`
- `connect()`
- `parse_event()`
- `to_channel_message()`
- `send_reply()`
- `run()`

说明：

- `parse_event()` 负责解析飞书原始事件
- `to_channel_message()` 负责转成统一 `ChannelMessage`
- 业务处理仍然交给现有 `AgentLoop`

## mock 回归

`feishu-mock` 用于验证：

1. mock event 被解析
2. mock event 被转成 `ChannelMessage`
3. 进入统一 `AgentLoop`
4. 返回 MiniBot 响应
5. run trace 中记录渠道来源

## 简历表述边界

可以写：

- “实现 Feishu WebSocket Bot 接入路径并支持 mock 回归”

不能写：

- “已真实联通飞书生产 Bot”

除非后续在真实配置和真实 SDK 下完成联调并留存证据。
