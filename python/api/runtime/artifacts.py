"""步骤产物登记与最终交付报告（自 server.py 拆分；行为不变）。

本模块包含：
- _register_step_artifacts: 步骤成功后把产物引用登记进 artifacts 表
- _generate_final_report:    计划 Completed 后生成最终交付报告（Designer LLM）

所有符号仍可从 api.server 导入（见 server.py 末尾的 re-export）。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from api.shared import broadcast_log, state

from logging_config import get_logger

logger = get_logger(__name__)


def prepare_final_report(plan, run_id=None, *, database=None, workspace=None):
    """Durable, per-Run snapshot; optional reports never own execution reservations."""
    from tools.workspace import resolve_workspace_root
    from uuid import uuid4
    db = database or state.database
    job_id = f"report-job-{run_id or uuid4()}"
    existing = db.get_artifact(job_id)
    if existing:
        return existing
    metadata = {"status": "pending", "attempts": 0,
                "snapshot": plan.model_dump(mode="json"),
                "workspace": str(workspace or resolve_workspace_root())}
    if not db.create_artifact(job_id, "delivery_report_job", f"job://{job_id}",
                              plan_id=str(plan.plan_id), run_id=run_id, metadata=metadata):
        raise RuntimeError("报告任务登记失败")
    return db.get_artifact(job_id)


async def _run_report_job(job, database, designer, timeout=180):
    import asyncio
    from models import ExecutionPlan
    from tools.workspace import set_workspace_override, reset_workspace_override
    metadata = dict(job["metadata"])
    if metadata.get("status") == "completed":
        return
    artifact_id = job["artifact_id"].replace("report-job-", "report-", 1)
    token = set_workspace_override(metadata["workspace"])
    from utils import usage_tracker
    usage_token = usage_tracker._context.set({"plan_id": job.get("plan_id"), "phase": "report"})
    def persist(status, **extra):
        metadata.update(status=status, updated_at=datetime.now().isoformat(), **extra)
        if not database.update_artifact_metadata(job["artifact_id"], metadata):
            raise RuntimeError("报告任务状态写入失败")
    try:
        # A crash between content commit and job commit must not duplicate the report.
        if database.get_artifact(artifact_id):
            persist("completed", report_artifact_id=artifact_id)
            return
        persist("running", attempts=metadata.get("attempts", 0) + 1, error=None)
        plan = ExecutionPlan(**metadata["snapshot"])
        results = [step for segment in plan.segments for step in segment.steps]
        summary = await asyncio.wait_for(designer.generate_summary(plan, results), timeout=timeout)
        if not summary or not str(summary).strip():
            raise ValueError("模型返回空交付报告")
        database.store_artifact_content(
            artifact_id, str(summary).encode("utf-8"), plan_id=str(plan.plan_id),
            run_id=job.get("run_id"), kind="delivery_report",
            metadata={"source": "final_report", "plan_status": "Completed"},
        )
        persist("completed", report_artifact_id=artifact_id)
        await broadcast_log(f"[REPORT] 最终交付报告已生成: {artifact_id}", "success")
    except asyncio.CancelledError:
        persist("interrupted", error="报告任务在服务关停时取消，将在下次启动恢复")
        raise
    except Exception as exc:
        # Persist a bounded diagnostic, not a credential-bearing upstream body.
        persist("failed", error=f"报告生成失败: {type(exc).__name__}")
        logger.warning("最终交付报告生成失败: {} ({})", job["artifact_id"], type(exc).__name__)
    finally:
        usage_tracker._context.reset(usage_token)
        reset_workspace_override(token)


async def _generate_final_report(plan, run_id=None, *, refresh=True):
    """Fail-open report generation after mandatory Run/ownership finalization."""
    try:
        if state.designer is None or state.llm_client is None:
            return
        db, designer = state.database, state.designer
        if refresh:
            plan = db.get_plan(str(plan.plan_id)) or plan
        job = prepare_final_report(plan, run_id, database=db)
        timeout = float(state.config.get("reports", {}).get("timeout_seconds", 180))
        await _run_report_job(job, db, designer, timeout=timeout)
    except Exception as exc:
        logger.warning("最终交付报告不可用: {}", type(exc).__name__)


def recover_report_jobs():
    """Restart interrupted jobs once, from their frozen Run snapshot, not a newer plan."""
    import asyncio
    if state.designer is None or state.llm_client is None:
        return
    for job in state.database.list_artifacts():
        metadata = job.get("metadata", {})
        if job.get("kind") != "delivery_report_job" or metadata.get("status") not in {"pending", "running", "interrupted"}:
            continue
        run = state.database.get_execution_run(job["run_id"]) if job.get("run_id") else None
        if (job.get("run_id") and (not run or run["status"] != "Completed")) or metadata.get("attempts", 0) >= 2:
            metadata.update(status="failed", error="原Run未完成或报告恢复次数已耗尽")
            state.database.update_artifact_metadata(job["artifact_id"], metadata)
            continue
        task = asyncio.create_task(_run_report_job(
            job, state.database, state.designer,
            float(state.config.get("reports", {}).get("timeout_seconds", 180)),
        ), name=job["artifact_id"])
        state.execution_tasks.add(task)
        def finished(task, tasks=state.execution_tasks):
            tasks.discard(task)
            if not task.cancelled() and task.exception() is not None:
                logger.warning("报告恢复任务异常: {}", type(task.exception()).__name__)
        task.add_done_callback(finished)


def _artifact_result_layers(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """返回可能被 executor 逐层包裹的结果字典（result → output → output.output）。

    普通工具结果是 {path, ...}；agent 结果是
    {success, output: {agent_status, ...}, agent_artifacts: [...]}，executor 再把
    整个结果塞进外层 output。只读最外层会永远登记不到 agent 产物。
    """
    layers: List[Dict[str, Any]] = []
    node: Any = result
    for _ in range(3):
        if not isinstance(node, dict):
            break
        layers.append(node)
        node = node.get("output")
    return layers


def _register_step_artifacts(plan_id: str, step_id: str, result: Dict[str, Any]) -> None:
    """步骤成功后把产物引用登记进 artifacts 表（仅元数据，不拷贝内容）。

    收集来源：工具输出的 path（file_creator/text_editor 类）、agent 的
    file:// 产物引用。登记失败只降级（观察性），绝不影响执行主链路。
    """
    try:
        from api.desktop_artifacts import store_desktop_screenshot
        if store_desktop_screenshot(state, plan_id, step_id, result):
            return
        candidates: List[str] = []
        for source in _artifact_result_layers(result):
            p = source.get("path")
            if isinstance(p, str) and p.strip():
                candidates.append(p.strip())
            for bag in (source.get("agent_artifacts"), source.get("artifacts")):
                for ref in (bag or []):
                    if isinstance(ref, str) and ref.startswith("file://"):
                        candidates.append(ref[len("file://"):])
        seen = set()
        for path_text in candidates:
            normalized = path_text.replace("\\", "/")
            if normalized in seen:
                continue
            seen.add(normalized)
            try:
                p = Path(path_text)
                size = p.stat().st_size if p.exists() else None
            except OSError:
                size = None
            state.database.create_artifact(
                artifact_id=f"{plan_id[:8]}-{step_id[:8]}-{len(seen)}-{normalized.replace('/', '_')[-60:]}",
                kind="file",
                uri=f"file://{normalized}",
                plan_id=plan_id,
                step_id=step_id,
                size_bytes=size,
                metadata={"source": "step_execution"},
            )
    except Exception as register_exc:
        logger.warning("步骤产物登记失败（忽略）: {}", register_exc)

