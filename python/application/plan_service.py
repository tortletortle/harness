"""Application-level plan operations, introduced without changing API/schema."""

from typing import Any, Dict, List, Optional, Tuple

from python.repositories.plan_repository import PlanRepository


class PlanService:
    """Read and validate plan operations before API routes perform side effects."""

    def __init__(self, repository: PlanRepository) -> None:
        self.repository = repository

    def get_plan(self, plan_id: str):
        return self.repository.get(plan_id)

    def get_status_summary(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self.repository.status_summary(plan_id)

    def pause_checkpoint(self, plan_id: str, branch_version: int) -> Tuple[Optional[str], Optional[str]]:
        rows = self.repository.active_steps(plan_id, branch_version)
        paused_at = next((str(row["step_id"]) for row in rows if row["status"] == "Running"), None)
        resume_from = next(
            (str(row["step_id"]) for row in rows if row["status"] in {"Pending", "Retry"}),
            None,
        )
        return paused_at, resume_from

    def effective_step_status(self, plan_id: str, step_id: str, branch_version: int) -> Optional[str]:
        return self.repository.database.get_effective_step_status(plan_id, step_id, branch_version)
