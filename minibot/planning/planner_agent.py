"""PlannerAgent — break a user goal into a linear TaskPlan."""

from __future__ import annotations

import json
import urllib.request
from uuid import uuid4

from .plan_schema import Step, TaskPlan, make_single_step_plan, plan_from_json


class PlannerAgent:
    """Generate a TaskPlan from a user goal.

    In fake mode the planner uses a lightweight keyword-based rule
    engine.  In real mode it delegates to an LLM and validates the
    returned JSON.
    """

    def __init__(
        self,
        *,
        mode: str = "fake",
        model_provider: str = "fake",
        model_name: str = "fake",
        model_base_url: str | None = None,
        model_api_key: str | None = None,
    ) -> None:
        self.mode = mode
        self.model_provider = model_provider
        self.model_name = model_name
        self.model_base_url = model_base_url
        self.model_api_key = model_api_key

    def plan(self, goal: str, task_id: str | None = None) -> TaskPlan:
        """Generate a TaskPlan for *goal*.

        Falls back to a single-step plan when LLM output cannot be parsed.
        """
        if self.mode == "real":
            try:
                result = plan_from_json(self._plan_real(goal), task_id=task_id)
                if result is not None:
                    return result
            except Exception:
                pass
        else:
            plan = self._plan_fake(goal, task_id)
            if plan is not None:
                return plan

        return make_single_step_plan(goal, task_id)

    # ------------------------------------------------------------------
    # Fake / rule-based planning
    # ------------------------------------------------------------------

    def _plan_fake(self, goal: str, task_id: str | None = None) -> TaskPlan | None:
        """Decompose *goal* into steps using keyword rules."""
        steps = self._decompose_goal(goal)
        if not steps:
            return None
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        return TaskPlan(
            plan_id=f"plan_{uuid4().hex[:12]}",
            task_id=task_id,
            goal=goal,
            status="pending",
            steps=steps,
            created_at=now,
            updated_at=now,
        )

    def _decompose_goal(self, goal: str) -> list[Step]:
        """Heuristic decomposition using keyword patterns."""
        goal_lower = goal.lower()
        steps: list[Step] = []
        step_index = 0

        # Pattern: "read X and Y, summarize/write Z"
        if "读取" in goal or "read" in goal_lower:
            # Extract file-related sub-tasks
            if "readme" in goal_lower or "readme.md" in goal_lower:
                step_index += 1
                steps.append(
                    Step(
                        step_id=f"s{step_index}",
                        description="读取 README.md",
                        expected_output="项目能力摘要",
                        tool_hints=["file_read"],
                    )
                )
            if "resume_mapping" in goal_lower or "docs" in goal:
                step_index += 1
                steps.append(
                    Step(
                        step_id=f"s{step_index}",
                        description="读取 docs/resume_mapping.md",
                        expected_output="简历映射与能力清单",
                        tool_hints=["file_read"],
                    )
                )
            # Any other file reading
            for keyword in ["architecture.md", "decisions.md", "final_acceptance.md"]:
                if keyword in goal_lower:
                    step_index += 1
                    steps.append(
                        Step(
                            step_id=f"s{step_index}",
                            description=f"读取 {keyword}",
                            expected_output="文档内容",
                            tool_hints=["file_read"],
                        )
                    )

        if "总结" in goal or "summarize" in goal_lower or "汇总" in goal:
            step_index += 1
            steps.append(
                Step(
                    step_id=f"s{step_index}",
                    description="总结当前能力边界",
                    expected_output="MiniBot 能力边界总结",
                    tool_hints=[],
                )
            )

        if "写" in goal or "write" in goal_lower or "写入" in goal:
            # Extract file name — look for the target file name near "write" / "写入"
            import re

            # Find the write-related portion of the goal
            write_match = re.search(
                r"(?:写入|写到|write\s+(?:to\s+)?)\s*([\w./]+\.(?:md|txt|json))",
                goal,
                re.IGNORECASE,
            )
            if write_match:
                target_file = write_match.group(1)
            else:
                # Fallback: find the last .md/.txt/.json filename mentioned
                all_files = re.findall(r"[\w./]+\.(?:md|txt|json)", goal)
                target_file = all_files[-1] if all_files else "output.md"
            step_index += 1
            steps.append(
                Step(
                    step_id=f"s{step_index}",
                    description=f"写入 {target_file}",
                    expected_output=f"文件 {target_file} 写入成功",
                    tool_hints=["file_write"],
                )
            )

        if "搜索" in goal or "search" in goal_lower:
            step_index += 1
            steps.append(
                Step(
                    step_id=f"s{step_index}",
                    description=f"搜索: {goal}",
                    expected_output="搜索结果",
                    tool_hints=["web_search", "web_fetch"],
                )
            )

        if "计算" in goal or "calculate" in goal_lower:
            step_index += 1
            steps.append(
                Step(
                    step_id=f"s{step_index}",
                    description=f"计算: {goal}",
                    expected_output="计算结果",
                    tool_hints=["calculator"],
                )
            )

        # If no specific patterns matched, create a generic step
        if not steps:
            step_index += 1
            steps.append(
                Step(
                    step_id=f"s{step_index}",
                    description=goal,
                    expected_output="task completed",
                    tool_hints=[],
                )
            )

        return steps

    # ------------------------------------------------------------------
    # Real / LLM planning
    # ------------------------------------------------------------------

    def _plan_real(self, goal: str) -> str:
        """Call an LLM to produce a plan JSON string."""
        if not self.model_base_url or not self.model_api_key:
            raise RuntimeError("real planner requires model_base_url and model_api_key")

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a task planner that decomposes user goals into linear steps. "
                        "Output ONLY valid JSON matching this schema:\n"
                        '{"plan_id": "plan_xxx", "goal": "...", "steps": ['
                        '{"step_id": "s1", "description": "...", "expected_output": "...", '
                        '"tool_hints": ["file_read"], "depends_on": []}]}\n'
                        "Each step MUST have step_id and description. "
                        "tool_hints can suggest tools: file_read, file_write, web_search, web_fetch, "
                        "calculator, python_exec, shell_exec, memory_search. "
                        "Keep steps linear — depends_on is optional."
                    ),
                },
                {"role": "user", "content": f"Decompose this goal into linear steps:\n{goal}"},
            ],
        }
        request = urllib.request.Request(
            f"{str(self.model_base_url).rstrip('/')}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.model_api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            response_payload = json.loads(response.read().decode("utf-8"))
        content = str(response_payload["choices"][0]["message"].get("content", "")).strip()
        # Extract JSON from markdown fences if present
        if content.startswith("```"):
            lines = content.splitlines()
            json_lines = [line for line in lines[1:] if not line.startswith("```")]
            content = "\n".join(json_lines)
        return content
