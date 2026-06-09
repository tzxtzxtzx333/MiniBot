from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from minibot.app import MiniBotApp
from minibot.channels.base import ChannelMessage
from minibot.context.placeholder_cleaner import PlaceholderCleaner
from minibot.harness.context_builder import ContextBuilder
from minibot.json_utils import load_json_file
from minibot.memory.recall import MemoryRecall


ROOT = Path(__file__).resolve().parents[1]


def _prepare_temp_root(
    *,
    hooks: dict[str, object] | None = None,
    chat_turn_limit: int | None = None,
    token_budget: int | None = None,
    archive_token_budget: int | None = None,
    auto_compact_enabled: bool | None = None,
    history_turn_compact_threshold: int | None = None,
    history_compact_keep_recent: int | None = None,
    history_retrieval_enabled: bool | None = None,
) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "configs", temp_root / "configs")
    for name in ("benchmarks", "examples", "reports"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)

    config_path = temp_root / "configs" / "minibot.json"
    config = load_json_file(config_path)
    if chat_turn_limit is not None:
        config["chat_turn_limit"] = chat_turn_limit
    if token_budget is not None:
        config["context_token_budget"] = token_budget
    if archive_token_budget is not None:
        config["archive_token_budget"] = archive_token_budget
    if auto_compact_enabled is not None or history_turn_compact_threshold is not None or history_compact_keep_recent is not None:
        memory = config.get("memory", {})
        if isinstance(memory, dict):
            memory = dict(memory)
        else:
            memory = {}
        if auto_compact_enabled is not None:
            memory["auto_compact_enabled"] = auto_compact_enabled
        if history_turn_compact_threshold is not None:
            memory["history_turn_compact_threshold"] = history_turn_compact_threshold
        if history_compact_keep_recent is not None:
            memory["history_compact_keep_recent"] = history_compact_keep_recent
        config["memory"] = memory
    if history_retrieval_enabled is not None:
        retrieval = config.get("history_retrieval", {})
        if isinstance(retrieval, dict):
            retrieval = dict(retrieval)
        else:
            retrieval = {}
        retrieval["enabled"] = history_retrieval_enabled
        config["history_retrieval"] = retrieval
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    hooks_config = hooks if hooks is not None else {"hooks": []}
    (temp_root / "configs" / "hooks.json").write_text(
        json.dumps(hooks_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return temp_root


def test_user_remember_writes_long_term_memory() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="remember-session",
                content="记住 我喜欢黑咖啡",
            )
        )
        memory_text = app.runtime.workspace.read_memory()
        assert "我喜欢黑咖啡" in memory_text
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_user_remember_deduplicates_existing_memory_entries() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.workspace.memory_file.write_text(
            "# MEMORY\n\n- 鎴戝枩娆㈤奔\n- 鎴戝枩娆腑鏂囧洖绛?\n",
            encoding="utf-8",
        )
        app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="remember-dedupe-session",
                content="璁颁綇 鎴戝枩娆㈤奔",
            )
        )
        memory_lines = app.runtime.workspace.read_memory().splitlines()
        assert memory_lines.count("- 鎴戝枩娆㈤奔") == 1
        assert memory_lines.count("- 鎴戝枩娆腑鏂囧洖绛?") == 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_new_command_compacts_history_and_resets_recent_history() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="new-session",
                content="hello archive",
            )
        )
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="new-session",
                content="/new",
            )
        )

        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives
        latest_archive = archives[-1].read_text(encoding="utf-8")
        assert "summary_by: SummarizerAgent" in latest_archive
        assert "archive_mode: fake" in latest_archive
        assert "archive_model_provider: fake" in latest_archive
        assert "archive_model_name: fake" in latest_archive
        assert "compression_trigger: manual_new" in latest_archive
        history_text = app.runtime.workspace.read_history()
        assert "hello archive" not in history_text
        assert result.response == "MiniBot archived the recent session and started a new history window."

        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["compression_events"]
        assert record["compression_events"][0]["trigger"] == "manual_new"
        assert record["compression_events"][0]["archive_mode"] == "fake"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_new_command_uses_real_summarizer_and_records_real_archive_metadata(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        monkeypatch.setenv("MINIBOT_MODEL_MODE", "real")
        monkeypatch.setenv("MINIBOT_MODEL_PROVIDER", "deepseek")
        monkeypatch.setenv("MINIBOT_MODEL_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("MINIBOT_MODEL_API_KEY", "test-key")
        monkeypatch.setenv("MINIBOT_MODEL_NAME", "deepseek-chat")

        def fake_urlopen(request, timeout: int = 0):  # noqa: ANN001, ARG001
            return type(
                "_Response",
                (),
                {
                    "read": lambda self: json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": "## 用户目标\n- real archive\n\n## 后续建议\n- continue"
                                    }
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    "__enter__": lambda self: self,
                    "__exit__": lambda self, exc_type, exc, tb: None,
                },
            )()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-new-session", content="hello archive")
        )
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="real-new-session", content="/new")
        )

        latest_archive = sorted(app.runtime.workspace.archives_dir.glob("*.md"))[-1].read_text(encoding="utf-8")
        assert "archive_mode: real" in latest_archive
        assert "archive_model_provider: deepseek" in latest_archive
        assert "archive_model_name: deepseek-chat" in latest_archive
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["compression_events"][0]["archive_mode"] == "real"
        assert record["compression_events"][0]["archive_model_provider"] == "deepseek"
        assert record["compression_events"][0]["archive_model_name"] == "deepseek-chat"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_history_compacts_when_turn_threshold_is_exceeded() -> None:
    temp_root = _prepare_temp_root(history_turn_compact_threshold=2, history_compact_keep_recent=1)
    try:
        app = MiniBotApp(temp_root)
        compaction_run_id: str | None = None
        for index in range(5):
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(
                    channel="test",
                    user_id="tester",
                    session_id="turn-threshold-session",
                    content=f"message {index}",
                )
            )
            # The 3rd run (index=2, turn_count=3 > threshold=2) should trigger compaction
            if index == 2:
                compaction_run_id = result.run_id
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives
        assert compaction_run_id is not None
        run_path = app.runtime.workspace.runs_dir / f"{compaction_run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert any(event["trigger"] == "turn_threshold" for event in record["compression_events"])
        # Verify HISTORY.md is truncated (not fully reset)
        history_text = app.runtime.workspace.read_history()
        # Should keep at most keep_recent=1 turn, so at most 1 "user:" line
        user_lines = [line for line in history_text.splitlines() if line.startswith("user: ")]
        assert len(user_lines) <= 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_context_builder_injects_memory_and_recalls_history_and_archives() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.workspace.memory_file.write_text("# MEMORY\n\n- project: alpha rollout\n", encoding="utf-8")
        app.runtime.workspace.history_file.write_text("# HISTORY\n\nuser: alpha checklist\n", encoding="utf-8")
        archive_path = app.runtime.workspace.archives_dir / "archive-alpha.md"
        archive_path.write_text(
            "# ARCHIVE\n\nsummary_by: SummarizerAgent\nsource_session_id: sess\ncreated_at: now\ntoken_before: 10\ntoken_after: 5\ncompression_trigger: user_new_command\n\n## 关键事实\n- alpha owner is Alice\n",
            encoding="utf-8",
        )

        context = app.runtime.agent_loop.context_builder.build(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="context-session",
                content="alpha owner",
            )
        )
        assert "alpha rollout" in str(context["memory"])
        recalled = "\n".join(str(item) for item in context["recalled_memories"])
        assert "alpha checklist" in recalled
        assert "alpha owner is Alice" in recalled
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_memory_recall_skips_unreadable_archives_and_directories(monkeypatch) -> None:
    temp_root = _prepare_temp_root()
    try:
        archives_dir = temp_root / ".minibot" / "archives"
        archives_dir.mkdir(parents=True, exist_ok=True)
        good_archive = archives_dir / "good.md"
        denied_archive = archives_dir / "denied.md"
        binary_archive = archives_dir / "binary.md"
        nested_dir = archives_dir / "nested.md"
        good_archive.write_text("# ARCHIVE\n- Atlas owner is Alice\n", encoding="utf-8")
        denied_archive.write_text("# ARCHIVE\n- should be skipped\n", encoding="utf-8")
        binary_archive.write_bytes(b"\xff\xfe\x00\x00")
        nested_dir.mkdir()

        original_read_text = Path.read_text

        def fake_read_text(self: Path, *args, **kwargs):  # noqa: ANN001
            if self == denied_archive:
                raise PermissionError(str(self))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fake_read_text)
        recall = MemoryRecall()
        snippets = recall.recall("Atlas owner", archives_dir=archives_dir)
        joined = "\n".join(snippets)
        assert "Atlas owner is Alice" in joined
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_placeholder_cleaner_removes_invalid_placeholders_and_truncates_tool_output() -> None:
    cleaner = PlaceholderCleaner(max_tool_output_chars=40)
    dirty_context = {
        "system_prompt": "system one\nsystem one\n",
        "memory": "TODO keep\n<pending>\nreal fact",
        "history": "None\nreal line",
        "message": "hello",
        "recalled_memories": ["<pending>", "useful memory"],
        "tool_results": [{}, {"tool_name": "demo", "output": {"result": "x" * 120}}],
    }

    cleaned, meta = cleaner.clean_context(dirty_context)
    assert "TODO" not in cleaned["memory"]
    assert "<pending>" not in cleaned["memory"]
    assert cleaned["recalled_memories"] == ["useful memory"]
    assert len(cleaned["tool_results"]) == 1
    assert "..." in json.dumps(cleaned["tool_results"][0], ensure_ascii=False)
    assert meta["cleaned_placeholders"]


def test_agent_loop_records_cleaned_placeholders_in_trace() -> None:
    temp_root = _prepare_temp_root(history_retrieval_enabled=False)
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.context_builder.enable_history_retrieval = False
        app.runtime.workspace.history_file.write_text("# HISTORY\n\nTODO\n<pending>\nreal history\n", encoding="utf-8")
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="clean-session",
                content="hello cleaner",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert isinstance(record["cleaned_placeholders"], int)
        assert record["cleaned_placeholders"] > 0
        assert isinstance(record["cleaned_placeholder_items"], list)
        assert record["cleaned_placeholder_items"]
        assert isinstance(record["compression_events"], list)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_agent_loop_records_zero_cleaned_placeholders_and_empty_compression_events() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="clean-zero-session",
                content="PlaceholderCleaner 瀛楁娴嬭瘯",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["cleaned_placeholders"] == 0
        assert record["cleaned_placeholder_items"] == []
        assert record["compression_events"] == []
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_minibot_app_loads_bom_prefixed_minibot_config() -> None:
    temp_root = _prepare_temp_root()
    try:
        config_path = temp_root / "configs" / "minibot.json"
        bom_prefixed = "\ufeff" + config_path.read_text(encoding="utf-8")
        config_path.write_text(bom_prefixed, encoding="utf-8")
        app = MiniBotApp(temp_root)
        assert app.runtime.config.app_name == "MiniBot"
        assert app.runtime.config.workspace_dir == ".minibot"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# HISTORY relevance retrieval tests
# ---------------------------------------------------------------------------


def test_history_retriever_scores_relevant_turns_above_irrelevant() -> None:
    from minibot.memory.history_retriever import HistoryRetriever

    retriever = HistoryRetriever(top_k=3, max_chars=4000)
    history = (
        "# HISTORY\n\n"
        "user: what is the weather today\n"
        "assistant: It is sunny and 25C.\n"
        "user: tell me about the history of Rome\n"
        "assistant: Rome was founded in 753 BC...\n"
        "user: what is the capital of France\n"
        "assistant: The capital of France is Paris.\n"
        "user: how do I deploy a Python app\n"
        "assistant: You can use Docker or a simple script...\n"
    )
    result = retriever.retrieve("Python deploy Docker", history)
    assert result["history_retrieval_mode"] == "relevance"
    assert result["retrieved_history_count"] >= 1
    # The deploy turn should be scored highest
    assert "deploy" in result["history_text"].lower() or "python app" in result["history_text"].lower()
    # Irrelevant weather turn should not appear in top_k
    # (it might appear if top_k is large enough, but our score should rank deploy above weather)
    retrieved = result["history_text"]
    deploy_idx = retrieved.lower().find("deploy")
    weather_idx = retrieved.lower().find("weather")
    if deploy_idx >= 0 and weather_idx >= 0:
        assert deploy_idx < weather_idx, "deploy should appear before weather in scored output"


def test_context_builder_injects_retrieved_history_metadata() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.workspace.history_file.write_text(
            "# HISTORY\n\n"
            "user: python docker deploy guide\n"
            "assistant: Use Dockerfile and docker-compose...\n"
            "user: what is the weather\n"
            "assistant: Sunny today.\n",
            encoding="utf-8",
        )
        context = app.runtime.agent_loop.context_builder.build(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="retrieval-meta-session",
                content="how to deploy python",
            )
        )
        # History text should contain the relevant turn
        history = str(context.get("history", ""))
        assert "deploy" in history.lower()
        # History meta should be present
        history_meta = context.get("_history_meta", {})
        assert history_meta.get("history_retrieval_mode") == "relevance"
        assert history_meta.get("retrieved_history_count", 0) >= 1
        assert history_meta.get("retrieved_history_chars", 0) > 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_history_retrieval_empty_history_does_not_error() -> None:
    from minibot.memory.history_retriever import HistoryRetriever

    retriever = HistoryRetriever()
    result = retriever.retrieve("any query", "# HISTORY\n\n")
    assert result["history_retrieval_mode"] == "relevance"
    assert result["retrieved_history_count"] == 0
    assert result["history_text"] == ""


def test_history_retrieval_disabled_returns_full_history() -> None:
    from minibot.memory.history_retriever import HistoryRetriever

    retriever = HistoryRetriever(enabled=False)
    history_text = "# HISTORY\n\nuser: hello\nassistant: hi\n"
    result = retriever.retrieve("any query", history_text)
    assert result["history_retrieval_mode"] == "full"
    assert result["history_text"] == history_text


def test_run_record_includes_history_retrieval_fields() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.workspace.history_file.write_text(
            "# HISTORY\n\n"
            "user: python deployment best practices\n"
            "assistant: Use CI/CD pipelines and Docker...\n"
            "user: unrelated topic about cooking\n"
            "assistant: Cooking is fun...\n",
            encoding="utf-8",
        )
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(
                channel="test",
                user_id="tester",
                session_id="retrieval-record-session",
                content="python deployment",
            )
        )
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        context_metrics = record.get("context_metrics", {})
        assert context_metrics.get("history_retrieval_mode") == "relevance"
        assert context_metrics.get("retrieved_history_count", 0) >= 1
        assert context_metrics.get("retrieved_history_chars", 0) > 0
        context_summary = str(record.get("context_summary", ""))
        assert "history_retrieval_mode=relevance" in context_summary
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Turn threshold auto-compaction tests
# ---------------------------------------------------------------------------


def test_auto_compact_triggers_on_turn_threshold_and_keeps_recent_turns() -> None:
    temp_root = _prepare_temp_root(history_turn_compact_threshold=2, history_compact_keep_recent=1)
    try:
        app = MiniBotApp(temp_root)
        for index in range(5):
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(
                    channel="test",
                    user_id="tester",
                    session_id="auto-compact-session",
                    content=f"message {index}",
                )
            )
        # Archive should exist
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives, "Expected at least one archive file"

        # Latest archive should have the new metadata fields
        latest_archive = sorted(archives, key=lambda p: p.stat().st_mtime)[-1].read_text(encoding="utf-8")
        assert "history_turn_count_before:" in latest_archive
        assert "history_turn_count_after:" in latest_archive

        # Run record should have compression event with turn_threshold trigger
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        compression_events = record.get("compression_events", [])
        turn_threshold_events = [e for e in compression_events if e.get("trigger") == "turn_threshold"]
        assert turn_threshold_events, "Expected at least one turn_threshold compression event"
        event = turn_threshold_events[-1]
        assert event.get("compression_trigger") == "turn_threshold"
        assert event.get("history_turn_count_before", 0) > event.get("history_turn_count_after", 0)

        # HISTORY should be truncated to at most keep_recent turns
        history_text = app.runtime.workspace.read_history()
        user_lines = [line for line in history_text.splitlines() if line.startswith("user: ")]
        assert len(user_lines) <= 1, f"Expected <=1 user lines, got {len(user_lines)}"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_new_command_trigger_is_manual_new() -> None:
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
        app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="manual-new-session", content="some chat")
        )
        result = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="manual-new-session", content="/new")
        )
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives
        latest_archive = archives[-1].read_text(encoding="utf-8")
        assert "compression_trigger: manual_new" in latest_archive
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["compression_events"][0]["trigger"] == "manual_new"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_auto_compact_disabled_does_not_trigger() -> None:
    temp_root = _prepare_temp_root(
        auto_compact_enabled=False, history_turn_compact_threshold=2, history_compact_keep_recent=1
    )
    try:
        app = MiniBotApp(temp_root)
        for index in range(5):
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(
                    channel="test",
                    user_id="tester",
                    session_id="no-auto-compact-session",
                    content=f"message {index}",
                )
            )
        # No archive should exist since auto_compact is disabled
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert not archives, "Expected no archives when auto_compact is disabled"
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record.get("compression_events", []) == []
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_summarizer_failure_preserves_history() -> None:
    """If the summarizer fails (returns empty), HISTORY should not be lost."""
    temp_root = _prepare_temp_root(history_turn_compact_threshold=1, history_compact_keep_recent=1)
    try:
        app = MiniBotApp(temp_root)
        # Write pre-existing history
        app.runtime.workspace.history_file.write_text(
            "# HISTORY\n\nuser: important turn 1\nassistant: response 1\nuser: important turn 2\nassistant: response 2\n",
            encoding="utf-8",
        )
        history_before = app.runtime.workspace.read_history()
        assert "important turn 1" in history_before

        # Monkey-patch summarizer to fail
        original_summarize = app.runtime.summarizer_agent.summarize

        def failing_summarize(history_text: str = "", memory_text: str = "") -> dict[str, object]:  # noqa: ARG001
            raise RuntimeError("simulated summarizer failure")

        app.runtime.summarizer_agent.summarize = failing_summarize

        try:
            app.runtime.agent_loop.handle_message(
                ChannelMessage(
                    channel="test",
                    user_id="tester",
                    session_id="summarizer-fail-session",
                    content="trigger compaction",
                )
            )
        except RuntimeError:
            pass  # Expected \u2014 summarizer failure

        # Restore summarizer
        app.runtime.summarizer_agent.summarize = original_summarize

        # HISTORY must still contain the original turns (not wiped)
        history_after = app.runtime.workspace.read_history()
        assert "important turn 1" in history_after, "HISTORY should not be lost on summarizer failure"
        assert "important turn 2" in history_after, "HISTORY should not be lost on summarizer failure"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_auto_compact_does_not_affect_safety_or_multiround() -> None:
    """Verify that auto-compact doesn't interfere with basic chat/tool behavior."""
    temp_root = _prepare_temp_root(history_turn_compact_threshold=10, history_compact_keep_recent=5)
    try:
        app = MiniBotApp(temp_root)
        # Normal chat
        result1 = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="safety-session", content="hello")
        )
        assert result1.response == "MiniBot echo: hello"

        # Tool call
        result2 = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="safety-session", content="calculate 1 + 1")
        )
        assert result2.response == "MiniBot tool result: 2"

        # /new still produces archive
        result3 = app.runtime.agent_loop.handle_message(
            ChannelMessage(channel="test", user_id="tester", session_id="safety-session", content="/new")
        )
        assert "archived" in result3.response.lower()
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives
        assert "compression_trigger: manual_new" in archives[-1].read_text(encoding="utf-8")
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
