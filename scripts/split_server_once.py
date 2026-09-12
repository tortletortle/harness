# -*- coding: utf-8 -*-
"""一次性迁移脚本：把 server.py 的路由段按锚点抽取到 api/routes/ 包。

运行一次后即删除。块边界用内容锚定位，不依赖行号。
"""
from pathlib import Path

SERVER = Path("python/api/server.py")
ROUTES = Path("python/api/routes")

text = SERVER.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)


def find_line(anchor: str, start: int = 0) -> int:
    for i in range(start, len(lines)):
        if anchor in lines[i]:
            return i
    raise SystemExit(f"anchor not found: {anchor!r}")


def block(start_anchor: str, end_anchor: str) -> str:
    s = find_line(start_anchor)
    # 回退到装饰器行（@app.xxx 在 async def 上一行或两行）
    while s > 0 and not lines[s].lstrip().startswith("@app."):
        s -= 1
        if lines[s].lstrip().startswith(("async def", "def ", "class ")):
            break
    e = find_line(end_anchor, s + 1)
    # end_anchor 回退到它的装饰器行
    while e > 0 and not lines[e].lstrip().startswith(("@app.", "class ")):
        e -= 1
        if lines[e].lstrip().startswith(("async def", "def ")):
            break
    return "".join(lines[s:e])


def segment(start_anchor: str, end_anchor: str) -> str:
    """按锚点直接切（不回退装饰器），含头不含尾。"""
    s = find_line(start_anchor)
    e = find_line(end_anchor, s + 1)
    return "".join(lines[s:e])


ROUTES.mkdir(exist_ok=True)

HEADER = '''"""{title}（自 server.py 拆分；行为不变）。"""
from __future__ import annotations

'''

# ---- design_routes ----
design_block = (
    block('@app.post("/api/design"', '@app.get("/api/plans"')
    + segment('@app.delete("/api/design/{design_id}"', '@app.post("/api/restart"')
)
design_src = (
    HEADER.format(title="设计书路由")
    + "import json\nfrom typing import Any, Dict, Optional\nfrom uuid import uuid4\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\nfrom pydantic import BaseModel\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state, broadcast_plan_update, broadcast_log, format_design_for_frontend, _require_write\n"
    + "from designer import GenerationError\n\n"
    + "router = APIRouter()\n\n\n"
    + 'class DesignRequest(BaseModel):\n    """生成设计书请求"""\n    user_request: str\n\n'
    + 'class ApprovalRequest(BaseModel):\n    """审批设计书请求"""\n    design_id: str\n    approved: bool\n    feedback: Optional[str] = None\n\n\n'
    + design_block.replace("@app.", "@router.")
)
(ROUTES / "design_routes.py").write_text(design_src, encoding="utf-8")

# ---- plan_routes ----
plan_block = (
    segment('@app.get("/api/plans"', '@app.get("/api/agent-sessions"')
    + segment('@app.post("/api/plan/{plan_id}/execute"', 'class ApprovalDecisionRequest')
    + segment('@app.post("/api/plan/{plan_id}/archive"', '@app.post("/api/quick-task"')
)
plan_src = (
    HEADER.format(title="计划路由（列表/详情/执行控制/生命周期/回滚/runs）")
    + "import asyncio\nimport json\nfrom datetime import datetime\nfrom typing import Any, Dict, Optional\nfrom uuid import UUID, uuid4\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import (\n    state, broadcast_plan_update, broadcast_log, format_plan_for_frontend,\n    _require_write, _compute_pause_checkpoint,\n)\n"
    + "from models import PlanStatus, StepStatus, RunStatus, AttemptStatus\n\n"
    + "router = APIRouter()\n\n\n"
    + plan_block.replace("@app.", "@router.")
)
(ROUTES / "plan_routes.py").write_text(plan_src, encoding="utf-8")

# ---- approval_routes ----
approval_block = segment('class ApprovalDecisionRequest', 'def _is_retryable_exit_code_acceptance')
approval_src = (
    HEADER.format(title="工具级审批路由")
    + "from typing import Any, Dict, Optional\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\nfrom pydantic import BaseModel\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state, broadcast_approval_resolved, broadcast_log\n\n"
    + "router = APIRouter()\n\n\n"
    + approval_block.replace("@app.", "@router.")
)
(ROUTES / "approval_routes.py").write_text(approval_src, encoding="utf-8")

# ---- artifact_routes ----
artifact_block = segment('@app.get("/api/artifacts"', '@app.post("/api/plan/{plan_id}/execute"')
artifact_src = (
    HEADER.format(title="Artifact 路由")
    + "from typing import Any, Dict, Optional\nfrom uuid import uuid4\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\nfrom fastapi.responses import Response\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state\n\n"
    + "router = APIRouter()\n\n\n"
    + artifact_block.replace("@app.", "@router.")
)
(ROUTES / "artifact_routes.py").write_text(artifact_src, encoding="utf-8")

# ---- agent_session_routes ----
agent_block = segment('@app.get("/api/agent-sessions"', '@app.get("/api/artifacts"')
agent_src = (
    HEADER.format(title="Agent 运行记录路由")
    + "from typing import Optional\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state\n\n"
    + "router = APIRouter()\n\n\n"
    + agent_block.replace("@app.", "@router.")
)
(ROUTES / "agent_session_routes.py").write_text(agent_src, encoding="utf-8")

# ---- worktree_routes ----
worktree_block = segment('@app.post("/api/plan/{plan_id}/worktree"', '@app.post("/api/quick-task"')
worktree_src = (
    HEADER.format(title="Worktree 隔离路由")
    + "from typing import Any, Dict, Optional\nfrom uuid import uuid4\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\nfrom fastapi.responses import JSONResponse\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state, broadcast_log, _require_write\n\n"
    + "router = APIRouter()\n\n\n"
    + worktree_block.replace("@app.", "@router.")
)
(ROUTES / "worktree_routes.py").write_text(worktree_src, encoding="utf-8")

# ---- quick_task_routes ----
quick_block = segment('@app.post("/api/quick-task"', '@app.post("/api/plan/{plan_id}/replan"')
quick_src = (
    HEADER.format(title="任务模式识别入口路由")
    + "import asyncio\nfrom typing import Any, Dict\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state\n"
    + "from authorization import GrantLimits\n\n"
    + "router = APIRouter()\n\n\n"
    + quick_block.replace("@app.", "@router.")
)
(ROUTES / "quick_task_routes.py").write_text(quick_src, encoding="utf-8")

# ---- replan_routes ----
replan_block = segment('@app.post("/api/plan/{plan_id}/replan"', '@app.get("/api/audit-logs"')
replan_src = (
    HEADER.format(title="动态重规划路由")
    + "import json\nfrom typing import Any, Dict, Optional\nfrom uuid import uuid4\n\n"
    + "from fastapi import APIRouter, Depends, HTTPException\nfrom fastapi.responses import JSONResponse\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state, broadcast_plan_update, broadcast_log, _require_write\n"
    + "from designer import GenerationError\n"
    + "from models import PlanStatus, StepStatus\n\n"
    + "router = APIRouter()\n\n\n"
    + replan_block.replace("@app.", "@router.")
)
(ROUTES / "replan_routes.py").write_text(replan_src, encoding="utf-8")

# ---- misc_routes ----
misc_block = (
    segment('@app.get("/")', '@app.post("/api/design"')
    + segment('@app.get("/api/audit-logs"', 'def start_server(')
)
misc_src = (
    HEADER.format(title="系统/生态路由（状态、工具、chat、skills、通知、能力、配置、审计、用量、重启、WebSocket）")
    + "import asyncio\nimport json\nimport os\nfrom pathlib import Path\nfrom typing import Any, Dict, Optional\n\n"
    + "import yaml\nfrom fastapi import APIRouter, Depends, HTTPException, Body, WebSocket, WebSocketDisconnect\n\n"
    + "from api.auth import require_token\n"
    + "from api.shared import state, broadcast_log, _load_models_config, _deep_merge_dict\n"
    + "from database import DatabaseError\n"
    + "from designer import GenerationError\n\n"
    + "router = APIRouter()\n\n\n"
    + misc_block.replace("@app.", "@router.")
)
(ROUTES / "misc_routes.py").write_text(misc_src, encoding="utf-8")

print("routes extracted:")
for p in sorted(ROUTES.glob("*.py")):
    print(" ", p.name, len(p.read_text(encoding="utf-8").splitlines()), "lines")
