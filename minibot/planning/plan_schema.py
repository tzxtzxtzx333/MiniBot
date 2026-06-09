"""TaskPlan and Step data model with JSON validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

_VALID_PLAN_STATUSES = frozenset({"pending", "running", "completed", "failed", "waiting_approval"})
_VALID_STEP_STATUSES = frozenset({"pending", "running", "completed", "failed", "skipped", "waiting_approval"})


@dataclass(slots=True)
class Step:
    """A single step within a TaskPlan."""

    step_id: str
    description: str
    expected_output: str = ""
    status: str = "pending"
    tool_hints: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    run_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    failure_category: str | None = None
    retry_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "expected_output": self.expected_output,
            "status": self.status,
            "tool_hints": self.tool_hints,
            "depends_on": self.depends_on,
            "run_id": self.run_id,
            "evidence_ids": self.evidence_ids,
            "failure_category": self.failure_category,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Step":
        return cls(
            step_id=str(data.get("step_id", "")),
            description=str(data.get("description", "")),
            expected_output=str(data.get("expected_output", "")),
            status=str(data.get("status", "pending")),
            tool_hints=[str(t) for t in data.get("tool_hints", []) if isinstance(t, (str,))],
            depends_on=[str(d) for d in data.get("depends_on", []) if isinstance(d, (str,))],
            run_id=str(data["run_id"]) if data.get("run_id") else None,
            evidence_ids=[str(e) for e in data.get("evidence_ids", []) if isinstance(e, (str,))],
            failure_category=str(data["failure_category"]) if data.get("failure_category") else None,
            retry_count=int(data.get("retry_count", 0)),
        )


@dataclass(slots=True)
class TaskPlan:
    """Linear plan that breaks a user goal into sequential steps."""

    plan_id: str
    goal: str
    task_id: str | None = None
    status: str = "pending"
    steps: list[Step] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "TaskPlan":
        steps_data = data.get("steps", [])
        steps: list[Step] = []
        if isinstance(steps_data, list):
            for item in steps_data:
                if isinstance(item, dict):
                    steps.append(Step.from_dict(item))
        return cls(
            plan_id=str(data.get("plan_id", "")),
            task_id=str(data["task_id"]) if data.get("task_id") else None,
            goal=str(data.get("goal", "")),
            status=str(data.get("status", "pending")),
            steps=steps,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            metadata=dict(data.get("metadata", {})),
        )

    def current_step_index(self) -> int | None:
        """Return the index of the first pending/failed/waiting_approval step."""
        for idx, step in enumerate(self.steps):
            if step.status in {"pending", "failed", "waiting_approval"}:
                return idx
        return None

    def all_completed(self) -> bool:
        return all(s.status == "completed" for s in self.steps)


# ---------------------------------------------------------------------------
# JSON schema validation
# ---------------------------------------------------------------------------

PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["plan_id", "goal", "steps"],
    "properties": {
        "plan_id": {"type": "string"},
        "task_id": {"type": "string"},
        "goal": {"type": "string"},
        "status": {"type": "string", "enum": list(_VALID_PLAN_STATUSES)},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["step_id", "description"],
                "properties": {
                    "step_id": {"type": "string"},
                    "description": {"type": "string"},
                    "expected_output": {"type": "string"},
                    "status": {"type": "string"},
                    "tool_hints": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "run_id": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "failure_category": {"type": "string"},
                    "retry_count": {"type": "integer"},
                },
            },
        },
    },
}


def validate_plan_dict(data: dict[str, object]) -> list[str]:
    """Lightweight structural validation of a plan dict.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        errors.append("plan must be a JSON object")
        return errors

    if not isinstance(data.get("plan_id"), str) or not data["plan_id"]:
        errors.append("plan_id is required and must be a non-empty string")
    if not isinstance(data.get("goal"), str) or not data["goal"]:
        errors.append("goal is required and must be a non-empty string")
    steps = data.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        errors.append("steps must be a non-empty array")
    else:
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"step[{idx}] must be an object")
                continue
            if not isinstance(step.get("step_id"), str) or not step["step_id"]:
                errors.append(f"step[{idx}].step_id is required")
            if not isinstance(step.get("description"), str) or not step["description"]:
                errors.append(f"step[{idx}].description is required")
    return errors


def plan_from_json(raw: str, *, task_id: str | None = None) -> TaskPlan | None:
    """Parse and validate a JSON string into a TaskPlan.

    Returns ``None`` when the input is malformed.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if task_id and not data.get("task_id"):
        data["task_id"] = task_id

    errors = validate_plan_dict(data)
    if errors:
        return None

    plan = TaskPlan.from_dict(data)
    now = datetime.now(timezone.utc).isoformat()
    if not plan.created_at:
        plan.created_at = now
    plan.updated_at = now
    if not plan.plan_id:
        plan.plan_id = f"plan_{uuid4().hex[:12]}"
    return plan


def make_single_step_plan(goal: str, task_id: str | None = None) -> TaskPlan:
    """Create a fallback single-step plan when normal planning fails."""
    now = datetime.now(timezone.utc).isoformat()
    plan_id = f"plan_{uuid4().hex[:12]}"
    return TaskPlan(
        plan_id=plan_id,
        task_id=task_id,
        goal=goal,
        status="pending",
        steps=[
            Step(
                step_id="s1",
                description=goal,
                expected_output="task completed",
                status="pending",
            )
        ],
        created_at=now,
        updated_at=now,
    )
