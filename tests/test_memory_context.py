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
) -> Path:
    temp_root = ROOT / ".tmp_test_roots" / str(uuid4())
    temp_root.mkdir(parents=True, exist_ok=True)
    for name in ("configs", "benchmarks", "examples", "reports"):
        source = ROOT / name
        target = temp_root / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    config_path = temp_root / "configs" / "minibot.json"
    config = load_json_file(config_path)
    if chat_turn_limit is not None:
        config["chat_turn_limit"] = chat_turn_limit
    if token_budget is not None:
        config["context_token_budget"] = token_budget
    if archive_token_budget is not None:
        config["archive_token_budget"] = archive_token_budget
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
        assert "compression_trigger: user_new_command" in latest_archive
        history_text = app.runtime.workspace.read_history()
        assert "hello archive" not in history_text
        assert result.response == "MiniBot archived the recent session and started a new history window."

        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert record["compression_events"]
        assert record["compression_events"][0]["trigger"] == "user_new_command"
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


def test_history_compacts_when_turn_limit_is_exceeded() -> None:
    temp_root = _prepare_temp_root(chat_turn_limit=2)
    try:
        app = MiniBotApp(temp_root)
        for index in range(3):
            result = app.runtime.agent_loop.handle_message(
                ChannelMessage(
                    channel="test",
                    user_id="tester",
                    session_id="turn-limit-session",
                    content=f"message {index}",
                )
            )
        archives = list(app.runtime.workspace.archives_dir.glob("*.md"))
        assert archives
        run_path = app.runtime.workspace.runs_dir / f"{result.run_id}.json"
        record = json.loads(run_path.read_text(encoding="utf-8"))
        assert any(event["trigger"] == "turn_limit_exceeded" for event in record["compression_events"])
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
    temp_root = _prepare_temp_root()
    try:
        app = MiniBotApp(temp_root)
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
