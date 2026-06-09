"""Minimal provider smoke — check config presence, not real connectivity."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def run_provider_smoke(report_path: Path | None = None) -> dict[str, object]:
    """Check provider config status and return a structured report."""
    results: list[dict[str, object]] = []

    # DeepSeek / OpenAI-compatible
    deepseek_ok = bool(os.getenv("MINIBOT_MODEL_API_KEY", "").strip())
    results.append({
        "provider": "DeepSeek",
        "type": "model",
        "status": "pass" if deepseek_ok else "config_missing",
        "env_required": ["MINIBOT_MODEL_API_KEY", "MINIBOT_MODEL_BASE_URL"],
        "details": "API key configured" if deepseek_ok else "MINIBOT_MODEL_API_KEY not set",
    })

    # Tavily web_search
    tavily_ok = bool(os.getenv("TAVILY_API_KEY", "").strip())
    results.append({
        "provider": "Tavily",
        "type": "web_search",
        "status": "pass" if tavily_ok else "config_missing",
        "env_required": ["TAVILY_API_KEY"],
        "details": "API key configured" if tavily_ok else "TAVILY_API_KEY not set",
    })

    # QWeather
    weather_ok = bool(os.getenv("MINIBOT_WEATHER_API_KEY", "").strip())
    results.append({
        "provider": "QWeather",
        "type": "weather",
        "status": "pass" if weather_ok else "config_missing",
        "env_required": ["MINIBOT_WEATHER_API_KEY"],
        "details": "API key configured" if weather_ok else "MINIBOT_WEATHER_API_KEY not set",
    })

    # AMap MCP
    amap_ok = bool(os.getenv("MINIBOT_AMAP_MCP_ENDPOINT", "").strip())
    results.append({
        "provider": "AMap MCP",
        "type": "map_route",
        "status": "pass" if amap_ok else "config_missing",
        "env_required": ["MINIBOT_AMAP_MCP_ENDPOINT", "MINIBOT_AMAP_MCP_API_KEY"],
        "details": "MCP endpoint configured" if amap_ok else "MINIBOT_AMAP_MCP_ENDPOINT not set",
    })

    # Feishu WebSocket
    feishu_app = bool(os.getenv("FEISHU_APP_ID", "").strip())
    feishu_secret = bool(os.getenv("FEISHU_APP_SECRET", "").strip())
    if feishu_app and feishu_secret:
        feishu_status = "pass"
        feishu_detail = "FEISHU_APP_ID and FEISHU_APP_SECRET configured"
    elif feishu_app or feishu_secret:
        feishu_status = "config_missing"
        feishu_detail = "partial config: one of APP_ID/APP_SECRET missing"
    else:
        try:
            import lark_oapi  # noqa: F401
            feishu_status = "config_missing"
            feishu_detail = "lark-oapi SDK available but FEISHU_APP_ID/APP_SECRET not set"
        except ImportError:
            feishu_status = "sdk_missing"
            feishu_detail = "lark-oapi SDK not installed, FEISHU_APP_ID/APP_SECRET not set"
    results.append({
        "provider": "Feishu",
        "type": "channel",
        "status": feishu_status,
        "env_required": ["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
        "details": feishu_detail,
    })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "providers": results,
        "summary": {
            "pass": sum(1 for r in results if r["status"] == "pass"),
            "config_missing": sum(1 for r in results if r["status"] == "config_missing"),
            "sdk_missing": sum(1 for r in results if r["status"] == "sdk_missing"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        },
    }

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report
