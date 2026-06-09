# Provider Smoke Evidence

证明 MiniBot 对各外部 provider 的配置接入边界。**不放生产级联通声明，不伪造运行结果。**

## 状态类型

| 状态 | 含义 |
|---|---|
| `pass` | 配置完整，上次真实运行通过 |
| `config_missing` | 缺 API key / endpoint / SDK |
| `failed` | 配置存在但真实调用失败（网络/配额/上游错误） |
| `not_tested` | 未执行过真实 smoke |

## Provider 矩阵

| Provider | 当前 fake smoke 状态 | Real smoke 状态 | 说明 |
|---|---|---|---|
| **DeepSeek / OpenAI-compatible** | — | `pass`（需配置 API key） | `model_provider=deepseek`，`MINIBOT_MODEL_API_KEY` |
| **Tavily web_search** | mock provider 通过 | `config_missing`（缺 `TAVILY_API_KEY`） | `MINIBOT_WEB_SEARCH_PROVIDER=tavily` |
| **QWeather weather** | mock provider 通过 | `config_missing`（缺 key） | `MINIBOT_WEATHER_PROVIDER=real` |
| **AMap MCP map_route** | mock provider 通过 | `config_missing`（缺 MCP endpoint） | `MINIBOT_MAP_PROVIDER=mcp` |
| **AMap MCP map_poi_search** | mock provider 通过 | `config_missing`（缺 MCP endpoint） | `MINIBOT_MAP_PROVIDER=mcp` |
| **Feishu WebSocket** | `config_missing`（缺 `FEISHU_APP_ID`） | `config_missing` | 需 lark-oapi SDK |
| **Feishu mock** | 通过 | — | `feishu-mock` 仅本地回归 |
| **web_fetch** | — | 真实 HTTP provider 通过 | 依赖 urllib |
| **ModelVerifier** | fake mode 通过 | `verifier_config_missing`（缺 key） | 可选真实校验 |

## 真实运行命令（需要 API key）

```powershell
# DeepSeek real mode
python -m minibot chat --message "hello"  # 需要 .env 中 MINIBOT_MODEL_API_KEY

# Tavily web_search
python -m minibot chat --message "搜索 MiniBot Agent Harness"

# Feishu WebSocket
python -m minibot feishu  # 需要 FEISHU_APP_ID + FEISHU_APP_SECRET
```

## 证据模板

参见 `docs/evidence/provider_smoke_template.json`。

## 口径说明

- `config_missing` 不是项目失败——表明 provider 是可配置接入的边界；
- fake/mock 回归与 real provider smoke 严格区分；
- 不放生产级联通声明（如"已真实接入飞书生产 Bot"）。
