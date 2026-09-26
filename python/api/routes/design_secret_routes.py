"""设计书审批后的项目密钥录入路由（外部工具仓 design_secrets 的宿主侧接线）。

对 harness_tools 的导入全部走 api.secrets_hook 的守卫式按需加载，
宿主在工具仓缺席时照常工作（路由返回 available=False，不提示、不报错）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import require_token
from api.shared import state
from logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter()


class SecretsPlanRequest(BaseModel):
    """设计书审批时附带的可选密钥声明。

    declaration 可以是 fenced block 文本（```json {"required_secrets": [...]}```）
    或密钥名数组的 JSON 文本；留空则扫设计书自身文本。
    """
    declaration: Optional[str] = None
    names: Optional[List[str]] = None


@router.get("/api/secrets", dependencies=[Depends(require_token)])
async def list_project_secrets():
    """列出已写入的项目密钥名（不含任何值）。"""
    from api.secrets_hook import list_project_secrets as _hook

    result = _hook()
    return {"success": True, **result}


@router.post("/api/design/{design_id}/secrets/plan", dependencies=[Depends(require_token)])
async def design_secrets_plan(design_id: str, request: Optional[SecretsPlanRequest] = None):
    """审批通过后调用：返回该设计还需要用户填哪些密钥，以及一次性录入入口。

    只在有"声明了但还没写"的密钥时打开本地录入界面（prompt_shown=True）；
    没有待写项时不弹，也不往任何响应里带密钥值。
    """
    try:
        design = state.database.get_design(design_id)
        if not design:
            raise HTTPException(status_code=404, detail="设计书不存在")
        from api.secrets_hook import build_secrets_prompt

        body = request or SecretsPlanRequest()
        declaration = body.declaration
        if not declaration and body.names:
            import json as _json

            declaration = _json.dumps({"required_secrets": body.names}, ensure_ascii=False)
        result = build_secrets_prompt(design, declaration=declaration)
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("查询设计书密钥计划失败: {}", e)
        raise HTTPException(status_code=500, detail="查询设计书密钥计划失败")
