"""baseline: 固化 2026-09-19 现有 schema（12 表 + 10 索引）

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-19

本迁移与 python/database.py::_initialize_tables 在此日期的行为完全一致
（含历史 ALTER TABLE 增列合并后的最终形态，核对源 out/schema_baseline.sql）。

- 新库：alembic upgrade head 可直接建出运行时同款 schema。
- 既有库（表已由运行时创建）：执行 alembic stamp 0001_baseline 只登记版本，
  不重复建表。所有 DDL 都带 IF NOT EXISTS 防御，误跑也不会破坏数据。
- 运行时 _initialize_tables 暂时保留（幂等），第二阶段再移除建表职责。
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = [
    """CREATE TABLE IF NOT EXISTS designs (
        design_id TEXT PRIMARY KEY,
        user_request TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        part1_design TEXT NOT NULL,
        part2_tool_assessment TEXT NOT NULL,
        user_approval INTEGER,
        user_comments TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS plans (
        plan_id TEXT PRIMARY KEY,
        design_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL,
        segments TEXT NOT NULL,
        implementation_list TEXT NOT NULL,
        dynamic_insertions TEXT NOT NULL,
        current_branch INTEGER NOT NULL DEFAULT 1,
        resume_from_step_id TEXT,
        paused_at_step_id TEXT,
        archived INTEGER NOT NULL DEFAULT 0,
        display_name TEXT,
        FOREIGN KEY (design_id) REFERENCES designs(design_id)
    )""",
    """CREATE TABLE IF NOT EXISTS steps (
        step_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        step_order INTEGER NOT NULL,
        description TEXT NOT NULL,
        tool TEXT NOT NULL,
        inputs TEXT NOT NULL,
        check_criteria TEXT NOT NULL,
        acceptance_json TEXT,
        outputs TEXT,
        status TEXT NOT NULL,
        executed_at TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        depends_on TEXT NOT NULL DEFAULT '[]',
        parent_step_id TEXT,
        branch_version INTEGER NOT NULL DEFAULT 1,
        state_snapshot TEXT,
        compressed_context TEXT,
        duration_ms INTEGER,
        failure_reason TEXT,
        is_obsolete INTEGER NOT NULL DEFAULT 0,
        max_retries INTEGER NOT NULL DEFAULT 2,
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
    )""",
    """CREATE TABLE IF NOT EXISTS execution_runs (
        run_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        status TEXT NOT NULL,
        trigger TEXT NOT NULL,
        branch_version INTEGER NOT NULL DEFAULT 1,
        started_at TEXT,
        finished_at TEXT,
        heartbeat_at TEXT,
        owner_token TEXT NOT NULL,
        failure_reason TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
    )""",
    """CREATE TABLE IF NOT EXISTS execution_attempts (
        attempt_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        duration_ms INTEGER,
        retryable INTEGER,
        failure_reason TEXT,
        outputs TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES execution_runs(run_id),
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id),
        FOREIGN KEY (step_id) REFERENCES steps(step_id)
    )""",
    """CREATE TABLE IF NOT EXISTS step_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        step_id TEXT NOT NULL,
        plan_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        event_data TEXT,
        branch_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        FOREIGN KEY (step_id) REFERENCES steps(step_id),
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
    )""",
    """CREATE TABLE IF NOT EXISTS logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL,
        context TEXT,
        service TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS audit_logs (
        audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        plan_id TEXT,
        step_id TEXT,
        tool_id TEXT NOT NULL,
        instruction TEXT NOT NULL,
        inputs TEXT,
        success INTEGER,
        blocked INTEGER NOT NULL DEFAULT 0,
        block_reason TEXT,
        actor TEXT,
        grant_id TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        plan_id TEXT,
        run_id TEXT,
        step_id TEXT,
        kind TEXT NOT NULL,
        uri TEXT NOT NULL,
        content_hash TEXT,
        size_bytes INTEGER,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
    )""",
    """CREATE TABLE IF NOT EXISTS worktrees (
        worktree_id TEXT PRIMARY KEY,
        plan_id TEXT,
        repo_path TEXT NOT NULL,
        worktree_path TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_commit TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        closed_at TEXT,
        FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
    )""",
    """CREATE TABLE IF NOT EXISTS agent_sessions (
        session_id TEXT PRIMARY KEY,
        plan_id TEXT,
        run_id TEXT,
        step_id TEXT,
        agent_tool TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running',
        turns_used INTEGER NOT NULL DEFAULT 0,
        model_calls INTEGER NOT NULL DEFAULT 0,
        tool_calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS agent_turns (
        turn_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        turn_no INTEGER NOT NULL,
        outcome TEXT NOT NULL DEFAULT 'model_ok',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER,
        detail_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
    )""",
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_execution_runs_plan ON execution_runs(plan_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_execution_attempts_run ON execution_attempts(run_id, attempt_no)",
    "CREATE INDEX IF NOT EXISTS idx_steps_plan_branch_status_order ON steps(plan_id, branch_version, status, is_obsolete, step_order)",
    "CREATE INDEX IF NOT EXISTS idx_step_events_plan_event ON step_events(plan_id, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_plans_created_at ON plans(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_plan_timestamp ON audit_logs(plan_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_plan_step ON artifacts(plan_id, step_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_worktrees_plan_status ON worktrees(plan_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_sessions_plan ON agent_sessions(plan_id, started_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_turns_session ON agent_turns(session_id, turn_no)",
]


def upgrade() -> None:
    for ddl in _TABLES:
        op.execute(ddl)
    for ddl in _INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    # baseline 不提供 downgrade：回退等于删库，必须人工决策。
    raise RuntimeError("baseline 迁移不支持自动回退；如需重建请人工处理数据库文件")

