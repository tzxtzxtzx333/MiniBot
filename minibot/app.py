"""Application assembly for MiniBot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import MiniBotConfig, load_config
from .context.history_truncator import HistoryTruncator
from .context.placeholder_cleaner import PlaceholderCleaner
from .context.prompt_builder import PromptBuilder
from .context.token_budget import TokenBudget
from .evals.benchmark_runner import BenchmarkRunner
from .evidence.store import EvidenceStore
from .evidence.summarizer import EvidenceSummarizer
from .governance.approval_store import ApprovalStore
from .governance.policy_manager import ToolPolicyManager
from .harness.agent_loop import AgentLoop
from .harness.context_builder import ContextBuilder
from .harness.model_client import _load_env_settings, load_model_client
from .harness.run_recorder import RunRecorder
from .harness.tool_dispatcher import ToolDispatcher
from .hooks.hook_manager import HookManager
from .memory.archive import ArchiveWriter
from .memory.compactor import MemoryCompactor
from .memory.history_retriever import HistoryRetriever
from .memory.recall import MemoryRecall
from .memory.store import MemoryStore
from .planning.long_task_runner import LongTaskRunner
from .planning.planner_agent import PlannerAgent
from .planning.replanner_agent import ReplannerAgent
from .planning.step_verifier import StepVerifier
from .planning.task_executor import TaskExecutor
from .status import MiniBotStatusService
from .subagents.memory_agent import MemoryAgent
from .subagents.summarizer_agent import SummarizerAgent
from .subagents.tool_agent import ToolAgent
from .subagents.verifier_agent import VerifierAgent
from .tasks.store import TaskStore
from .workspace import WorkspaceManager


@dataclass(slots=True)
class MiniBotRuntime:
    """Container for assembled runtime services."""

    config: MiniBotConfig
    workspace: WorkspaceManager
    memory_store: MemoryStore
    recorder: RunRecorder
    model_client: object
    tool_dispatcher: ToolDispatcher
    memory_agent: MemoryAgent
    summarizer_agent: SummarizerAgent
    tool_agent: ToolAgent
    verifier_agent: VerifierAgent
    agent_loop: AgentLoop
    status_service: MiniBotStatusService
    benchmark_runner: BenchmarkRunner
    hook_manager: HookManager
    evidence_store: EvidenceStore
    evidence_summarizer: EvidenceSummarizer
    planner_agent: PlannerAgent
    task_executor: TaskExecutor
    long_task_runner: LongTaskRunner


class MiniBotApp:
    """Create and hold the first-stage MiniBot application runtime."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path.cwd()).resolve()
        self.runtime = self._build_runtime()

    def _build_runtime(self) -> MiniBotRuntime:
        config = load_config(self.root / "configs" / "minibot.json")
        model_client = load_model_client(project_root=self.root, mode=config.model_mode)
        workspace = WorkspaceManager(self.root, config.workspace_dir)
        workspace.ensure()
        token_budget = TokenBudget()
        settings: dict[str, str] = {}
        if config.model_mode == "real":
            settings = _load_env_settings(self.root)
            summarizer_agent = SummarizerAgent(
                mode="real",
                model_provider=settings["MINIBOT_MODEL_PROVIDER"],
                model_name=settings["MINIBOT_MODEL_NAME"],
                model_base_url=settings["MINIBOT_MODEL_BASE_URL"],
                model_api_key=settings["MINIBOT_MODEL_API_KEY"],
            )
        else:
            summarizer_agent = SummarizerAgent(mode="fake")
        archive_writer = ArchiveWriter(workspace.archives_dir)
        memory_compactor = MemoryCompactor(
            summarizer_agent=summarizer_agent,
            archive_writer=archive_writer,
            token_budget=token_budget,
        )
        memory_store = MemoryStore(workspace, compactor=memory_compactor, token_budget=token_budget)
        recorder = RunRecorder(workspace.runs_dir)
        prompt_builder = PromptBuilder()
        history_retriever = HistoryRetriever(
            enabled=config.history_retrieval.enabled,
            mode=config.history_retrieval.mode,
            top_k=config.history_retrieval.top_k,
            max_chars=config.history_retrieval.max_chars,
        )
        context_builder = ContextBuilder(
            workspace=workspace,
            prompt_builder=prompt_builder,
            memory_recall=MemoryRecall(),
            placeholder_cleaner=PlaceholderCleaner(),
            token_budget=token_budget,
            history_truncator=HistoryTruncator(token_budget=token_budget),
            history_retriever=history_retriever,
            context_token_budget=config.context_token_budget,
        )
        hook_manager = HookManager(self.root / "configs" / "hooks.json")
        tool_dispatcher = ToolDispatcher(
            policy_manager=ToolPolicyManager(self.root),
            project_root=self.root,
            workspace=workspace,
            memory_store=memory_store,
            memory_recall=context_builder.memory_recall,
        )
        evidence_store = EvidenceStore(workspace.evidence_dir)
        evidence_summarizer = EvidenceSummarizer(
            mode=config.model_mode,
            summary_max_chars=config.evidence.summary_max_chars,
            key_points_max=config.evidence.key_points_max,
            external_summarizer=summarizer_agent if config.model_mode == "real" else None,
        )
        memory_agent = MemoryAgent()
        tool_agent = ToolAgent(tool_dispatcher)
        verifier_agent = VerifierAgent()
        agent_loop = AgentLoop(
            model_client=model_client,
            context_builder=context_builder,
            tool_dispatcher=tool_dispatcher,
            memory_store=memory_store,
            recorder=recorder,
            hook_manager=hook_manager,
            memory_agent=memory_agent,
            tool_agent=tool_agent,
            verifier_agent=verifier_agent,
            chat_turn_limit=config.chat_turn_limit,
            budget=config.budget,
            archive_token_budget=config.archive_token_budget,
            auto_compact_enabled=config.memory.auto_compact_enabled,
            history_turn_compact_threshold=config.memory.history_turn_compact_threshold,
            history_compact_keep_recent=config.memory.history_compact_keep_recent,
            evidence_store=evidence_store,
            evidence_summarizer=evidence_summarizer,
            evidence_enabled=config.evidence.enabled,
            tool_output_min_chars=config.evidence.tool_output_min_chars,
        )
        task_store = TaskStore(workspace.root / "tasks")
        approval_store = ApprovalStore(workspace.approvals_dir)
        status_service = MiniBotStatusService(
            self.root,
            config,
            workspace,
            task_store=task_store,
            approval_store=approval_store,
        )
        step_verifier = StepVerifier(mode=config.model_mode, external_verifier=verifier_agent)
        planner_agent = PlannerAgent(
            mode=config.model_mode,
            model_provider=(
                settings.get("MINIBOT_MODEL_PROVIDER", "fake")
                if config.model_mode == "real"
                else "fake"
            ),
            model_name=(
                settings.get("MINIBOT_MODEL_NAME", "fake")
                if config.model_mode == "real"
                else "fake"
            ),
            model_base_url=(
                settings.get("MINIBOT_MODEL_BASE_URL") if config.model_mode == "real" else None
            ),
            model_api_key=(
                settings.get("MINIBOT_MODEL_API_KEY") if config.model_mode == "real" else None
            ),
        )
        task_executor = TaskExecutor(
            agent_loop=agent_loop,
            step_verifier=step_verifier,
            plan_store_dir=workspace.plans_dir,
            workspace=workspace,
        )
        replanner = ReplannerAgent(mode=config.model_mode)
        long_task_runner = LongTaskRunner(
            task_executor=task_executor,
            replanner=replanner,
            task_store=task_store,
        )
        benchmark_runner = BenchmarkRunner(
            agent_loop,
            self.root,
            verifier_agent=verifier_agent,
            long_task_runner=long_task_runner,
            planner_agent=planner_agent,
        )
        return MiniBotRuntime(
            config=config,
            workspace=workspace,
            memory_store=memory_store,
            recorder=recorder,
            model_client=model_client,
            tool_dispatcher=tool_dispatcher,
            memory_agent=memory_agent,
            summarizer_agent=summarizer_agent,
            tool_agent=tool_agent,
            verifier_agent=verifier_agent,
            agent_loop=agent_loop,
            status_service=status_service,
            benchmark_runner=benchmark_runner,
            hook_manager=hook_manager,
            evidence_store=evidence_store,
            evidence_summarizer=evidence_summarizer,
            planner_agent=planner_agent,
            task_executor=task_executor,
            long_task_runner=long_task_runner,
        )
