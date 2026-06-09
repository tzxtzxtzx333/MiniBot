"""Verify demo hook config is loadable and produces expected HookResults."""

from __future__ import annotations

import json
from pathlib import Path

from minibot.hooks.hook_manager import HookManager

ROOT = Path(__file__).resolve().parents[1]


def test_demo_hooks_config_exists() -> None:
    path = ROOT / "configs" / "hooks.demo.json"
    assert path.exists(), f"Missing demo hook config: {path}"


def test_demo_hooks_config_is_valid_json() -> None:
    path = ROOT / "configs" / "hooks.demo.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    hooks = data.get("hooks", [])
    assert isinstance(hooks, list)
    assert len(hooks) >= 4, f"Expected at least 4 demo hooks, got {len(hooks)}"


def test_hook_manager_loads_demo_config() -> None:
    manager = HookManager(ROOT / "configs" / "hooks.demo.json")
    assert len(manager.hooks) >= 4


def test_session_start_log_action_matches() -> None:
    """SessionStart hook with exact matcher triggers log action."""
    manager = HookManager(ROOT / "configs" / "hooks.demo.json")
    results = manager.trigger("SessionStart", "SessionStart", context={"run_id": "demo-1"})
    assert len(results) >= 1
    assert any(r["action"] == "log" for r in results)


def test_pretooluse_block_regex_blocks_rm_rf() -> None:
    """Regex matcher should block 'rm -rf' patterns."""
    manager = HookManager(ROOT / "configs" / "hooks.demo.json")
    results = manager.trigger("PreToolUse", "shell_exec rm -rf /", context={"run_id": "demo-2"})
    assert any(r["action"] == "block" and r.get("blocked") for r in results)


def test_pretooluse_require_approval_exact_matches_file_write() -> None:
    """Exact matcher should trigger require_approval for file_write."""
    manager = HookManager(ROOT / "configs" / "hooks.demo.json")
    results = manager.trigger("PreToolUse", "file_write", context={"run_id": "demo-3"})
    assert any(r["action"] == "require_approval" for r in results)


def test_posttooluse_redact_regex_matches_sensitive() -> None:
    """Regex matcher should trigger redact for sensitive output."""
    manager = HookManager(ROOT / "configs" / "hooks.demo.json")
    output = json.dumps({"api_key": "sk-secret-123", "result": "ok"})
    results = manager.trigger("PostToolUse", output, context={"run_id": "demo-4"})
    assert any(r["action"] == "redact" for r in results)


def test_default_hooks_config_is_empty_and_harmless() -> None:
    """Default hooks.json is empty — no hooks injected."""
    manager = HookManager(ROOT / "configs" / "hooks.json")
    assert len(manager.hooks) == 0
    results = manager.trigger("PreToolUse", "rm -rf /")
    assert results == []
