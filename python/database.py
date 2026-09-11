"""
数据库持久化层
使用SQLite存储设计书、执行计划和日志
"""

import sqlite3
import json
import uuid
from contextlib import closing
from typing import Optional, List, Dict, Any, Mapping
from datetime import datetime
from pathlib import Path

from models import (
    DesignDocument,
    ExecutionPlan,
    Step,
    PlanStatus,
    StepStatus,
    RunStatus,
    AttemptStatus,
)
from logging_config import get_logger
from domain.work_graph import dependency_statuses, normalize_dependencies, validate_graph

logger = get_logger(__name__)


class DatabaseError(RuntimeError):
    """数据库操作异常：SQL 失败、连接损坏等意外错误。False/None 仅表示未命中，不代表故障。"""


class Database:
    """数据库管理类"""
    
    def __init__(self, db_path: str = "data/eas.db"):
        """初始化数据库"""
        self.db_path = db_path
        
        # 确保数据目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化表结构
        self._initialize_tables()
    
    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row  # 使用Row对象
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        # SQLite 默认不强制外键，必须逐连接开启，
        # 否则建表时的 FOREIGN KEY 声明形同虚设，孤儿 steps/logs 可以存在。
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    def _initialize_tables(self):
        """初始化数据库表"""
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            
            # 执行运行与步骤尝试：一份计划可有多次 execute/resume/retry。
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_runs (
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
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_attempts (
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
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_runs_plan ON execution_runs(plan_id, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_execution_attempts_run ON execution_attempts(run_id, attempt_no)")

            # 设计书表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS designs (
                    design_id TEXT PRIMARY KEY,
                    user_request TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    part1_design TEXT NOT NULL,
                    part2_tool_assessment TEXT NOT NULL,
                    user_approval INTEGER,
                    user_comments TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            
            # 执行计划表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    design_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    segments TEXT NOT NULL,
                    implementation_list TEXT NOT NULL,
                    dynamic_insertions TEXT NOT NULL,
                    FOREIGN KEY (design_id) REFERENCES designs(design_id)
                )
            """)
            
            # 步骤表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS steps (
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
                    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
                )
            """)

            # 兼容旧库：如果 steps 表是在本迁移之前创建的，上面的 CREATE TABLE IF NOT EXISTS
            # 不会补齐新列，这里显式检测并 ALTER TABLE 补上。
            cursor.execute("PRAGMA table_info(steps)")
            existing_columns = {row[1] for row in cursor.fetchall()}
            if "retry_count" not in existing_columns:
                cursor.execute(
                    "ALTER TABLE steps ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
                )
            # 状态机扩展字段迁移
            for column, ddl in [
                ("parent_step_id", "ALTER TABLE steps ADD COLUMN parent_step_id TEXT"),
                ("branch_version", "ALTER TABLE steps ADD COLUMN branch_version INTEGER NOT NULL DEFAULT 1"),
                ("state_snapshot", "ALTER TABLE steps ADD COLUMN state_snapshot TEXT"),
                ("compressed_context", "ALTER TABLE steps ADD COLUMN compressed_context TEXT"),
                ("duration_ms", "ALTER TABLE steps ADD COLUMN duration_ms INTEGER"),
                ("failure_reason", "ALTER TABLE steps ADD COLUMN failure_reason TEXT"),
                ("is_obsolete", "ALTER TABLE steps ADD COLUMN is_obsolete INTEGER NOT NULL DEFAULT 0"),
                ("max_retries", "ALTER TABLE steps ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 2"),
                # 结构化验收规则（AcceptanceRunner 用）：JSON 数组，为空表示旧版字符串验收
                ("acceptance_json", "ALTER TABLE steps ADD COLUMN acceptance_json TEXT"),
                # WorkGraph 依赖：JSON 数组的 step_id，空数组 = 旧版按 step_order 线性执行
                ("depends_on", "ALTER TABLE steps ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'"),
            ]:
                if column not in existing_columns:
                    cursor.execute(ddl)

            # 执行计划表状态机扩展字段迁移
            cursor.execute("PRAGMA table_info(plans)")
            plan_columns = {row[1] for row in cursor.fetchall()}
            for column, ddl in [
                ("current_branch", "ALTER TABLE plans ADD COLUMN current_branch INTEGER NOT NULL DEFAULT 1"),
                ("resume_from_step_id", "ALTER TABLE plans ADD COLUMN resume_from_step_id TEXT"),
                ("paused_at_step_id", "ALTER TABLE plans ADD COLUMN paused_at_step_id TEXT"),
                ("archived", "ALTER TABLE plans ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"),
                ("display_name", "ALTER TABLE plans ADD COLUMN display_name TEXT"),
            ]:
                if column not in plan_columns:
                    cursor.execute(ddl)

            # 步骤事件表：用于状态机回放、时间旅行、审计回溯
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS step_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    step_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    branch_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (step_id) REFERENCES steps(step_id),
                    FOREIGN KEY (plan_id) REFERENCES plans(plan_id)
                )
            """)

            # 日志表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    context TEXT,
                    service TEXT
                )
            """)
            
            # 审计日志表：记录每一次工具调用（谁/何时/哪个计划哪个步骤/什么工具指令/结果），
            # 用于安全事件回溯（例如危险命令被拦截、恶意包名被拒绝）。
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
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
                )
            """)
            cursor.execute("PRAGMA table_info(audit_logs)")
            audit_columns = {row[1] for row in cursor.fetchall()}
            if "grant_id" not in audit_columns:
                cursor.execute("ALTER TABLE audit_logs ADD COLUMN grant_id TEXT")

            # 产物元数据表：只登记引用与哈希，不把大文件内容写进 SQLite。
            # WorkGraph 的 Agent/步骤之间通过 artifact://<artifact_id> 协作。
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
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
                )
            """)

            # Worktree 隔离表：计划可绑定一个独立 git worktree 执行，
            # 完成后经人工合并回主仓库或丢弃。status ∈ active/merged/discarded。
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS worktrees (
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
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_steps_plan_branch_status_order ON steps(plan_id, branch_version, status, is_obsolete, step_order)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_step_events_plan_event ON step_events(plan_id, event_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_plans_created_at ON plans(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_plan_timestamp ON audit_logs(plan_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_plan_step ON artifacts(plan_id, step_id, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_worktrees_plan_status ON worktrees(plan_id, status, created_at)")
            conn.commit()
    
    def create_artifact(
        self, artifact_id: str, kind: str, uri: str, *, plan_id: str = None,
        run_id: str = None, step_id: str = None, content_hash: str = None,
        size_bytes: int = None, metadata: Dict[str, Any] = None,
    ) -> bool:
        """登记产物元数据；内容留在 uri 指向的外部存储。"""
        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute("""
                    INSERT INTO artifacts
                    (artifact_id, plan_id, run_id, step_id, kind, uri, content_hash,
                     size_bytes, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(artifact_id), plan_id, run_id, step_id, str(kind), str(uri),
                    content_hash, size_bytes,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ))
                return True
        except Exception as e:
            logger.exception("登记产物失败: {}", e)
            raise DatabaseError(f"登记产物失败: {e}") from e

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        try:
            with closing(self._get_connection()) as conn:
                row = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (str(artifact_id),)).fetchone()
                if not row:
                    return None
                result = dict(row)
                result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
                return result
        except Exception as e:
            logger.exception("查询产物失败: {}", e)
            raise DatabaseError(f"查询产物失败: {e}") from e

    def list_artifacts(self, plan_id: str = None, step_id: str = None) -> List[Dict[str, Any]]:
        try:
            with closing(self._get_connection()) as conn:
                query = "SELECT * FROM artifacts WHERE 1=1"
                params: list[Any] = []
                if plan_id is not None:
                    query += " AND plan_id = ?"
                    params.append(str(plan_id))
                if step_id is not None:
                    query += " AND step_id = ?"
                    params.append(str(step_id))
                query += " ORDER BY created_at ASC"
                rows = [dict(row) for row in conn.execute(query, params).fetchall()]
                for row in rows:
                    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
                return rows
        except Exception as e:
            logger.exception("列出产物失败: {}", e)
            raise DatabaseError(f"列出产物失败: {e}") from e

    # ===== Worktree 隔离：计划级绑定与生命周期 =====

    def create_worktree_record(
        self, worktree_id: str, plan_id: str, repo_path: str,
        worktree_path: str, branch: str, base_commit: str = None,
    ) -> bool:
        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute("""
                    INSERT INTO worktrees
                    (worktree_id, plan_id, repo_path, worktree_path, branch,
                     base_commit, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                """, (
                    str(worktree_id), str(plan_id), str(repo_path), str(worktree_path),
                    str(branch), base_commit, datetime.utcnow().isoformat(),
                ))
                return True
        except Exception as e:
            logger.exception("登记 worktree 失败: {}", e)
            raise DatabaseError(f"登记 worktree 失败: {e}") from e

    def get_active_worktree(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """计划当前 active 的 worktree 绑定；无则 None。"""
        try:
            with closing(self._get_connection()) as conn:
                row = conn.execute("""
                    SELECT * FROM worktrees
                    WHERE plan_id = ? AND status = 'active'
                    ORDER BY created_at DESC LIMIT 1
                """, (str(plan_id),)).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception("查询 worktree 绑定失败: {}", e)
            raise DatabaseError(f"查询 worktree 绑定失败: {e}") from e

    def get_worktree(self, worktree_id: str) -> Optional[Dict[str, Any]]:
        try:
            with closing(self._get_connection()) as conn:
                row = conn.execute(
                    "SELECT * FROM worktrees WHERE worktree_id = ?", (str(worktree_id),)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception("查询 worktree 失败: {}", e)
            raise DatabaseError(f"查询 worktree 失败: {e}") from e

    def list_worktrees(self, plan_id: str) -> List[Dict[str, Any]]:
        try:
            with closing(self._get_connection()) as conn:
                rows = conn.execute("""
                    SELECT * FROM worktrees WHERE plan_id = ?
                    ORDER BY created_at ASC
                """, (str(plan_id),)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.exception("列出 worktree 失败: {}", e)
            raise DatabaseError(f"列出 worktree 失败: {e}") from e

    def close_worktree(self, worktree_id: str, status: str) -> bool:
        """终态收敛：status ∈ merged/discarded；非法值拒绝。"""
        if status not in ("merged", "discarded"):
            logger.warning("拒绝非法 worktree 终态: {!r}", status)
            return False
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.execute(
                    "UPDATE worktrees SET status = ?, closed_at = ? WHERE worktree_id = ?",
                    (status, datetime.utcnow().isoformat(), str(worktree_id)),
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("更新 worktree 终态失败: {}", e)
            raise DatabaseError(f"更新 worktree 终态失败: {e}") from e

    def save_design(self, design: DesignDocument) -> bool:
        """保存设计书"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO designs (
                        design_id, user_request, timestamp, 
                        part1_design, part2_tool_assessment,
                        user_approval, user_comments, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(design_id) DO UPDATE SET
                        user_request = excluded.user_request,
                        timestamp = excluded.timestamp,
                        part1_design = excluded.part1_design,
                        part2_tool_assessment = excluded.part2_tool_assessment,
                        user_approval = excluded.user_approval,
                        user_comments = excluded.user_comments
                """, (
                    str(design.design_id),
                    design.user_request,
                    design.timestamp.isoformat(),
                    json.dumps(design.part1_design.model_dump()),
                    json.dumps(design.part2_tool_assessment.model_dump()),
                    design.user_approval,
                    design.user_comments,
                    datetime.utcnow().isoformat(),
                ))
                
                conn.commit()
            return True
        except Exception as e:
            logger.exception("保存设计书失败: {}", e)
            raise DatabaseError(f"保存设计书失败: {e}") from e
    
    def get_design(self, design_id: str) -> Optional[DesignDocument]:
        """获取设计书"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM designs WHERE design_id = ?
                """, (design_id,))
                
                row = cursor.fetchone()
                
                if row:
                    from uuid import UUID
                    return DesignDocument(
                        design_id=UUID(row['design_id']),
                        user_request=row['user_request'],
                        timestamp=datetime.fromisoformat(row['timestamp']),
                        part1_design=json.loads(row['part1_design']),
                        part2_tool_assessment=json.loads(row['part2_tool_assessment']),
                        user_approval=row['user_approval'],
                        user_comments=row['user_comments'],
                    )
                return None
        except Exception as e:
            logger.exception("查询设计书失败: {}", e)
            raise DatabaseError(f"查询设计书失败: {e}") from e
    
    def save_plan(self, plan: ExecutionPlan) -> bool:
        """保存执行计划。"""
        try:
            logger.debug(f"save_plan called for plan_id={plan.plan_id}")
            logger.debug(f"Plan has {len(plan.segments)} segments")
            
            segments_json = json.dumps(
                [seg.model_dump(mode="json") for seg in plan.segments],
                ensure_ascii=False,
            )
            implementation_json = json.dumps(plan.implementation_list, ensure_ascii=False)
            insertions_json = json.dumps(
                [insertion.model_dump(mode="json") for insertion in plan.dynamic_insertions],
                ensure_ascii=False,
            )
            graph_steps = [
                {"step_id": str(step.step_id), "depends_on": normalize_dependencies(getattr(step, "depends_on", []))}
                for segment in plan.segments
                for step in segment.steps
            ]
            graph_result = validate_graph(graph_steps)
            if not graph_result.valid:
                raise DatabaseError(f"执行计划图校验失败: {graph_result.error}")
            status = plan.status.value if hasattr(plan.status, "value") else str(plan.status)

            # `conn` 自身作为上下文管理器：正常退出时 COMMIT，异常时 ROLLBACK。
            # plans + steps 跟多表多行写入因此具备原子性，不会留下半截计划。
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                logger.debug(f"Saving plan to database...")
                # 保存计划状态机字段
                cursor.execute("""
                    INSERT INTO plans (
                        plan_id, design_id, created_at, status,
                        segments, implementation_list, dynamic_insertions,
                        current_branch, resume_from_step_id, paused_at_step_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                        status = excluded.status,
                        segments = excluded.segments,
                        implementation_list = excluded.implementation_list,
                        dynamic_insertions = excluded.dynamic_insertions,
                        current_branch = excluded.current_branch,
                        resume_from_step_id = excluded.resume_from_step_id,
                        paused_at_step_id = excluded.paused_at_step_id
                """, (
                    str(plan.plan_id),
                    str(plan.design_id),
                    plan.created_at.isoformat(),
                    status,
                    segments_json,
                    implementation_json,
                    insertions_json,
                    getattr(plan, 'current_branch', 1),
                    str(getattr(plan, 'resume_from_step_id', None)) if getattr(plan, 'resume_from_step_id', None) else None,
                    str(getattr(plan, 'paused_at_step_id', None)) if getattr(plan, 'paused_at_step_id', None) else None,
                ))

                # Delete existing steps for this plan (for updates)
                # 先清 step_events：它同时 FK 引用 plans 和 steps，不清会导致
                # 删除 steps 时外键失败（失败后重新审批的真实路径，第 2 期观察发现）
                logger.debug(f"Deleting existing step_events for plan...")
                cursor.execute("""
                    DELETE FROM step_events WHERE plan_id = ?
                """, (str(plan.plan_id),))
                logger.debug(f"Deleting existing steps for plan...")
                cursor.execute("""
                    DELETE FROM steps WHERE plan_id = ?
                """, (str(plan.plan_id),))

                # Save all steps from all segments
                step_count = 0
                for seg_idx, segment in enumerate(plan.segments):
                    logger.debug(f"Processing segment {seg_idx}, has {len(segment.steps)} steps")
                    for step_idx, step in enumerate(segment.steps):
                        step_status = step.status.value if hasattr(step.status, "value") else str(step.status)
                        logger.debug(f"Saving step {step_idx}: {step.step_id} - {step.description[:30]}...")

                        # 资源声明随 inputs 持久化（不动 schema）：
                        # 执行期 select_next_batch 从 steps 表读回即可做冲突检测。
                        _inputs = dict(step.inputs or {})
                        _resources = getattr(step, "resources", None)
                        if isinstance(_resources, Mapping) and (_resources.get("read") or _resources.get("write")):
                            _inputs = {**_inputs, "resources": dict(_resources)}
                        else:
                            _inputs.pop("resources", None)

                        cursor.execute("""
                            INSERT INTO steps (
                                step_id, plan_id, segment_id, step_order,
                                description, tool, inputs, check_criteria, acceptance_json,
                                outputs, status, executed_at, retry_count,
                                parent_step_id, branch_version, state_snapshot,
                                compressed_context, duration_ms, failure_reason, is_obsolete,
                                depends_on
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(step.step_id),
                            str(plan.plan_id),
                            str(segment.segment_id),
                            step.order,
                            step.description,
                            step.tool,
                            json.dumps(_inputs, ensure_ascii=False),
                            step.check_criteria,
                            json.dumps(getattr(step, 'acceptance', []), ensure_ascii=False),
                            json.dumps(step.outputs, ensure_ascii=False) if step.outputs else None,
                            step_status,
                            step.executed_at.isoformat() if step.executed_at else None,
                            step.retry_count,
                            str(step.parent_step_id) if getattr(step, 'parent_step_id', None) else None,
                            getattr(step, 'branch_version', 1),
                            json.dumps(getattr(step, 'state_snapshot', None), ensure_ascii=False) if getattr(step, 'state_snapshot', None) else None,
                            getattr(step, 'compressed_context', None),
                            getattr(step, 'duration_ms', None),
                            getattr(step, 'failure_reason', None),
                            1 if getattr(step, 'is_obsolete', False) else 0,
                            json.dumps(list(getattr(step, 'depends_on', []) or []), ensure_ascii=False),
                        ))
                        step_count += 1

                logger.debug(f"Saved {step_count} steps total")
            logger.debug(f"Commit successful")
            return True
        except Exception as e:
            logger.exception("保存执行计划失败: {}", e)
            raise DatabaseError(f"保存执行计划失败: {e}") from e
    
    def get_plan_status(self, plan_id: str) -> Optional[str]:
        """轻量级查询计划当前状态，不解析 segments JSON。用于执行循环中频繁检测是否被请求暂停。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM plans WHERE plan_id = ?", (plan_id,))
                row = cursor.fetchone()
                return row["status"] if row else None
        except Exception as e:
            logger.exception("查询计划状态失败: {}", e)
            raise DatabaseError(f"查询计划状态失败: {e}") from e

    def get_plan_status_summary(self, plan_id: str) -> Optional[Dict[str, Any]]:
        """
        轻量级查询计划状态与活动步骤状态，供 plan_update WebSocket 广播使用。

        Returns:
            {"status": <计划状态>, "steps": [{"step_id": str, "status": str}, ...]}
            steps 只含未废弃（is_obsolete=0）的活动行——回滚后旧分支的废弃克隆
            不会混入广播；按 branch_version 升序、step_order 升序排列。
            计划行不存在返回 None（未命中，非故障）。

        SQL 失败抛 DatabaseError（与其他读操作一致）。
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM plans WHERE plan_id = ?", (plan_id,))
                plan_row = cursor.fetchone()
                if not plan_row:
                    return None
                cursor.execute(
                    "SELECT step_id, status FROM steps "
                    "WHERE plan_id = ? AND is_obsolete = 0 "
                    "ORDER BY branch_version ASC, step_order ASC",
                    (plan_id,),
                )
                steps = [
                    {"step_id": str(row["step_id"]), "status": row["status"]}
                    for row in cursor.fetchall()
                ]
                return {"status": plan_row["status"], "steps": steps}
        except Exception as e:
            logger.exception("查询计划状态摘要失败: {}", e)
            raise DatabaseError(f"查询计划状态摘要失败: {e}") from e

    def get_plan(self, plan_id: str) -> Optional[ExecutionPlan]:
        """获取执行计划"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT * FROM plans WHERE plan_id = ?
                """, (plan_id,))

                row = cursor.fetchone()

                if row:
                    from uuid import UUID
                    
                    # 从 JSON 加载 segments
                    segments_data = json.loads(row['segments'])
                    
                    # 从 steps 表读取步骤状态和输出（覆盖 JSON 中的旧数据）。
                    # 回滚克隆会沿用原 step_id 产生多行（旧分支 obsolete + 新分支活动），
                    # dict 覆盖顺序必须让「活动分支、未废弃」的行排在最后生效，
                    # 否则 get_plan 会随机返回旧分支的过期状态。
                    cursor.execute("""
                        SELECT *
                        FROM steps
                        WHERE plan_id = ?
                        ORDER BY is_obsolete DESC, branch_version ASC
                    """, (plan_id,))

                    steps_rows = cursor.fetchall()
                    steps_updates = {
                        row['step_id']: {
                            'status': row['status'],
                            'outputs': json.loads(row['outputs']) if row['outputs'] else None,
                            'executed_at': row['executed_at'],
                            'retry_count': row['retry_count'],
                            'max_retries': row['max_retries'],
                            'duration_ms': row['duration_ms'],
                            'failure_reason': row['failure_reason'],
                            'compressed_context': row['compressed_context'],
                            'state_snapshot': json.loads(row['state_snapshot']) if row['state_snapshot'] else None,
                            'acceptance': json.loads(row['acceptance_json']) if row['acceptance_json'] else [],
                            'depends_on': json.loads(row['depends_on']) if row['depends_on'] else [],
                            'is_obsolete': row['is_obsolete'],
                            'step_order': row['step_order'],
                            'branch_version': row['branch_version'],
                        }
                        for row in steps_rows
                    }

                    # 回滚克隆拥有新 step_id（原 id 是主键，无法重复插入），
                    # 但 segments JSON 里只有原步骤。把活动分支克隆行的状态
                    # 通过 parent_step_id 映射回原步骤，展示层才能反映当前分支。
                    current_branch = row['current_branch'] or 1
                    clone_updates = {
                        r['parent_step_id']: {
                            'status': r['status'],
                            'outputs': json.loads(r['outputs']) if r['outputs'] else None,
                            'executed_at': r['executed_at'],
                            'retry_count': r['retry_count'],
                            'acceptance': json.loads(r['acceptance_json']) if r['acceptance_json'] else [],
                            'depends_on': json.loads(r['depends_on']) if r['depends_on'] else [],
                            'is_obsolete': r['is_obsolete'],
                            'branch_version': r['branch_version'],
                        }
                        for r in steps_rows
                        if r['parent_step_id'] and r['branch_version'] == current_branch
                    }

                    # 更新 segments 中的步骤状态
                    for segment in segments_data:
                        for step in segment.get('steps', []):
                            step_id = step.get('step_id')
                            if step_id in steps_updates:
                                step.update(steps_updates[step_id])
                            if step_id in clone_updates:
                                step.update(clone_updates[step_id])
                    
                    return ExecutionPlan(
                        plan_id=UUID(row['plan_id']),
                        design_id=UUID(row['design_id']),
                        created_at=datetime.fromisoformat(row['created_at']),
                        status=row['status'],
                        segments=segments_data,
                        implementation_list=json.loads(row['implementation_list']),
                        dynamic_insertions=json.loads(row['dynamic_insertions']),
                        current_branch=row['current_branch'] or 1,
                        resume_from_step_id=UUID(row['resume_from_step_id']) if row['resume_from_step_id'] else None,
                        paused_at_step_id=UUID(row['paused_at_step_id']) if row['paused_at_step_id'] else None,
                    )
                return None
        except Exception as e:
            logger.exception("获取执行计划失败: {}", e)
            raise DatabaseError(f"获取执行计划失败: {e}") from e
    
    def list_designs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """列出最近的设计书"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT design_id, user_request, timestamp, created_at
                    FROM designs
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.exception("列出设计书失败: {}", e)
            raise DatabaseError(f"列出设计书失败: {e}") from e
    
    def list_plans(self, design_id: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """列出执行计划"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                
                if design_id:
                    cursor.execute("""
                        SELECT plan_id, design_id, created_at, status, archived, display_name
                        FROM plans
                        WHERE design_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (design_id, limit))
                else:
                    cursor.execute("""
                        SELECT plan_id, design_id, created_at, status, archived, display_name
                        FROM plans
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (limit,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.exception("列出执行计划失败: {}", e)
            raise DatabaseError(f"列出执行计划失败: {e}") from e
    
    def set_plan_archived(self, plan_id: str, archived: bool) -> bool:
        """设置计划归档标记。返回是否命中计划行。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE plans SET archived = ? WHERE plan_id = ?",
                    (1 if archived else 0, plan_id),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("设置计划归档状态失败: {}", e)
            raise DatabaseError(f"设置计划归档状态失败: {e}") from e

    def rename_plan(self, plan_id: str, display_name: str) -> bool:
        """设置计划备注名（可读名称，用于列表展示）。返回是否命中计划行。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE plans SET display_name = ? WHERE plan_id = ?",
                    (display_name, plan_id),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("重命名计划失败: {}", e)
            raise DatabaseError(f"重命名计划失败: {e}") from e

    def delete_plan(self, plan_id: str) -> bool:
        """删除计划及其全部步骤与事件；审计日志保留作为历史记录。

        返回是否命中计划行；未命中时调用方应返回 404。
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM steps WHERE plan_id = ?", (plan_id,))
                cursor.execute("DELETE FROM step_events WHERE plan_id = ?", (plan_id,))
                cursor.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
        except Exception as e:
            logger.exception("删除计划失败: {}", e)
            raise DatabaseError(f"删除计划失败: {e}") from e

    def design_exists(self, design_id: str) -> bool:
        """轻量存在性检查（不做完整模型解析）。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM designs WHERE design_id = ? LIMIT 1",
                    (design_id,),
                )
                return cursor.fetchone() is not None
        except Exception as e:
            logger.exception("查询设计书存在性失败: {}", e)
            raise DatabaseError(f"查询设计书存在性失败: {e}") from e

    def delete_design(self, design_id: str) -> bool:
        """删除设计书行。返回是否命中。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM designs WHERE design_id = ?", (design_id,))
                deleted = cursor.rowcount > 0
                conn.commit()
                return deleted
        except Exception as e:
            logger.exception("删除设计书失败: {}", e)
            raise DatabaseError(f"删除设计书失败: {e}") from e

    def get_steps(self, plan_id: str) -> List[Dict[str, Any]]:
        """获取计划的所有步骤"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM steps
                    WHERE plan_id = ?
                    ORDER BY step_order ASC
                """, (plan_id,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.exception("获取步骤列表失败: {}", e)
            raise DatabaseError(f"获取步骤列表失败: {e}") from e

    def get_active_steps_snapshot(self, plan_id: str, branch_version: int) -> List[Dict[str, Any]]:
        """
        获取指定分支上未废弃的活动步骤快照，按 step_order 升序。

        专用于暂停检查点计算：SQL 里直接过滤 branch_version / is_obsolete，
        避免把历史分支与废弃行全量拉到调用方再客户端过滤（get_steps 是全表行）。
        空列表表示该分支没有活动步骤（未命中，非故障）；SQL 失败抛 DatabaseError。
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM steps
                    WHERE plan_id = ? AND branch_version = ? AND is_obsolete = 0
                    ORDER BY step_order ASC
                """, (plan_id, branch_version))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception("查询活动步骤快照失败: {}", e)
            raise DatabaseError(f"查询活动步骤快照失败: {e}") from e

    def get_active_failed_steps(self, plan_id: str, branch_version: int) -> List[Dict[str, Any]]:
        """获取指定活动分支上仍处于 Failed 的步骤，供完成判定和恢复检查点使用。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM steps
                    WHERE plan_id = ? AND branch_version = ?
                      AND is_obsolete = 0 AND status = ?
                    ORDER BY step_order ASC
                """, (plan_id, branch_version, StepStatus.FAILED.value))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception("查询活动失败步骤失败: {}", e)
            raise DatabaseError(f"查询活动失败步骤失败: {e}") from e

    def get_effective_step_status(self, plan_id: str, step_id: str, branch_version: int) -> Optional[str]:
        """
        查询步骤在指定分支上的「生效状态」。

        回滚克隆拥有新 step_id 且 parent_step_id 指向原步骤，活动行是克隆行；
        因此优先取该分支上 parent_step_id == step_id 的未废弃克隆行状态，
        没有克隆时回落到原行本身的状态。找不到任何行返回 None（未命中，非故障）；
        SQL 失败抛 DatabaseError。
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT status FROM steps
                    WHERE plan_id = ? AND parent_step_id = ?
                      AND is_obsolete = 0 AND branch_version = ?
                    ORDER BY step_order ASC
                    LIMIT 1
                """, (plan_id, step_id, branch_version))
                row = cursor.fetchone()
                if row:
                    return row["status"]
                cursor.execute(
                    "SELECT status FROM steps WHERE plan_id = ? AND step_id = ?",
                    (plan_id, step_id),
                )
                row = cursor.fetchone()
                return row["status"] if row else None
        except Exception as e:
            logger.exception("查询步骤生效状态失败: {}", e)
            raise DatabaseError(f"查询步骤生效状态失败: {e}") from e

    def mark_step_tree_skipped(self, plan_id: str, step_id: str, executed_at: datetime = None) -> bool:
        """
        把指定步骤及其所有未废弃后代（回滚克隆沿 parent_step_id 链）标记为 Skipped。

        跳过的语义是「这个步骤不再执行」：克隆行与原步骤共用执行语义但 id 不同，
        只改原 id 会让活动分支上的克隆照常执行，跳过形同虚设。
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE steps SET status = ?, executed_at = COALESCE(?, executed_at) "
                    "WHERE plan_id = ? AND step_id = ?",
                    (StepStatus.SKIPPED.value, executed_at.isoformat() if executed_at else None,
                     plan_id, step_id),
                )
                frontier = [step_id]
                while frontier:
                    placeholders = ",".join("?" * len(frontier))
                    cursor.execute(
                        f"UPDATE steps SET status = ?, executed_at = COALESCE(?, executed_at) "
                        f"WHERE plan_id = ? AND parent_step_id IN ({placeholders}) AND is_obsolete = 0",
                        [StepStatus.SKIPPED.value,
                         executed_at.isoformat() if executed_at else None, plan_id] + frontier,
                    )
                    if cursor.rowcount == 0:
                        break
                    cursor.execute(
                        f"SELECT step_id FROM steps WHERE plan_id = ? "
                        f"AND parent_step_id IN ({placeholders}) AND is_obsolete = 0",
                        [plan_id] + frontier,
                    )
                    frontier = [r["step_id"] for r in cursor.fetchall()]
                return True
        except Exception as e:
            logger.exception("标记跳过失败: {}", e)
            raise DatabaseError(f"标记跳过失败: {e}") from e

    def update_step(self, step_id: str, status: str, outputs: Dict[str, Any] = None, executed_at: datetime = None, retry_count: Optional[int] = None) -> bool:
        """
        更新步骤状态。

        Returns:
            True  — 命中并更新了数据行
            False — step_id 不存在或状态值非法（未命中，非故障）

        数据库异常（SQL 失败、连接损坏等）抛出 DatabaseError，不再静默吞掉。
        调用方必须检查返回值。忽略它会导致「日志显示成功、DB 状态未变」的静默失败。
        """
        # 校验状态值，避免写入脏数据
        valid = {s.value for s in StepStatus}
        if status not in valid:
            logger.warning("拒绝非法步骤状态 {!r}，合法值: {}", status, sorted(valid))
            return False

        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()

                if retry_count is None:
                    cursor.execute("""
                        UPDATE steps
                        SET status = ?, outputs = ?, executed_at = ?
                        WHERE step_id = ?
                    """, (
                        status,
                        json.dumps(outputs, ensure_ascii=False) if outputs is not None else None,
                        executed_at.isoformat() if executed_at else None,
                        step_id,
                    ))
                else:
                    cursor.execute("""
                        UPDATE steps
                        SET status = ?, outputs = ?, executed_at = ?, retry_count = ?
                        WHERE step_id = ?
                    """, (
                        status,
                        json.dumps(outputs, ensure_ascii=False) if outputs is not None else None,
                        executed_at.isoformat() if executed_at else None,
                        retry_count,
                        step_id,
                    ))

                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("更新步骤状态失败: {}", e)
            raise DatabaseError(f"更新步骤状态失败: {e}") from e

    def update_step_state_machine(
        self,
        step_id: str,
        status: str,
        outputs: Dict[str, Any] = None,
        executed_at: datetime = None,
        retry_count: Optional[int] = None,
        duration_ms: Optional[int] = None,
        failure_reason: Optional[str] = None,
        compressed_context: Optional[str] = None,
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        状态机专用更新：除了基础字段，还写 duration_ms / failure_reason /
        compressed_context / state_snapshot。
        """
        valid = {s.value for s in StepStatus}
        if status not in valid:
            logger.warning("拒绝非法步骤状态 {!r}，合法值: {}", status, sorted(valid))
            return False

        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE steps
                    SET status = ?,
                        outputs = ?,
                        executed_at = ?,
                        retry_count = COALESCE(?, retry_count),
                        duration_ms = ?,
                        failure_reason = ?,
                        compressed_context = ?,
                        state_snapshot = ?
                    WHERE step_id = ?
                """, (
                    status,
                    json.dumps(outputs, ensure_ascii=False) if outputs is not None else None,
                    executed_at.isoformat() if executed_at else None,
                    retry_count,
                    duration_ms,
                    failure_reason,
                    compressed_context,
                    json.dumps(state_snapshot, ensure_ascii=False) if state_snapshot is not None else None,
                    step_id,
                ))
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("更新步骤状态机状态失败: {}", e)
            raise DatabaseError(f"更新步骤状态机状态失败: {e}") from e

    def update_step_compressed_context(self, step_id: str, compressed_context: str) -> bool:
        """仅更新步骤的上下文摘要，不覆盖 outputs/状态快照等已有验收证据。"""
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE steps SET compressed_context = ? WHERE step_id = ?",
                    (compressed_context, str(step_id)),
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("更新步骤上下文摘要失败: {}", e)
            raise DatabaseError(f"更新步骤上下文摘要失败: {e}") from e

    def add_step_event(self, step_id: str, plan_id: str, event_type: str, event_data: Dict[str, Any] = None, branch_version: int = 1) -> bool:
        """
        记录步骤事件，用于状态机回放与时间旅行。
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO step_events (step_id, plan_id, event_type, event_data, branch_version, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(step_id),
                    str(plan_id),
                    event_type,
                    json.dumps(event_data, ensure_ascii=False) if event_data is not None else None,
                    branch_version,
                    datetime.utcnow().isoformat(),
                ))
                return True
        except Exception as e:
            logger.exception("写入 step_event 失败: {}", e)
            raise DatabaseError(f"写入 step_event 失败: {e}") from e

    def get_step_events(self, plan_id: str, step_id: str = None, branch_version: int = None) -> List[Dict[str, Any]]:
        """获取计划/步骤的事件流，用于回溯与可视化。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                query = "SELECT * FROM step_events WHERE plan_id = ?"
                params = [plan_id]
                if step_id:
                    query += " AND step_id = ?"
                    params.append(str(step_id))
                if branch_version is not None:
                    query += " AND branch_version = ?"
                    params.append(branch_version)
                query += " ORDER BY event_id ASC"
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception("查询 step_events 失败: {}", e)
            raise DatabaseError(f"查询 step_events 失败: {e}") from e

    def select_next_batch(
        self, plan_id: str, branch_version: int = 1,
        resume_from_step_id: str = None, max_concurrency: int = 1,
        parallel_graphs: bool = False,
    ) -> Dict[str, Any]:
        """依赖感知 + 资源冲突过滤的批取步。

        返回 {steps, blocked_reason, blocked_step_ids}：
        - steps: 本批可执行步骤行（至多 max_concurrency 个，两两资源不冲突）
        - blocked_step_ids: 因依赖未满足而无法执行的 pending 步骤 id
        - blocked_reason: 存在依赖失败导致永久阻塞时的结构化原因

        兼容规则：
        - 无显式依赖图的旧计划始终每批一个步骤（与旧串行行为一致）；
        - parallel_graphs=False 时强制每批一个步骤；
        - max_concurrency <= 1 时每批一个步骤。
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM steps
                    WHERE plan_id = ?
                    ORDER BY is_obsolete DESC, branch_version ASC, step_order ASC
                """, (plan_id,))
                rows = [dict(r) for r in cursor.fetchall()]

            if resume_from_step_id:
                for row in rows:
                    if str(row["step_id"]) == str(resume_from_step_id) and not row.get("is_obsolete"):
                        return {"steps": [row], "blocked_reason": None, "blocked_step_ids": []}
                return {"steps": [], "blocked_reason": None, "blocked_step_ids": []}

            def _with_parsed_inputs(row: Dict[str, Any]) -> Dict[str, Any]:
                enriched = dict(row)
                try:
                    parsed = json.loads(enriched["inputs"]) if isinstance(enriched.get("inputs"), str) else (enriched.get("inputs") or {})
                except (ValueError, TypeError):
                    parsed = {}
                enriched["inputs"] = parsed
                if isinstance(parsed, Mapping) and isinstance(parsed.get("resources"), Mapping):
                    enriched["resources"] = parsed["resources"]
                return enriched

            statuses: Dict[str, str] = {}
            for row in rows:
                statuses[str(row["step_id"])] = str(row["status"])
                parent = row.get("parent_step_id")
                if parent and not row.get("is_obsolete"):
                    statuses[str(parent)] = str(row["status"])

            active = [
                row for row in rows
                if not row.get("is_obsolete") and int(row.get("branch_version") or 1) == int(branch_version)
            ]
            pending = [row for row in active if str(row["status"]) == StepStatus.PENDING.value]
            if not pending:
                return {"steps": [], "blocked_reason": None, "blocked_step_ids": []}

            has_explicit_graph = any(
                str(row.get("depends_on") or "").strip() not in ("", "[]", "null")
                for row in active
            )

            blocked: List[str] = []
            failed_deps: List[str] = []
            if not has_explicit_graph or not parallel_graphs or max_concurrency <= 1:
                # 串行兼容路径：取第一个就绪步骤。
                for row in pending:
                    ready, failed, blocking = dependency_statuses(row, statuses)
                    if ready:
                        return {"steps": [row], "blocked_reason": None, "blocked_step_ids": blocked}
                    blocked.append(str(row["step_id"]))
                    if failed:
                        failed_deps.extend(blocking)
                reason = f"依赖步骤失败，无法继续: {sorted(set(failed_deps))}" if failed_deps else None
                return {"steps": [], "blocked_reason": reason, "blocked_step_ids": blocked}

            # 并行路径：收集全部就绪候选，贪心挑选非冲突批次。
            from domain.work_graph import select_parallel_batch
            candidates: List[Dict[str, Any]] = []
            for row in pending:
                ready, failed, blocking = dependency_statuses(row, statuses)
                if ready:
                    candidates.append(_with_parsed_inputs(row))
                else:
                    blocked.append(str(row["step_id"]))
                    if failed:
                        failed_deps.extend(blocking)
            batch = select_parallel_batch(candidates, max_concurrency)
            reason = f"依赖步骤失败，无法继续: {sorted(set(failed_deps))}" if failed_deps and not batch else None
            return {"steps": batch, "blocked_reason": reason, "blocked_step_ids": blocked}
        except Exception as e:
            logger.exception("获取下一个可执行批次失败: {}", e)
            raise DatabaseError(f"获取下一个可执行批次失败: {e}") from e

    def get_next_runnable_step(self, plan_id: str, branch_version: int = 1, resume_from_step_id: str = None):
        """兼容旧接口：仅返回依赖感知选择出的步骤行。"""
        return self.select_next_step(plan_id, branch_version, resume_from_step_id)["step"]

    def select_next_step(self, plan_id: str, branch_version: int = 1, resume_from_step_id: str = None):
        """依赖感知的取步：返回 {step, blocked_reason, blocked_step_ids}。

        - step: 可执行步骤行（dict）或 None
        - blocked_step_ids: 因依赖未满足而无法执行的 pending 步骤 id
        - blocked_reason: 存在依赖失败导致永久阻塞时的结构化原因
        """
        selection = self.select_next_batch(
            plan_id, branch_version=branch_version,
            resume_from_step_id=resume_from_step_id, max_concurrency=1,
        )
        return {
            "step": selection["steps"][0] if selection["steps"] else None,
            "blocked_reason": selection.get("blocked_reason"),
            "blocked_step_ids": selection.get("blocked_step_ids") or [],
        }

    def mark_steps_obsolete(self, plan_id: str, after_step_id: str, branch_version: int) -> int:
        """
        将目标 step 之后的同 branch 步骤标记为 obsolete。
        返回被标记的数量。
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE steps
                    SET is_obsolete = 1
                    WHERE plan_id = ?
                      AND branch_version = ?
                      AND step_order > (
                          SELECT step_order FROM steps WHERE plan_id = ? AND step_id = ?
                      )
                """, (plan_id, branch_version, plan_id, after_step_id))
                return cursor.rowcount
        except Exception as e:
            logger.exception("标记 obsolete 失败: {}", e)
            raise DatabaseError(f"标记 obsolete 失败: {e}") from e

    def clone_steps_from(self, plan_id: str, after_step_id: str, new_branch_version: int) -> bool:
        """
        从指定 step 之后克隆后续步骤到新 branch，保留原 branch 用于对比。
        只克隆「当前活动分支」上未废弃的步骤——否则多分支计划每次回滚
        都会把历史分支的同序号步骤再克隆一遍，产生重复步骤。
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT current_branch FROM plans WHERE plan_id = ?
                """, (plan_id,))
                plan_row = cursor.fetchone()
                source_branch = plan_row["current_branch"] if plan_row and plan_row["current_branch"] else 1
                cursor.execute("""
                    SELECT * FROM steps
                    WHERE plan_id = ?
                      AND branch_version = ?
                      AND is_obsolete = 0
                      AND step_order > (
                          SELECT step_order FROM steps WHERE plan_id = ? AND step_id = ?
                      )
                    ORDER BY step_order ASC
                """, (plan_id, source_branch, plan_id, after_step_id))
                rows = cursor.fetchall()
                for row in rows:
                    # step_id 是主键，克隆必须生成新 id；
                    # parent_step_id 保留原步骤 id 以维持血缘（get_plan 展示层据此映射）。
                    cursor.execute("""
                        INSERT INTO steps (
                            step_id, plan_id, segment_id, step_order,
                            description, tool, inputs, check_criteria, acceptance_json,
                            outputs, status, executed_at, retry_count,
                            parent_step_id, branch_version, state_snapshot,
                            compressed_context, duration_ms, failure_reason, is_obsolete,
                            depends_on
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(uuid.uuid4()),
                        str(row["plan_id"]),
                        str(row["segment_id"]),
                        row["step_order"],
                        row["description"],
                        row["tool"],
                        row["inputs"],
                        row["check_criteria"],
                        row["acceptance_json"],
                        row["outputs"],
                        StepStatus.PENDING.value,
                        None,
                        row["retry_count"],
                        row["step_id"],
                        new_branch_version,
                        None,
                        None,
                        None,
                        None,
                        0,
                        row["depends_on"] if "depends_on" in row.keys() else "[]",
                    ))
                return True
        except Exception as e:
            logger.exception("克隆步骤失败: {}", e)
            raise DatabaseError(f"克隆步骤失败: {e}") from e

    def start_execution_run(
        self,
        plan_id: str,
        from_statuses: tuple,
        trigger: str = "execute",
        owner_token: str = "",
        branch_version: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """原子认领计划并创建 Running Run；认领失败不产生 Run。"""
        if not from_statuses:
            return None
        run_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        valid = {s.value for s in PlanStatus}
        if PlanStatus.RUNNING.value not in valid:
            return None
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" * len(from_statuses))
                cursor.execute(
                    f"UPDATE plans SET status = ? WHERE plan_id = ? AND status IN ({placeholders})",
                    [PlanStatus.RUNNING.value, str(plan_id), *from_statuses],
                )
                if cursor.rowcount != 1:
                    return None
                cursor.execute("""INSERT INTO execution_runs
                    (run_id, plan_id, status, trigger, branch_version, started_at,
                     heartbeat_at, owner_token, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, str(plan_id), RunStatus.RUNNING.value, str(trigger),
                     int(branch_version or 1), now, now, str(owner_token or run_id), now))
                return {"run_id": run_id, "plan_id": str(plan_id), "owner_token": str(owner_token or run_id),
                        "status": RunStatus.RUNNING.value, "branch_version": int(branch_version or 1),
                        "started_at": now}
        except Exception as e:
            logger.exception("启动执行 Run 失败: {}", e)
            raise DatabaseError(f"启动执行 Run 失败: {e}") from e

    def finish_execution_run(self, run_id: str, owner_token: str, status: str,
                             failure_reason: str = None) -> bool:
        """仅 Run owner 可以结束 Run，防止旧任务覆盖新任务。"""
        if status not in {s.value for s in RunStatus}:
            return False
        now = datetime.utcnow().isoformat()
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""UPDATE execution_runs
                    SET status = ?, finished_at = ?, heartbeat_at = ?, failure_reason = ?
                    WHERE run_id = ? AND owner_token = ? AND status = ?""",
                    (status, now, now, failure_reason, str(run_id), str(owner_token), RunStatus.RUNNING.value))
                return cursor.rowcount == 1
        except Exception as e:
            logger.exception("结束执行 Run 失败: {}", e)
            raise DatabaseError(f"结束执行 Run 失败: {e}") from e

    def heartbeat_execution_run(self, run_id: str, owner_token: str) -> bool:
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE execution_runs SET heartbeat_at = ? WHERE run_id = ? AND owner_token = ? AND status = ?",
                               (datetime.utcnow().isoformat(), str(run_id), str(owner_token), RunStatus.RUNNING.value))
                return cursor.rowcount == 1
        except Exception as e:
            raise DatabaseError(f"执行 Run 心跳失败: {e}") from e

    def create_execution_attempt(self, run_id: str, plan_id: str, step_id: str,
                                 attempt_no: int) -> Dict[str, Any]:
        attempt_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        try:
            with closing(self._get_connection()) as conn, conn:
                conn.execute("""INSERT INTO execution_attempts
                    (attempt_id, run_id, plan_id, step_id, attempt_no, status, started_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (attempt_id, str(run_id), str(plan_id), str(step_id), int(attempt_no),
                     AttemptStatus.RUNNING.value, now, now))
                return {"attempt_id": attempt_id, "run_id": str(run_id), "plan_id": str(plan_id),
                        "step_id": str(step_id), "attempt_no": int(attempt_no), "status": AttemptStatus.RUNNING.value,
                        "started_at": now}
        except Exception as e:
            logger.exception("创建执行 Attempt 失败: {}", e)
            raise DatabaseError(f"创建执行 Attempt 失败: {e}") from e

    def finish_execution_attempt(self, attempt_id: str, status: str, *, retryable: bool = None,
                                 failure_reason: str = None, outputs: Dict[str, Any] = None,
                                 duration_ms: int = None, run_id: str = None,
                                 owner_token: str = None) -> bool:
        if status not in {s.value for s in AttemptStatus}:
            return False
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                # 可选 fencing：attempt 必须仍属于指定 run，且该 run 正由 owner_token 持有。
                # 防止「旧任务/旧前台实例」在换跑/恢复后把新任务正在执行的 attempt 盖上终态。
                if run_id is not None:
                    cursor.execute("""SELECT r.status AS run_status FROM execution_attempts a
                        JOIN execution_runs r ON r.run_id = a.run_id
                        WHERE a.attempt_id = ? AND a.run_id = ?""",
                        (str(attempt_id), str(run_id)))
                    row = cursor.fetchone()
                    if not row:
                        return False
                    if row["run_status"] != RunStatus.RUNNING.value:
                        return False
                    if owner_token is not None:
                        cursor.execute("""SELECT owner_token FROM execution_runs WHERE run_id = ?""",
                                       (str(run_id),))
                        run_row = cursor.fetchone()
                        if not run_row or run_row["owner_token"] != str(owner_token):
                            return False
                cursor.execute("""UPDATE execution_attempts SET status = ?, finished_at = ?,
                    duration_ms = ?, retryable = ?, failure_reason = ?, outputs = ?
                    WHERE attempt_id = ? AND status = ?""",
                    (status, datetime.utcnow().isoformat(), duration_ms,
                     None if retryable is None else int(bool(retryable)), failure_reason,
                     json.dumps(outputs, ensure_ascii=False, default=str) if outputs is not None else None,
                     str(attempt_id), AttemptStatus.RUNNING.value))
                return cursor.rowcount == 1
        except Exception as e:
            raise DatabaseError(f"结束执行 Attempt 失败: {e}") from e

    def recover_orphaned_runs(self) -> int:
        """将服务重启遗留的 Running Run/Attempt 标记为 Interrupted。"""
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("SELECT run_id FROM execution_runs WHERE status = ?", (RunStatus.RUNNING.value,))
                run_ids = [str(row["run_id"]) for row in cursor.fetchall()]
                now = datetime.utcnow().isoformat()
                for run_id in run_ids:
                    cursor.execute("UPDATE execution_attempts SET status = ?, finished_at = ? WHERE run_id = ? AND status = ?",
                                   (AttemptStatus.INTERRUPTED.value, now, run_id, AttemptStatus.RUNNING.value))
                    cursor.execute("UPDATE execution_runs SET status = ?, finished_at = ?, heartbeat_at = ?, failure_reason = ? WHERE run_id = ? AND status = ?",
                                   (RunStatus.INTERRUPTED.value, now, now, "interrupted by service restart", run_id, RunStatus.RUNNING.value))
                return len(run_ids)
        except Exception as e:
            logger.exception("孤儿 Run 恢复失败: {}", e)
            raise DatabaseError(f"孤儿 Run 恢复失败: {e}") from e

    def get_execution_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._get_connection()) as conn:
            row = conn.execute("SELECT * FROM execution_runs WHERE run_id = ?", (str(run_id),)).fetchone()
            return dict(row) if row else None

    def list_execution_runs(self, plan_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with closing(self._get_connection()) as conn:
            rows = conn.execute("SELECT * FROM execution_runs WHERE plan_id = ? ORDER BY created_at DESC LIMIT ?",
                                (str(plan_id), max(1, min(int(limit or 100), 500)))).fetchall()
            return [dict(row) for row in rows]

    def list_execution_attempts(self, run_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        with closing(self._get_connection()) as conn:
            rows = conn.execute("SELECT * FROM execution_attempts WHERE run_id = ? ORDER BY attempt_no ASC, created_at ASC LIMIT ?",
                                (str(run_id), max(1, min(int(limit or 500), 1000)))).fetchall()
            return [dict(row) for row in rows]

    def update_plan(self, plan_id: str, status: str) -> bool:
        """
        更新计划状态。向后兼容旧调用方；新状态机逻辑请使用 update_plan_state_machine。
        """
        return self.update_plan_state_machine(plan_id, status)

    def update_plan_state_machine(self, plan_id: str, status: str, resume_from_step_id: str = None, paused_at_step_id: str = None, current_branch: int = None) -> bool:
        """更新计划状态及状态机字段。"""
        valid = {s.value for s in PlanStatus}
        if status not in valid:
            logger.warning("拒绝非法计划状态 {!r}，合法值: {}", status, sorted(valid))
            return False
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE plans
                    SET status = ?,
                        resume_from_step_id = COALESCE(?, resume_from_step_id),
                        paused_at_step_id = COALESCE(?, paused_at_step_id),
                        current_branch = COALESCE(?, current_branch)
                    WHERE plan_id = ?
                """, (
                    status,
                    resume_from_step_id,
                    paused_at_step_id,
                    current_branch,
                    plan_id,
                ))
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("更新计划状态机状态失败: {}", e)
            raise DatabaseError(f"更新计划状态机状态失败: {e}") from e

    def claim_plan_status(self, plan_id: str, to_status: str, from_statuses: tuple) -> bool:
        """
        原子认领计划状态：UPDATE plans SET status=? WHERE plan_id=? AND status IN (...)。

        把「读当前状态 → 判断 → 写新状态」的 check-then-set 竞态收敛为单条 UPDATE，
        用于消除 暂停/执行(恢复) 并发时互相覆盖状态的竞态：
        只有两个操作都以条件 UPDATE 抢占，才能保证同一个计划只被一个操作认领。

        Returns:
            True  — 认领成功（计划当前状态属于 from_statuses，已被置为 to_status）
            False — 未命中：plan_id 不存在、当前状态不属于 from_statuses、
                    to_status 非法（与 update_plan_state_machine 一致，告警返回）、
                    from_statuses 为空

        SQL 失败（连接损坏等）抛 DatabaseError。
        """
        valid = {s.value for s in PlanStatus}
        if to_status not in valid:
            logger.warning("拒绝非法计划状态 {!r}，合法值: {}", to_status, sorted(valid))
            return False
        if not from_statuses:
            logger.warning("claim_plan_status 未提供来源状态集合，无法认领: plan_id={}", plan_id)
            return False
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                placeholders = ",".join("?" * len(from_statuses))
                cursor.execute(
                    f"UPDATE plans SET status = ? "
                    f"WHERE plan_id = ? AND status IN ({placeholders})",
                    [to_status, plan_id, *from_statuses],
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("认领计划状态失败: {}", e)
            raise DatabaseError(f"认领计划状态失败: {e}") from e

    def clear_plan_checkpoint(self, plan_id: str) -> bool:
        """
        清除计划的恢复断点（resume_from_step_id / paused_at_step_id 置 NULL）。

        恢复执行认领 Running 成功后调用：断点此刻已被本次恢复消费
        （plan.resume_from_step_id 已带入执行循环），留在库中只会成为过期数据。

        Returns:
            True  — 命中并清除
            False — plan_id 不存在（未命中，非故障）

        SQL 失败抛 DatabaseError。
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE plans SET resume_from_step_id = NULL, paused_at_step_id = NULL "
                    "WHERE plan_id = ?",
                    (plan_id,),
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.exception("清除计划断点失败: {}", e)
            raise DatabaseError(f"清除计划断点失败: {e}") from e

    def recover_orphaned_executions(self) -> int:
        """恢复服务重启时遗留的 Running/Retry 执行。"""
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("SELECT plan_id, current_branch FROM plans WHERE status = ?", (PlanStatus.RUNNING.value,))
                orphaned = [dict(row) for row in cursor.fetchall()]
                for plan_row in orphaned:
                    plan_id = str(plan_row["plan_id"])
                    branch = int(plan_row.get("current_branch") or 1)
                    cursor.execute("""SELECT step_id, step_order, branch_version, status FROM steps
                        WHERE plan_id = ? AND branch_version = ? AND is_obsolete = 0
                        AND status IN (?, ?) ORDER BY step_order ASC""",
                        (plan_id, branch, StepStatus.RUNNING.value, StepStatus.RETRY.value))
                    interrupted = [dict(row) for row in cursor.fetchall()]
                    checkpoint = None
                    for row in interrupted:
                        step_id = str(row["step_id"])
                        step_branch = int(row["branch_version"] or branch)
                        cursor.execute("""SELECT event_id FROM step_events
                            WHERE plan_id = ? AND step_id = ? AND branch_version = ? AND event_type = 'success'
                            ORDER BY event_id DESC LIMIT 1""", (plan_id, step_id, step_branch))
                        if cursor.fetchone():
                            cursor.execute("UPDATE steps SET status = ? WHERE step_id = ?", (StepStatus.SUCCESS.value, step_id))
                            continue
                        if checkpoint is None:
                            checkpoint = step_id
                        cursor.execute("""INSERT INTO step_events
                            (step_id, plan_id, event_type, event_data, branch_version, created_at)
                            VALUES (?, ?, 'interrupted', ?, ?, ?)""", (step_id, plan_id,
                            json.dumps({"reason": "interrupted by service restart", "previous_status": row["status"]}, ensure_ascii=False),
                            step_branch, datetime.utcnow().isoformat()))
                        cursor.execute("UPDATE steps SET status = ? WHERE step_id = ?", (StepStatus.PENDING.value, step_id))
                    if checkpoint is not None:
                        cursor.execute("UPDATE plans SET status = ?, resume_from_step_id = ?, paused_at_step_id = NULL WHERE plan_id = ?",
                                       (PlanStatus.FAILED.value, checkpoint, plan_id))
                    else:
                        cursor.execute("UPDATE plans SET status = ? WHERE plan_id = ?", (PlanStatus.FAILED.value, plan_id))
                return len(orphaned)
        except Exception as e:
            logger.exception("孤儿任务恢复失败: {}", e)
            raise DatabaseError(f"孤儿任务恢复失败: {e}") from e

    def log(self, level: str, message: str, context: Dict[str, Any] = None, service: str = None):
        """记录日志"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO logs (timestamp, level, message, context, service)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    datetime.utcnow().isoformat(),
                    level,
                    message,
                    json.dumps(context) if context else None,
                    service,
                ))

                conn.commit()
        except Exception as e:
            # 观察性写入，刻意保持静默：日志表写入失败不应中断业务流程，
            # 且本方法可能在异常处理路径中被调用，抛错会造成连锁失败。
            logger.error("记录日志失败: {}", e)

    def save_audit_log(
        self,
        tool_id: str,
        instruction: str,
        inputs: Dict[str, Any] = None,
        plan_id: str = None,
        step_id: str = None,
        success: Optional[bool] = None,
        blocked: bool = False,
        block_reason: str = None,
        actor: str = None,
        grant_id: str = None,
    ) -> bool:
        """
        写入审计日志。每一次工具调用（无论成功/失败/被安全拦截）都应记录，
        便于事后审计回溯（例如‘哪个计划触发了危险命令拦截’）。
        失败不抛异常，返回 False。
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO audit_logs (
                        timestamp, plan_id, step_id, tool_id, instruction,
                        inputs, success, blocked, block_reason, actor, grant_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.utcnow().isoformat(),
                    plan_id,
                    step_id,
                    tool_id,
                    instruction,
                    json.dumps(inputs, default=str) if inputs else None,
                    None if success is None else int(bool(success)),
                    int(bool(blocked)),
                    block_reason,
                    actor,
                    grant_id,
                ))
                return True
        except Exception as e:
            logger.error("记录审计日志失败: {}", e)
            return False

    def list_audit_logs(self, limit: int = 100, plan_id: str = None) -> List[Dict[str, Any]]:
        """查询审计日志，按时间倒序"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                if plan_id:
                    cursor.execute(
                        "SELECT * FROM audit_logs WHERE plan_id = ? ORDER BY audit_id DESC LIMIT ?",
                        (plan_id, limit),
                    )
                else:
                    cursor.execute(
                        "SELECT * FROM audit_logs ORDER BY audit_id DESC LIMIT ?",
                        (limit,),
                    )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.exception("查询审计日志失败: {}", e)
            raise DatabaseError(f"查询审计日志失败: {e}") from e


# 全局数据库实例
_db_instance = None


def get_database(db_path: str = "data/eas.db") -> Database:
    """获取全局数据库实例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance
