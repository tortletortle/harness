"""
数据库持久化层
使用SQLite存储设计书、执行计划和日志
"""

import sqlite3
import json
import uuid
from contextlib import closing
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path

from models import DesignDocument, ExecutionPlan, Step, PlanStatus, StepStatus
from logging_config import get_logger

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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 使用Row对象
        # SQLite 默认不强制外键，必须逐连接开启，
        # 否则建表时的 FOREIGN KEY 声明形同虚设，孤儿 steps/logs 可以存在。
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    
    def _initialize_tables(self):
        """初始化数据库表"""
        with closing(self._get_connection()) as conn:
            cursor = conn.cursor()
            
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
                    outputs TEXT,
                    status TEXT NOT NULL,
                    executed_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
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
                    actor TEXT
                )
            """)
            
            conn.commit()
    
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
                        user_approval = ?,
                        user_comments = ?
                """, (
                    str(design.design_id),
                    design.user_request,
                    design.timestamp.isoformat(),
                    json.dumps(design.part1_design.model_dump()),
                    json.dumps(design.part2_tool_assessment.model_dump()),
                    design.user_approval,
                    design.user_comments,
                    datetime.utcnow().isoformat(),
                    design.user_approval,
                    design.user_comments,
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

                        cursor.execute("""
                            INSERT INTO steps (
                                step_id, plan_id, segment_id, step_order,
                                description, tool, inputs, check_criteria,
                                outputs, status, executed_at, retry_count,
                                parent_step_id, branch_version, state_snapshot,
                                compressed_context, duration_ms, failure_reason, is_obsolete
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            str(step.step_id),
                            str(plan.plan_id),
                            str(segment.segment_id),
                            step.order,
                            step.description,
                            step.tool,
                            json.dumps(step.inputs, ensure_ascii=False),
                            step.check_criteria,
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
                        SELECT plan_id, design_id, created_at, status
                        FROM plans
                        WHERE design_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (design_id, limit))
                else:
                    cursor.execute("""
                        SELECT plan_id, design_id, created_at, status
                        FROM plans
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (limit,))
                
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.exception("列出执行计划失败: {}", e)
            raise DatabaseError(f"列出执行计划失败: {e}") from e
    
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

    def get_next_runnable_step(self, plan_id: str, branch_version: int = 1, resume_from_step_id: str = None):
        """
        获取当前计划下一个可运行的 step。
        优先返回指定 resume_from_step_id，否则返回最早 pending 且非 obsolete 的 step。
        """
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                if resume_from_step_id:
                    cursor.execute("""
                        SELECT * FROM steps
                        WHERE plan_id = ? AND step_id = ? AND is_obsolete = 0
                        ORDER BY step_order ASC
                        LIMIT 1
                    """, (plan_id, resume_from_step_id))
                else:
                    cursor.execute("""
                        SELECT * FROM steps
                        WHERE plan_id = ? AND status = ? AND is_obsolete = 0 AND branch_version = ?
                        ORDER BY step_order ASC
                        LIMIT 1
                    """, (plan_id, StepStatus.PENDING.value, branch_version))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception("获取下一个可运行步骤失败: {}", e)
            raise DatabaseError(f"获取下一个可运行步骤失败: {e}") from e

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
                            description, tool, inputs, check_criteria,
                            outputs, status, executed_at, retry_count,
                            parent_step_id, branch_version, state_snapshot,
                            compressed_context, duration_ms, failure_reason, is_obsolete
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(uuid.uuid4()),
                        str(row["plan_id"]),
                        str(row["segment_id"]),
                        row["step_order"],
                        row["description"],
                        row["tool"],
                        row["inputs"],
                        row["check_criteria"],
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
                    ))
                return True
        except Exception as e:
            logger.exception("克隆步骤失败: {}", e)
            raise DatabaseError(f"克隆步骤失败: {e}") from e

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
        """
        单进程部署下，进程重启意味着之前标记为 Running 的执行必然已经中断（无人在后台推进）。
        这些残留的 Running 记录会永久卡住，未来也无法被正常的暂停/恢复逻辑感知。

        在服务启动时调用：将所有 plans.status == Running 的计划标记为 Failed，
        同时把其下所有仍为 Running 的 steps 也标记为 Failed，避免留下不一致的中间态。

        Returns:
            被标记为 Failed 的计划数量
        """
        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT plan_id FROM plans WHERE status = ?",
                    (PlanStatus.RUNNING.value,),
                )
                orphaned_plan_ids = [row["plan_id"] for row in cursor.fetchall()]

                if not orphaned_plan_ids:
                    return 0

                for plan_id in orphaned_plan_ids:
                    cursor.execute(
                        "UPDATE plans SET status = ? WHERE plan_id = ?",
                        (PlanStatus.FAILED.value, plan_id),
                    )
                    cursor.execute(
                        "UPDATE steps SET status = ? WHERE plan_id = ? AND status = ?",
                        (StepStatus.FAILED.value, plan_id, StepStatus.RUNNING.value),
                    )

                return len(orphaned_plan_ids)
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
                        inputs, success, blocked, block_reason, actor
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
