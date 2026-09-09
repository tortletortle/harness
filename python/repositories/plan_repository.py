"""Compatibility repository for plan persistence; schema remains owned by Database."""

from typing import Any, Dict, List, Optional

from python.database import Database
from python.models import ExecutionPlan


class PlanRepository:
    """Small plan-focused facade over the existing Database implementation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get(self, plan_id: str) -> Optional[ExecutionPlan]:
        return self.database.get_plan(plan_id)

    def status(self, plan_id: str) -> Optional[str]:
        return self.database.get_plan_status(plan_id)

    def status_summary(self, plan_id: str) -> Optional[Dict[str, Any]]:
        return self.database.get_plan_status_summary(plan_id)

    def steps(self, plan_id: str) -> List[Dict[str, Any]]:
        return self.database.get_steps(plan_id)

    def active_steps(self, plan_id: str, branch_version: int) -> List[Dict[str, Any]]:
        return self.database.get_active_steps_snapshot(plan_id, branch_version)

    def mark_step_tree_skipped(self, plan_id: str, step_id: str) -> bool:
        return self.database.mark_step_tree_skipped(plan_id, step_id)
