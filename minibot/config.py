"""Configuration loading helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .json_utils import load_json_file


@dataclass(slots=True)
class HttpConfig:
    """HTTP service settings."""

    host: str
    port: int


@dataclass(slots=True)
class AgentBudgetProfile:
    """Budget caps for multi-round agent tool execution."""

    max_tool_rounds: int = 3
    max_tool_calls_total: int = 10
    max_runtime_seconds: int = 60
    max_same_tool_calls: int = 2


@dataclass(slots=True)
class HistoryRetrievalConfig:
    """HISTORY.md relevance retrieval settings."""

    enabled: bool = True
    mode: str = "relevance"
    top_k: int = 5
    max_chars: int = 2000


@dataclass(slots=True)
class MemoryCompactConfig:
    """Auto-compaction settings for history turn threshold."""

    auto_compact_enabled: bool = True
    history_turn_compact_threshold: int = 20
    history_compact_keep_recent: int = 6


@dataclass(slots=True)
class EvidenceConfig:
    """Evidence offloading settings for large tool outputs."""

    enabled: bool = True
    tool_output_min_chars: int = 1500
    summary_max_chars: int = 800
    key_points_max: int = 5


@dataclass(slots=True)
class MiniBotConfig:
    """Top-level application configuration."""

    app_name: str
    version: str
    workspace_dir: str
    model_mode: str
    chat_turn_limit: int
    context_token_budget: int
    archive_token_budget: int
    budget: AgentBudgetProfile
    agent_profiles: dict[str, AgentBudgetProfile] = field(default_factory=dict)
    http: HttpConfig | None = None
    history_retrieval: HistoryRetrievalConfig = field(default_factory=HistoryRetrievalConfig)
    memory: MemoryCompactConfig = field(default_factory=MemoryCompactConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)


def _resolve_agent_profile(data: dict[str, object]) -> AgentBudgetProfile:
    """Resolve the active agent budget profile.

    Priority:
    1. ``MINIBOT_AGENT_PROFILE`` env var selects a named profile
    2. Otherwise ``"default"`` profile from ``agent_profiles``
    3. Individual env vars (``MINIBOT_MAX_TOOL_ROUNDS`` etc.) override
    4. Top-level JSON keys are fallback defaults for each field
    """
    profile_name = os.environ.get("MINIBOT_AGENT_PROFILE", "default").strip()
    profiles_raw = data.get("agent_profiles", {})
    profiles: dict[str, dict[str, object]] = {}
    if isinstance(profiles_raw, dict):
        profiles = {str(k): dict(v) for k, v in profiles_raw.items() if isinstance(v, dict)}

    selected = profiles.get(profile_name, profiles.get("default", {}))

    def _int_env_or(name: str, fallback: int) -> int:
        env_val = os.environ.get(name, "").strip()
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
        return int(selected.get(name.lower(), data.get(name.lower(), fallback)))

    return AgentBudgetProfile(
        max_tool_rounds=max(1, min(100, _int_env_or("MINIBOT_MAX_TOOL_ROUNDS", 3))),
        max_tool_calls_total=max(1, min(1000, _int_env_or("MINIBOT_MAX_TOOL_CALLS_TOTAL", 10))),
        max_runtime_seconds=max(1, min(3600, _int_env_or("MINIBOT_MAX_RUNTIME_SECONDS", 60))),
        max_same_tool_calls=max(1, min(100, _int_env_or("MINIBOT_MAX_SAME_TOOL_CALLS", 2))),
    )


def load_config(path: Path) -> MiniBotConfig:
    """Load MiniBot configuration from JSON."""

    data = load_json_file(path)
    http = HttpConfig(**data["http"])
    env_mode = os.environ.get("MINIBOT_MODEL_MODE", "").strip()
    configured_mode = env_mode or str(data.get("model_mode", "fake"))
    budget = _resolve_agent_profile(data)
    # Build all agent_profiles as proper dataclass instances
    agent_profiles: dict[str, AgentBudgetProfile] = {}
    profiles_raw = data.get("agent_profiles", {})
    if isinstance(profiles_raw, dict):
        for key, value in profiles_raw.items():
            if isinstance(value, dict):
                agent_profiles[str(key)] = AgentBudgetProfile(
                    max_tool_rounds=int(value.get("max_tool_rounds", budget.max_tool_rounds)),
                    max_tool_calls_total=int(value.get("max_tool_calls_total", budget.max_tool_calls_total)),
                    max_runtime_seconds=int(value.get("max_runtime_seconds", budget.max_runtime_seconds)),
                    max_same_tool_calls=int(value.get("max_same_tool_calls", budget.max_same_tool_calls)),
                )
    # History retrieval config
    retrieval_raw = data.get("history_retrieval", {})
    if isinstance(retrieval_raw, dict):
        history_retrieval = HistoryRetrievalConfig(
            enabled=bool(retrieval_raw.get("enabled", True)),
            mode=str(retrieval_raw.get("mode", "relevance")),
            top_k=int(retrieval_raw.get("top_k", 5)),
            max_chars=int(retrieval_raw.get("max_chars", 2000)),
        )
    else:
        history_retrieval = HistoryRetrievalConfig()

    # Memory auto-compaction config
    memory_raw = data.get("memory", {})
    if isinstance(memory_raw, dict):
        memory_config = MemoryCompactConfig(
            auto_compact_enabled=bool(memory_raw.get("auto_compact_enabled", True)),
            history_turn_compact_threshold=int(memory_raw.get("history_turn_compact_threshold", 20)),
            history_compact_keep_recent=int(memory_raw.get("history_compact_keep_recent", 6)),
        )
    else:
        memory_config = MemoryCompactConfig()

    # Evidence config
    evidence_raw = data.get("evidence", {})
    if isinstance(evidence_raw, dict):
        evidence_config = EvidenceConfig(
            enabled=bool(evidence_raw.get("enabled", True)),
            tool_output_min_chars=int(evidence_raw.get("tool_output_min_chars", 1500)),
            summary_max_chars=int(evidence_raw.get("summary_max_chars", 800)),
            key_points_max=int(evidence_raw.get("key_points_max", 5)),
        )
    else:
        evidence_config = EvidenceConfig()

    return MiniBotConfig(
        app_name=data["app_name"],
        version=data["version"],
        workspace_dir=data["workspace_dir"],
        model_mode=_normalize_model_mode(configured_mode),
        chat_turn_limit=data["chat_turn_limit"],
        context_token_budget=int(data.get("context_token_budget", 1200)),
        archive_token_budget=int(data.get("archive_token_budget", 900)),
        budget=budget,
        agent_profiles=agent_profiles,
        http=http,
        history_retrieval=history_retrieval,
        memory=memory_config,
        evidence=evidence_config,
    )


def _normalize_model_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"", "fake"}:
        return "fake"
    if normalized in {"real", "openai-compatible"}:
        return "real"
    raise ValueError(f"unsupported model mode: {value}")
