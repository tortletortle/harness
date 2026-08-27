"""
数据库持久化层
使用SQLite存储设计书、执行计划和日志
"""

import sqlite3
import json
from contextlib import closing
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path

from models import DesignDocument, ExecutionPlan, Step, PlanStatus, StepStatus
from logging_config import get_logger

logger = get_logger(__name__)


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
            logger.error("保存设计书失败: {}", e)
            return False
    
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
            logger.error("获取设计书失败: {}", e)
            return None
    
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
                cursor.execute("""
                    INSERT INTO plans (
                        plan_id, design_id, created_at, status,
                        segments, implementation_list, dynamic_insertions
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(plan_id) DO UPDATE SET
                        status = excluded.status,
                        segments = excluded.segments,
                        implementation_list = excluded.implementation_list,
                        dynamic_insertions = excluded.dynamic_insertions
                """, (
                    str(plan.plan_id),
                    str(plan.design_id),
                    plan.created_at.isoformat(),
                    status,
                    segments_json,
                    implementation_json,
                    insertions_json,
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
                                outputs, status, executed_at, retry_count
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        ))
                        step_count += 1

                logger.debug(f"Saved {step_count} steps total")
            logger.debug(f"Commit successful")
            return True
        except Exception as e:
            logger.exception("保存执行计划失败: {}", e)
            return False
    
    def get_plan_status(self, plan_id: str) -> Optional[str]:
        """轻量级查询计划当前状态，不解析 segments JSON。用于执行循环中频繁检测是否被请求暂停。"""
        try:
            with closing(self._get_connection()) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM plans WHERE plan_id = ?", (plan_id,))
                row = cursor.fetchone()
                return row["status"] if row else None
        except Exception as e:
            logger.error("查询计划状态失败: {}", e)
            return None

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
                    
                    # 从 steps 表读取步骤状态和输出（覆盖 JSON 中的旧数据）
                    cursor.execute("""
                        SELECT step_id, status, outputs, executed_at, retry_count
                        FROM steps
                        WHERE plan_id = ?
                    """, (plan_id,))

                    steps_rows = cursor.fetchall()
                    steps_updates = {
                        row['step_id']: {
                            'status': row['status'],
                            'outputs': json.loads(row['outputs']) if row['outputs'] else None,
                            'executed_at': row['executed_at'],
                            'retry_count': row['retry_count'],
                        }
                        for row in steps_rows
                    }

                    # 更新 segments 中的步骤状态
                    for segment in segments_data:
                        for step in segment.get('steps', []):
                            step_id = step.get('step_id')
                            if step_id in steps_updates:
                                step['status'] = steps_updates[step_id]['status']
                                step['outputs'] = steps_updates[step_id]['outputs']
                                step['executed_at'] = steps_updates[step_id]['executed_at']
                                step['retry_count'] = steps_updates[step_id]['retry_count']
                    
                    return ExecutionPlan(
                        plan_id=UUID(row['plan_id']),
                        design_id=UUID(row['design_id']),
                        created_at=datetime.fromisoformat(row['created_at']),
                        status=row['status'],
                        segments=segments_data,
                        implementation_list=json.loads(row['implementation_list']),
                        dynamic_insertions=json.loads(row['dynamic_insertions']),
                    )
                return None
        except Exception as e:
            logger.exception("获取执行计划失败: {}", e)
            return None
    
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
            logger.error("列出设计书失败: {}", e)
            return []
    
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
            logger.error("列出执行计划失败: {}", e)
            return []
    
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
            logger.error("获取步骤列表失败: {}", e)
            return []
    
    def update_step(self, step_id: str, status: str, outputs: Dict[str, Any] = None, executed_at: datetime = None, retry_count: Optional[int] = None) -> bool:
        """
        更新步骤状态。

        Returns:
            True  — 命中并更新了数据行
            False — step_id 不存在、状态值非法或写入异常

        调用方必须检查返回值。忽略它会导致「日志显示成功、DB 状态未变」的静默失败。
        """
        # 校验状态值，避免写入 COMPLETED/FAILED 这类不属于 StepStatus 的脏数据
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
            logger.error("更新步骤状态失败: {}", e)
            return False

    def update_plan(self, plan_id: str, status: str) -> bool:
        """
        更新计划状态。

        之前计划状态只在 save_plan() 创建时写入一次，执行完成/失败从不回写，
        导致前端看到的 plans.status 永远是初始值。

        Returns:
            True — 命中并更新了数据行
        """
        valid = {s.value for s in PlanStatus}
        if status not in valid:
            logger.warning("拒绝非法计划状态 {!r}，合法值: {}", status, sorted(valid))
            return False

        try:
            with closing(self._get_connection()) as conn, conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE plans SET status = ? WHERE plan_id = ?",
                    (status, plan_id),
                )
                return cursor.rowcount > 0
        except Exception as e:
            logger.error("更新计划状态失败: {}", e)
            return False

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
            logger.error("孤儿任务恢复失败: {}", e)
            return 0
    
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
            logger.error("查询审计日志失败: {}", e)
            return []


# 全局数据库实例
_db_instance = None


def get_database(db_path: str = "data/eas.db") -> Database:
    """获取全局数据库实例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance
