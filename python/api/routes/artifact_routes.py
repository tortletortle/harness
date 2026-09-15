"""Artifact 路由（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from api.auth import require_token
from api.shared import state

router = APIRouter()


@router.get("/api/artifacts", dependencies=[Depends(require_token)])
async def list_artifacts(plan_id: Optional[str] = None, step_id: Optional[str] = None):
    """产物元数据列表（内容通过 /api/artifacts/{id}/content 拉取）。"""
    return {"success": True, "artifacts": state.database.list_artifacts(plan_id=plan_id, step_id=step_id)}


@router.get("/api/artifacts/{artifact_id}", dependencies=[Depends(require_token)])
async def get_artifact(artifact_id: str):
    artifact = state.database.get_artifact(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="产物不存在")
    return {"success": True, "artifact": artifact}


@router.get("/api/artifacts/{artifact_id}/content", dependencies=[Depends(require_token)])
async def get_artifact_content(artifact_id: str):
    """拉取产物内容（未入库的外部引用返回 404 与说明）。"""
    content = state.database.read_artifact_content(artifact_id)
    if content is None:
        artifact = state.database.get_artifact(artifact_id)
        if not artifact:
            raise HTTPException(status_code=404, detail="产物不存在")
        raise HTTPException(status_code=404, detail="产物内容未入库（仅登记了外部引用）")
    import hashlib
    artifact = state.database.get_artifact(artifact_id)
    expected = (artifact or {}).get("content_hash")
    if expected and expected != "sha256:" + hashlib.sha256(content).hexdigest():
        raise HTTPException(status_code=409, detail="产物内容校验失败")
    from api.desktop_artifacts import is_bounded_png
    is_image = is_bounded_png(content)
    if (artifact or {}).get("kind") == "desktop_screenshot" and (not is_image or not expected):
        raise HTTPException(status_code=415, detail="无效或超限的截图产物")
    return Response(content=content, media_type="image/png" if is_image else "application/octet-stream",
                    headers={"X-Artifact-Id": artifact_id, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/api/artifacts", dependencies=[Depends(require_token)])
async def create_artifact(request: Dict[str, Any]):
    """登记产物：支持纯引用（uri）或直接携带内容入库（content 文本，SHA-256 落盘）。"""
    body = request or {}
    kind = str(body.get("kind") or "file")
    artifact_id = str(body.get("artifact_id") or uuid4())
    content = body.get("content")
    if content is not None:
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            raise HTTPException(status_code=400, detail="content 必须是文本")
        record = state.database.store_artifact_content(
            artifact_id, content_bytes, plan_id=body.get("plan_id"),
            step_id=body.get("step_id"), kind=kind, metadata=body.get("metadata"),
        )
        return {"success": True, "artifact": record}
    uri = str(body.get("uri") or "").strip()
    if not uri:
        raise HTTPException(status_code=400, detail="缺少 uri 或 content")
    state.database.create_artifact(
        artifact_id=artifact_id, kind=kind, uri=uri,
        plan_id=body.get("plan_id"), step_id=body.get("step_id"),
        content_hash=body.get("content_hash"), metadata=body.get("metadata"),
    )
    return {"success": True, "artifact": state.database.get_artifact(artifact_id)}



