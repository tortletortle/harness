# -*- coding: utf-8 -*-
"""一次性迁移脚本：从 server.py 删除已拆出的段落，装配 routers，加兼容 re-export。运行一次后删除。"""
from pathlib import Path

p = Path("python/api/server.py")
text = p.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)


def find_line(anchor, start=0):
    for i in range(start, len(lines)):
        if anchor in lines[i]:
            return i
    raise SystemExit(f"anchor not found: {anchor!r}")


def cut(start_anchor, end_anchor):
    s = find_line(start_anchor)
    e = find_line(end_anchor, s + 1)
    while e > 0 and not lines[e].lstrip().startswith(("@app.", "class ", "def ", "async def")):
        e -= 1
        if lines[e].lstrip().startswith(("async def", "def ")):
            break
    del lines[s:e]
    return s


cut('class DesignRequest', 'class ExecutionRequest')
cut('class ConfigUpdateRequest', '# ===== 全局状态 =====')
cut('class AppState', '# ===== 启动和关闭（lifespan） =====')
cut('# ===== WebSocket 日志广播 =====', '# ===== API 路由 =====')
cut('def _compute_pause_checkpoint', '@app.post("/api/plan/{plan_id}/resume"')
cut('@app.delete("/api/design/{design_id}"', '@app.post("/api/restart"')
cut('@app.get("/")', '@app.post("/api/design"')
cut('@app.post("/api/design"', '@app.get("/api/plans"')
cut('@app.get("/api/plans"', '@app.get("/api/agent-sessions"')
cut('@app.get("/api/agent-sessions"', '@app.post("/api/plan/{plan_id}/execute"')
cut('@app.post("/api/plan/{plan_id}/execute"', 'class ApprovalDecisionRequest')
cut('class ApprovalDecisionRequest', 'def _is_retryable_exit_code_acceptance')
cut('@app.post("/api/plan/{plan_id}/archive"', '@app.post("/api/quick-task"')
cut('@app.post("/api/quick-task"', '@app.post("/api/plan/{plan_id}/replan"')
cut('@app.post("/api/plan/{plan_id}/replan"', '@app.get("/api/audit-logs"')
cut('@app.get("/api/audit-logs"', 'def start_server(')

new_text = "".join(lines)

shared_import = """from api.shared import (  # 拆分：状态/广播/助手（行为不变，符号保留在 server 命名空间）
    AppState, state, _require_write, _deep_merge_dict, _load_models_config,
    _send_to_clients, broadcast_log, broadcast_plan_update,
    broadcast_approval_request, broadcast_approval_resolved,
    _format_breakdown, format_design_for_frontend, format_plan_for_frontend,
    _compute_pause_checkpoint,
)
from api.routes import (
    design_routes, plan_routes, approval_routes, artifact_routes,
    agent_session_routes, worktree_routes, quick_task_routes, replan_routes,
    misc_routes,
)

"""
anchor = "from api.auth import load_token, require_token, verify_token\n"
assert anchor in new_text
new_text = new_text.replace(anchor, anchor + shared_import, 1)

asm_block = """# ===== 路由装配（按领域拆分至 api/routes/）=====
app.include_router(design_routes.router)
app.include_router(plan_routes.router)
app.include_router(approval_routes.router)
app.include_router(artifact_routes.router)
app.include_router(agent_session_routes.router)
app.include_router(worktree_routes.router)
app.include_router(quick_task_routes.router)
app.include_router(replan_routes.router)
app.include_router(misc_routes.router)


"""
idx = new_text.find("@app.exception_handler(DatabaseError)")
assert idx >= 0
new_text = new_text[:idx] + asm_block + new_text[idx:]

reexport = """

# ===== 兼容 re-export（测试与外部调用方经 server 命名空间访问路由函数）=====
from api.routes.plan_routes import (  # noqa: E402,F401
    get_plans, get_plan, get_plan_runs, get_run, get_run_attempts,
    execute_plan, pause_plan, resume_plan, skip_step,
    archive_plan, rename_plan, delete_plan, rollback_plan,
)
from api.routes.design_routes import (  # noqa: E402,F401
    create_design, get_designs, get_design, approve_design, delete_design,
)
from api.routes.approval_routes import (  # noqa: E402,F401
    list_pending_approvals, approve_tool_call, reject_tool_call,
)
from api.routes.worktree_routes import (  # noqa: E402,F401
    create_plan_worktree, get_plan_worktree, merge_plan_worktree, discard_plan_worktree,
)
from api.routes.quick_task_routes import quick_task  # noqa: E402,F401
from api.routes.replan_routes import replan_plan  # noqa: E402,F401
from api.routes.artifact_routes import (  # noqa: E402,F401
    list_artifacts, get_artifact, get_artifact_content, create_artifact,
)
from api.routes.agent_session_routes import (  # noqa: E402,F401
    list_agent_sessions, get_agent_session_turns,
)
from api.routes.misc_routes import (  # noqa: E402,F401
    root, get_status, get_tools, api_chat, api_chat_sessions, api_skills,
    notify_test, get_capabilities, get_config, update_config,
    get_audit_logs, get_usage, restart_service, websocket_logs,
)


def start_server("""
new_text = new_text.replace("\n\ndef start_server(", reexport, 1)

p.write_text(new_text, encoding="utf-8")
print("server.py slimmed:", len(new_text.splitlines()), "lines")
