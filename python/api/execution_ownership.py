"""In-process execution reservations, distinct from persistent Run ownership.

All mutations run synchronously on the application's event-loop thread. Reserve
uses execution_lock for the check-and-set; release has no await so it is also safe
in a Task done callback (including cancellation before the coroutine starts).
This is not a distributed lease or a replacement for database Run fencing.
"""
from uuid import uuid4


class ExecutionBusy(RuntimeError):
    """An existing task still owns the plan or the main workspace."""


def owns_reservation(state, plan_id, token):
    return bool(token) and state.execution_reservations.get(str(plan_id)) == token


async def reserve_execution(state, plan_id, *, isolated):
    plan_id = str(plan_id)
    async with state.execution_lock:
        if plan_id in state.execution_reservations:
            raise ExecutionBusy("该计划仍在排队、执行或收尾，请稍后重试")
        if not isolated and state.current_execution:
            current = str(state.current_execution)
            if (
                current in state.execution_reservations
                or bool(state.current_execution_owner)
                or state.database.get_plan_status(current) == "Running"
            ):
                raise ExecutionBusy(f"已有任务占用主工作区: {current}")
            # Only ownerless, non-running legacy markers may be healed.
            state.current_execution = None
            state.current_execution_owner = None
        token = str(uuid4())
        state.execution_reservations[plan_id] = token
        if not isolated:
            state.current_execution = plan_id
            state.current_execution_owner = {"plan_id": plan_id, "reservation_token": token}
        return token


def release_execution(state, plan_id, token):
    """Release only this reservation; never clear a newer/sibling task's slot."""
    plan_id = str(plan_id)
    if not owns_reservation(state, plan_id, token):
        return
    del state.execution_reservations[plan_id]
    owner = state.current_execution_owner or {}
    if (state.current_execution == plan_id and owner.get("plan_id") == plan_id
            and owner.get("reservation_token") == token):
        state.current_execution = None
        state.current_execution_owner = None
