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
from weakref import WeakKeyDictionary

from api.shared import broadcast_log, state

from domain.report_truth import ReportTruthError, capture_facts, unknown_facts, report_facts, render_report
from logging_config import get_logger

logger = get_logger(__name__)

# One backend/event loop may have several callers for the same durable report.
# Keep the actual worker separate from callers; a cancelled joiner cannot cancel it.
# This is deliberately NOT advertised as a distributed/multi-process lock.
_report_workers = WeakKeyDictionary()


def report_options(config):
    """Reject non-finite/unbounded values instead of hanging shutdown or leaking coroutines."""
    import math
    raw = config.get("reports", {}) if isinstance(config, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for key, default, lower, upper in (
        ("timeout_seconds", 180.0, 0.001, 3600),
        ("shutdown_grace_seconds", 15.0, 0, 300),
    ):
        value = raw.get(key, default)
        try:
            if isinstance(value, bool):
                raise ValueError("boolean duration")
            value = float(value)
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("invalid duration")
        except (TypeError, ValueError, OverflowError):
            logger.warning("报告配置 {} 无效，使用安全默认值 {}", key, default)
            value = default
        result[key] = value
    return result


def _attempt_count(metadata):
    value = metadata.get("attempts", 0)
    if type(value) is not int or value < 0:
        raise ValueError("invalid report attempts")
    return value


def prepare_final_report(plan, run_id=None, *, database=None, workspace=None, revision_id=None, interventions=None):
    """Durable, per-Run snapshot; optional reports never own execution reservations."""
    from tools.workspace import resolve_workspace_root
    from uuid import UUID, uuid4
    db = database or state.database
    job_id = f"report-job-{run_id or uuid4()}"
    if revision_id is not None:
        if not run_id:
            raise ValueError("Report revision requires an existing Run")
        job_id += "-revision-" + str(UUID(str(revision_id)))
    existing = db.get_artifact(job_id)
    if existing:
        return existing
    metadata = {"status": "pending", "attempts": 0,
                "snapshot": plan.model_dump(mode="json"),
                "execution_facts": capture_facts(db, plan, run_id, interventions),
                "workspace": str(workspace or resolve_workspace_root()),
                "report_workspace": str(resolve_workspace_root())}
    if not db.create_artifact(job_id, "delivery_report_job", f"job://{job_id}",
                              plan_id=str(plan.plan_id), run_id=run_id, metadata=metadata):
        raise RuntimeError("报告任务登记失败")
    return db.get_artifact(job_id)


async def _run_report_job(job, database, designer, timeout=180):
    """Single-flight per DB/job in this event loop; joiners share the same result."""
    import asyncio
    loop = asyncio.get_running_loop()
    workers = _report_workers.setdefault(loop, {})
    key = (str(Path(database.db_path).resolve()), job["artifact_id"])
    existing = workers.get(key)
    if existing is not None and not existing.done():
        await asyncio.shield(existing)
        return
    worker = asyncio.create_task(_execute_report_job(job, database, designer, timeout),
                                 name="report-worker-" + job["artifact_id"])
    workers[key] = worker
    try:
        # Owner cancellation propagates to the worker; duplicate callers use shield.
        await worker
    finally:
        if workers.get(key) is worker:
            workers.pop(key, None)
        if not workers:
            _report_workers.pop(loop, None)


async def _execute_report_job(job, database, designer, timeout):
    import asyncio
    from models import ExecutionPlan
    from tools.workspace import set_workspace_override, reset_workspace_override, resolve_workspace_root
    from utils import usage_tracker
    # Callers may hold an old pending snapshot after a previous invocation completed.
    current = database.get_artifact(job["artifact_id"])
    if not current:
        logger.warning("报告任务已不存在: {}", job["artifact_id"])
        return
    job = current
    metadata = dict(job.get("metadata") or {})
    artifact_id = job["artifact_id"].replace("report-job-", "report-", 1)
    token = set_workspace_override(metadata.get("report_workspace") or resolve_workspace_root())
    usage_token = usage_tracker._context.set({"plan_id": job.get("plan_id"), "phase": "report"})

    facts_token = None

    def persist(status, **extra):
        updated = {**metadata, "status": status, "updated_at": datetime.now().isoformat(), **extra}
        if not database.update_artifact_metadata(job["artifact_id"], updated):
            raise RuntimeError("报告任务状态写入失败")
        metadata.update(updated)

    def note_failure(status, error):
        try:
            persist(status, error=error)
        except Exception as exc:
            # Storage can be unavailable; do not replace cancellation/the original error.
            # The previous durable running record remains eligible for restart recovery.
            logger.warning("报告任务状态无法持久化: {} ({})", job["artifact_id"], type(exc).__name__)

    try:
        # Metadata alone cannot prove successful delivery. Never overwrite a damaged
        # existing artifact: keep it for diagnosis, and mark the job failed instead.
        existing = database.get_artifact(artifact_id)
        if existing:
            if existing.get("kind") != "delivery_report" or database.verify_artifact_hash(artifact_id) is not True:
                raise ValueError("报告内容缺失或哈希校验失败")
            persist("completed", report_artifact_id=artifact_id, error=None)
            return
        if metadata.get("status") == "completed":
            raise ValueError("已完成报告缺少交付产物")
        if metadata.get("status") == "failed":
            return
        attempts = _attempt_count(metadata)
        if attempts >= 2:
            raise ValueError("报告恢复次数已耗尽")
        plan = ExecutionPlan(**metadata["snapshot"])
        persist("running", attempts=attempts + 1, error=None)
        results = [step for segment in plan.segments for step in segment.steps]
        timeout = report_options({"reports": {"timeout_seconds": timeout}})["timeout_seconds"]
        facts = metadata.get("execution_facts") or unknown_facts(plan)
        facts_token = report_facts.set(facts)
        started = asyncio.get_running_loop().time()
        summary = await asyncio.wait_for(designer.generate_summary(plan, results), timeout=timeout)
        if not summary or not str(summary).strip():
            raise ValueError("模型返回空交付报告")
        narrative_generation_attempts = 1
        try:
            report_text = render_report(summary, facts)
            narrative_status = "validated"
        except ReportTruthError:
            # Do not send rejected text back to the model or publish it. Optional
            # designers may offer one safe rewrite using only frozen fact fields.
            report_text = None
            retry = getattr(designer, "generate_summary_retry", None)
            remaining = timeout - (asyncio.get_running_loop().time() - started)
            if callable(retry) and remaining > 0.01:
                narrative_generation_attempts = 2
                try:
                    revised = await asyncio.wait_for(retry(plan, results), timeout=remaining)
                    if revised and str(revised).strip():
                        report_text = render_report(revised, facts)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("报告事实校验后的重写未通过: {} ({})",
                                   job["artifact_id"], type(exc).__name__)
            if report_text is None:
                # Never publish either rejected model output. The independently
                # frozen facts remain useful, with an explicit downgrade label.
                narrative_status = "facts_only"
                report_text = render_report(
                    "模型补充说明未被采用：自动生成文字未通过事实校验。"
                    "本报告仅保留系统核验事实，不包含模型结论。", facts,
                )
                logger.warning("报告补充说明未通过事实校验，仅保存核验事实: {}", job["artifact_id"])
            else:
                narrative_status = "validated"
        database.store_artifact_content(
            artifact_id, report_text.encode("utf-8"), plan_id=str(plan.plan_id),
            run_id=job.get("run_id"), kind="delivery_report",
            metadata={"source": "final_report", "plan_status": plan.status.value,
                      "truth_schema_version": 1, "narrative_status": narrative_status,
                      "narrative_generation_attempts": narrative_generation_attempts},
        )
        persist("completed", report_artifact_id=artifact_id, narrative_status=narrative_status,
                narrative_generation_attempts=narrative_generation_attempts)
    except asyncio.CancelledError:
        note_failure("interrupted", "报告任务已取消，将在下次启动恢复")
        raise
    except Exception as exc:
        note_failure("failed", f"报告生成或完整性验证失败: {type(exc).__name__}")
        logger.warning("最终交付报告失败: {} ({})", job["artifact_id"], type(exc).__name__)
        return
    finally:
        if facts_token is not None:
            report_facts.reset(facts_token)
        usage_tracker._context.reset(usage_token)
        reset_workspace_override(token)

    # Notification is an observation of an already committed result. A broken or
    # cancelled websocket must never change completed -> failed/interrupted.
    try:
        await broadcast_log(f"[REPORT] 最终交付报告已生成: {artifact_id}", "success")
    except Exception as exc:
        logger.warning("报告已保存，但通知失败: {} ({})", artifact_id, type(exc).__name__)


async def _generate_final_report(plan, run_id=None, *, refresh=True):
    """Fail-open report generation after mandatory Run/ownership finalization."""
    try:
        if state.designer is None or state.llm_client is None:
            return
        db, designer = state.database, state.designer
        if refresh:
            plan = db.get_plan(str(plan.plan_id)) or plan
        job = prepare_final_report(plan, run_id, database=db)
        timeout = report_options(state.config)["timeout_seconds"]
        await _run_report_job(job, db, designer, timeout=timeout)
    except Exception as exc:
        logger.warning("最终交付报告不可用: {}", type(exc).__name__)


def recover_report_jobs():
    """Restart interrupted jobs once, from their frozen Run snapshot, not a newer plan."""
    import asyncio
    if state.designer is None or state.llm_client is None:
        return
    db, designer = state.database, state.designer
    timeout = report_options(state.config)["timeout_seconds"]
    try:
        jobs = db.list_artifacts()
    except Exception as exc:
        logger.warning("报告恢复列表读取失败: {}", type(exc).__name__)
        return
    for job in jobs:
        if job.get("kind") != "delivery_report_job":
            continue
        metadata = job.get("metadata")
        try:
            if not isinstance(metadata, dict):
                raise ValueError("invalid report metadata")
            if metadata.get("status") in {"completed", "failed"}:
                continue
            if metadata.get("status") not in {"pending", "running", "interrupted"}:
                raise ValueError("invalid report status")
            run = db.get_execution_run(job["run_id"]) if job.get("run_id") else None
            if (job.get("run_id") and (not run or run["status"] != "Completed")) or _attempt_count(metadata) >= 2:
                raise ValueError("原Run未完成或报告恢复次数已耗尽")
            task = asyncio.create_task(_run_report_job(job, db, designer, timeout), name=job["artifact_id"])
            state.execution_tasks.add(task)
            def finished(task, tasks=state.execution_tasks):
                tasks.discard(task)
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("报告恢复任务异常: {}", type(task.exception()).__name__)
            task.add_done_callback(finished)
        except Exception as exc:
            logger.warning("单条报告恢复记录不可用: {} ({})", job.get("artifact_id"), type(exc).__name__)
            failed = dict(metadata) if isinstance(metadata, dict) else {}
            failed.update(status="failed", error="报告恢复记录损坏、Run未完成或尝试次数耗尽")
            try:
                if not db.update_artifact_metadata(job["artifact_id"], failed):
                    logger.warning("报告恢复记录已不存在: {}", job.get("artifact_id"))
            except Exception as persist_exc:
                logger.warning("损坏报告状态写入失败: {}", type(persist_exc).__name__)


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

