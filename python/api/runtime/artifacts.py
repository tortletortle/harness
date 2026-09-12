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


async def _generate_final_report(plan, run_id: str = None) -> None:
    """计划 Completed 后生成最终交付报告（Designer LLM），落 artifacts 表。

    fail-open：Designer 缺失或 LLM 失败只记日志，绝不影响收尾状态。
    """
    try:
        if state.designer is None or state.llm_client is None:
            return
        results = [step for segment in plan.segments for step in segment.steps]
        summary = await state.designer.generate_summary(plan, results)
        if not summary or not str(summary).strip():
            return
        record = state.database.store_artifact_content(
            artifact_id=f"report-{str(plan.plan_id)[:8]}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            content=str(summary).encode("utf-8"),
            plan_id=str(plan.plan_id),
            run_id=run_id,
            kind="delivery_report",
            metadata={"source": "final_report", "plan_status": "Completed"},
        )
        await broadcast_log(f"[REPORT] 最终交付报告已生成: {record['artifact_id']}", "success")
    except Exception as report_exc:
        logger.warning("最终交付报告生成失败（忽略）: {}", report_exc)


def _register_step_artifacts(plan_id: str, step_id: str, result: Dict[str, Any]) -> None:
    """步骤成功后把产物引用登记进 artifacts 表（仅元数据，不拷贝内容）。

    收集来源：工具输出的 path（file_creator/text_editor 类）、agent 的
    file:// 产物引用。登记失败只降级（观察性），绝不影响执行主链路。
    """
    try:
        candidates: List[str] = []
        output = result.get("output")
        if isinstance(output, dict):
            p = output.get("path")
            if isinstance(p, str) and p.strip():
                candidates.append(p.strip())
        for ref in (result.get("agent_artifacts") or []):
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

