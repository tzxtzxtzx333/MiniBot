"""Evidence store tests — CRUD, search, tool output compression, CLI, regression."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.evidence.store import EvidenceStore
from minibot.evidence.summarizer import EvidenceSummarizer
from minibot.json_utils import load_json_file

ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root(**overrides: object) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)

    config_path = temp_root / "configs" / "minibot.json"
    config = load_json_file(config_path)
    for key, value in overrides.items():
        if isinstance(value, dict):
            config[key] = dict(config.get(key, {}))
            config[key].update(value)
        else:
            config[key] = value
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    (temp_root / "configs" / "hooks.json").write_text(
        json.dumps({"hooks": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return temp_root


# ---------------------------------------------------------------------------
# EvidenceStore CRUD & search
# ---------------------------------------------------------------------------


def test_evidence_store_create_and_get() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        record = store.create(
            run_id="run-001",
            task_id="task-001",
            tool_name="web_fetch",
            source="https://example.com",
            raw_chars=5000,
            summary="Fetched example page",
            key_points=["Point A", "Point B"],
        )
        assert record["evidence_id"].startswith("ev_")
        assert record["tool_name"] == "web_fetch"
        assert record["source"] == "https://example.com"
        assert record["raw_chars"] == 5000
        assert len(record["key_points"]) == 2

        retrieved = store.get(record["evidence_id"])
        assert retrieved is not None
        assert retrieved["evidence_id"] == record["evidence_id"]
        assert retrieved["summary"] == "Fetched example page"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_store_list_filters_by_run_id_and_task_id() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        store.create(
            run_id="run-a",
            task_id="t1",
            tool_name="ta",
            source="s",
            raw_chars=100,
            summary="a1",
            key_points=[],
        )
        store.create(
            run_id="run-a",
            task_id="t2",
            tool_name="tb",
            source="s",
            raw_chars=100,
            summary="a2",
            key_points=[],
        )
        store.create(
            run_id="run-b",
            task_id="t1",
            tool_name="tc",
            source="s",
            raw_chars=100,
            summary="b1",
            key_points=[],
        )

        by_run = store.list(run_id="run-a", limit=10)
        assert len(by_run) == 2

        by_task = store.list(task_id="t1", limit=10)
        assert len(by_task) == 2

        by_both = store.list(run_id="run-b", task_id="t1", limit=10)
        assert len(by_both) == 1
        assert by_both[0]["tool_name"] == "tc"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_store_search_keyword_recall() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        store.create(
            run_id="r1",
            task_id=None,
            tool_name="web_fetch",
            source="https://python.org",
            raw_chars=3000,
            summary="Python deployment guide with Docker instructions",
            key_points=["Dockerfile setup", "CI/CD pipeline"],
        )
        store.create(
            run_id="r2",
            task_id=None,
            tool_name="web_fetch",
            source="https://cooking.example.com",
            raw_chars=2000,
            summary="How to cook pasta with tomato sauce",
            key_points=["Boil water", "Add salt"],
        )

        results = store.search("Docker python deploy", top_k=3)
        assert len(results) >= 1
        assert any("docker" in str(r).lower() for r in results)

        results_cooking = store.search("pasta cooking", top_k=3)
        assert len(results_cooking) >= 1
        assert any("pasta" in str(r).lower() for r in results_cooking)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_store_search_empty_returns_empty() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        results = store.search("nonexistent query")
        assert results == []
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# EvidenceSummarizer
# ---------------------------------------------------------------------------


def test_evidence_summarizer_fake_mode_extracts_key_points() -> None:
    summarizer = EvidenceSummarizer(mode="fake", summary_max_chars=500, key_points_max=3)
    output = (
        "- Deploy using Docker Compose\n"
        "- Set up CI/CD with GitHub Actions\n"
        "- Monitor with Prometheus\n" + "extra text " * 200
    )
    result = summarizer.summarize("web_fetch", output)
    assert len(result["summary"]) <= 500
    assert len(result["key_points"]) <= 3
    assert result["raw_chars"] == len(output)
    assert result["archive_mode"] == "fake"


def test_evidence_summarizer_handles_dict_output() -> None:
    summarizer = EvidenceSummarizer()
    output = {"title": "Test Page", "content": "A" * 3000, "url": "https://example.com"}
    result = summarizer.summarize("web_fetch", output)
    assert result["raw_chars"] > 0
    assert len(result["summary"]) > 0


def test_evidence_summarizer_handles_none_output() -> None:
    summarizer = EvidenceSummarizer()
    result = summarizer.summarize("calculator", None)
    assert result["raw_chars"] == 0
    assert result["summary"] == ""


def test_evidence_summarizer_real_mode_falls_back_on_failure() -> None:
    summarizer = EvidenceSummarizer(mode="real", summary_max_chars=500, external_summarizer=None)
    result = summarizer.summarize("web_fetch", "some output " * 100)
    assert result["archive_mode"] == "fake"  # fallback
    assert len(result["summary"]) > 0


# ---------------------------------------------------------------------------
# AgentLoop evidence integration
# ---------------------------------------------------------------------------


def test_large_tool_output_creates_evidence() -> None:
    """When a tool produces output > threshold, evidence is created."""
    temp_root = _prepare_temp_root(
        evidence={
            "enabled": True,
            "tool_output_min_chars": 100,
            "summary_max_chars": 500,
            "key_points_max": 3,
        }
    )
    try:
        app = MiniBotApp(temp_root)
        # Calculator tool produces small output (< 100 chars) so it won't trigger evidence.
        # We need a tool that produces large output. Let's check what the fake model returns for
        # web_fetch or web_search. Actually in fake mode, tools return mock results.
        # Let's instead directly test the _offload_large_outputs method.

        large_output = {"_compressed": False, "output": "x" * 200}
        # Simulate what happens when tool produces large output
        # The fake model will trigger a tool call for "搜索 MiniBot..."
        # web_search returns mock results. Let's check the output size.
        # Actually, the mock web_search returns a structured result. The string form might not be > 1500.

        # Let's test with calculator — it won't trigger evidence since output is small.
        # For a positive test, we can directly call _offload_large_outputs
        tool_results, meta = app.runtime.agent_loop._offload_large_outputs(
            run_id="test-run",
            task_id="test-task",
            tool_results=[
                {
                    "tool_name": "web_fetch",
                    "status": "success",
                    "output": "A" * 2000,
                    "arguments": {"url": "https://example.com"},
                }
            ],
        )
        assert meta["compressed"] is True
        assert len(meta["evidence_ids"]) == 1
        assert tool_results[0]["output"]["_compressed"] is True
        assert "summary" in tool_results[0]["output"]
        assert "key_points" in tool_results[0]["output"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_small_tool_output_does_not_create_evidence() -> None:
    """Small tool outputs (below threshold) are left untouched."""
    temp_root = _prepare_temp_root(evidence={"enabled": True, "tool_output_min_chars": 500})
    try:
        app = MiniBotApp(temp_root)
        tool_results, meta = app.runtime.agent_loop._offload_large_outputs(
            run_id="test-run",
            task_id="test-task",
            tool_results=[
                {
                    "tool_name": "calculator",
                    "status": "success",
                    "output": 42,
                    "arguments": {"expression": "1+1"},
                }
            ],
        )
        assert meta["compressed"] is False
        assert meta["evidence_ids"] == []
        assert tool_results[0]["output"] == 42  # unchanged
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_disabled_skips_offloading() -> None:
    temp_root = _prepare_temp_root(evidence={"enabled": False, "tool_output_min_chars": 100})
    try:
        app = MiniBotApp(temp_root)
        tool_results, meta = app.runtime.agent_loop._offload_large_outputs(
            run_id="test-run",
            task_id="test-task",
            tool_results=[
                {
                    "tool_name": "web_fetch",
                    "status": "success",
                    "output": "A" * 2000,
                    "arguments": {"url": "https://example.com"},
                }
            ],
        )
        assert meta["compressed"] is False
        assert meta["evidence_ids"] == []
        assert tool_results[0]["output"] == "A" * 2000
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_run_record_includes_evidence_fields() -> None:
    temp_root = _prepare_temp_root(evidence={"enabled": True, "tool_output_min_chars": 100})
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="ev-run-session", content="hello"
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert "evidence_ids" in record
        assert "evidence_count" in record
        assert "tool_output_compressed_to_evidence" in record
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_context_metrics_includes_evidence_count() -> None:
    temp_root = _prepare_temp_root(evidence={"enabled": True, "tool_output_min_chars": 100})
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="ev-metrics-session", content="hello"
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        metrics = record.get("context_metrics", {})
        assert "evidence_chars" in metrics
        assert "evidence_count" in metrics
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_summarizer_failure_preserves_original_output() -> None:
    """If the evidence summarizer fails, the original output should not be lost."""
    temp_root = _prepare_temp_root(evidence={"enabled": True, "tool_output_min_chars": 100})
    try:
        app = MiniBotApp(temp_root)

        # Make summarizer fail by setting it to None (simulates failure/degraded mode)
        original_summarizer = app.runtime.agent_loop.evidence_summarizer
        app.runtime.agent_loop.evidence_summarizer = None

        tool_results, meta = app.runtime.agent_loop._offload_large_outputs(
            run_id="test-run",
            task_id="test-task",
            tool_results=[
                {
                    "tool_name": "web_fetch",
                    "status": "success",
                    "output": "IMPORTANT_DATA_" * 100,
                    "arguments": {"url": "https://example.com"},
                }
            ],
        )
        # Without summarizer, _offload_large_outputs short-circuits and returns original
        # (the guard `if not self.evidence_enabled or self.evidence_store is None or self.evidence_summarizer is None` returns early)
        assert tool_results[0]["output"] == "IMPORTANT_DATA_" * 100

        # Restore
        app.runtime.agent_loop.evidence_summarizer = original_summarizer
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_evidence_list_returns_json_array() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        store.create(
            run_id="r1",
            task_id=None,
            tool_name="t",
            source="s",
            raw_chars=100,
            summary="test",
            key_points=[],
        )

        import os
        import subprocess
        import sys

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "minibot", "evidence", "list"],
            capture_output=True,
            text=True,
            cwd=str(temp_root),
            env=env,
            timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_cli_evidence_show_found() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        record = store.create(
            run_id="r1",
            task_id=None,
            tool_name="t",
            source="s",
            raw_chars=100,
            summary="show me",
            key_points=["kp"],
        )

        import os
        import subprocess
        import sys

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "minibot", "evidence", "show", record["evidence_id"]],
            capture_output=True,
            text=True,
            cwd=str(temp_root),
            env=env,
            timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["evidence_id"] == record["evidence_id"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_cli_evidence_show_missing() -> None:
    import os
    import subprocess
    import sys

    temp_root = _prepare_temp_root()
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "minibot", "evidence", "show", "ev_nonexistent"],
            capture_output=True,
            text=True,
            cwd=str(temp_root),
            env=env,
            timeout=10,
        )
        assert result.returncode == 1
        assert "evidence_not_found" in result.stderr
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_cli_evidence_search() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        store.create(
            run_id="r1",
            task_id=None,
            tool_name="web_fetch",
            source="https://docker.com",
            raw_chars=2000,
            summary="Docker Compose deployment guide",
            key_points=["Use compose"],
        )

        import os
        import subprocess
        import sys

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, "-m", "minibot", "evidence", "search", "Docker deployment"],
            capture_output=True,
            text=True,
            cwd=str(temp_root),
            env=env,
            timeout=10,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Regression — non-interference
# ---------------------------------------------------------------------------


def test_evidence_does_not_affect_safety() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        # Normal chat
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="ev-safety", content="hello"
            )
        )
        assert result.response == "MiniBot echo: hello"
        # Tool call
        result2 = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test", user_id="tester", session_id="ev-safety", content="calculate 1 + 1"
            )
        )
        assert result2.response == "MiniBot tool result: 2"
        # /new still works
        result3 = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="ev-safety", content="/new")
        )
        assert "archived" in result3.response.lower()
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_does_not_affect_real_agent_flow() -> None:
    temp_root = _prepare_temp_root(evidence={"enabled": True, "tool_output_min_chars": 100})
    try:
        app = MiniBotApp(temp_root)
        # Multi-round: the fake model should still detect calculator expressions
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="ev-real-agent",
                content="calculate 128 * 64",
            )
        )
        assert result.response == "MiniBot tool result: 8192"
        # Check run record has evidence fields
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert "evidence_ids" in record
        assert "evidence_count" in record
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_evidence_jsonl_persists_across_store_reloads() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        store = app.runtime.evidence_store
        record_id = store.create(
            run_id="r-persist",
            task_id=None,
            tool_name="t",
            source="s",
            raw_chars=100,
            summary="persist",
            key_points=[],
        )["evidence_id"]

        # Reload store
        from minibot.evidence.store import EvidenceStore

        reloaded = EvidenceStore(app.runtime.workspace.evidence_dir)
        retrieved = reloaded.get(record_id)
        assert retrieved is not None
        assert retrieved["summary"] == "persist"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
