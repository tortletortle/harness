"""
数据模型定义

与Rust层的类型保持一致
"""

from datetime import datetime
from utils.utcnow import utcnow

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PlanStatus(str, Enum):
    DRAFT = "Draft"
    PENDING = "Pending"
    APPROVED = "Approved"
    RUNNING = "Running"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    ROLLED_BACK = "RolledBack"


class StepStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    RETRY = "Retry"
    SKIPPED = "Skipped"
    ROLLED_BACK = "RolledBack"


class RunStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    INTERRUPTED = "Interrupted"
    CANCELLED = "Cancelled"


class AttemptStatus(str, Enum):
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    RETRYING = "Retrying"
    INTERRUPTED = "Interrupted"
    TIMED_OUT = "TimedOut"
    CANCELLED = "Cancelled"


class ExecutionRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    status: RunStatus = RunStatus.PENDING
    trigger: str = "execute"
    branch_version: int = 1
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    owner_token: str = ""
    failure_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: utcnow())


class ExecutionAttempt(BaseModel):
    attempt_id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    plan_id: UUID
    step_id: UUID
    attempt_no: int = 0
    status: AttemptStatus = AttemptStatus.RUNNING
    started_at: datetime = Field(default_factory=lambda: utcnow())
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    retryable: Optional[bool] = None
    failure_reason: Optional[str] = None
    outputs: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=lambda: utcnow())


class Architecture(BaseModel):
    components: List[str]
    interactions: List[str]


class Difficulty(BaseModel):
    overall: str
    breakdown: Dict[str, Any]


class ToolRequirement(BaseModel):
    tool_id: str
    availability: str
    quality: Optional[str] = None
    limitations: Optional[str] = None
    impact: Optional[str] = None


class ToolAssessment(BaseModel):
    required_tools: List[ToolRequirement]
    overall_tool_completeness: float
    risks: List[str]


class DesignPart1(BaseModel):
    architecture: Architecture
    difficulty: Difficulty


class DesignDocument(BaseModel):
    design_id: UUID = Field(default_factory=uuid4)
    user_request: str
    timestamp: datetime = Field(default_factory=lambda: utcnow())
    part1_design: DesignPart1
    part2_tool_assessment: ToolAssessment
    user_approval: Optional[bool] = None
    user_comments: Optional[str] = None


class Step(BaseModel):
    step_id: UUID = Field(default_factory=uuid4)
    order: int
    description: str
    tool: str
    inputs: Dict[str, Any]
    check_criteria: str
    # 显式 WorkGraph 依赖；旧计划缺失时保持空列表并继续按 order 执行。
    depends_on: List[str] = Field(default_factory=list)
    # 资源声明：{"read": [...], "write": [...]}，用于受控并行的冲突检测。
    resources: Optional[Dict[str, Any]] = None
    # 结构化确定性验收规则；为空时兼容旧版 LLM check_result 路径。
    acceptance: List[Dict[str, Any]] = Field(default_factory=list)
    outputs: Optional[Dict[str, Any]] = None
    status: StepStatus = StepStatus.PENDING
    executed_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 2
    # 状态机扩展字段
    parent_step_id: Optional[UUID] = None
    branch_version: int = 1
    state_snapshot: Optional[Dict[str, Any]] = None
    compressed_context: Optional[str] = None
    duration_ms: Optional[int] = None
    failure_reason: Optional[str] = None
    is_obsolete: bool = False


class PlanSegment(BaseModel):
    segment_id: UUID = Field(default_factory=uuid4)
    description: str
    steps: List[Step]


class DynamicInsertion(BaseModel):
    after_step_id: UUID
    new_steps: List[Step]
    reason: str


class ExecutionPlan(BaseModel):
    plan_id: UUID = Field(default_factory=uuid4)
    design_id: UUID
    created_at: datetime = Field(default_factory=lambda: utcnow())
    status: PlanStatus = PlanStatus.PENDING
    segments: List[PlanSegment]
    implementation_list: List[str]
    dynamic_insertions: List[DynamicInsertion] = []
    # 状态机扩展字段
    current_branch: int = 1
    resume_from_step_id: Optional[UUID] = None
    paused_at_step_id: Optional[UUID] = None


class Instruction(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


class Performance(BaseModel):
    avg_execution_time: str
    success_rate: float


class ToolCapabilities(BaseModel):
    instructions: List[Instruction]
    input_format: Dict[str, Any]
    output_format: Dict[str, Any]
    quality_level: str
    performance: Performance
    limitations: List[str]


class ToolIndex(BaseModel):
    main: str
    config: Optional[str] = None
    tests: Optional[str] = None
    docs: Optional[str] = None


class ToolLicense(BaseModel):
    license_type: str
    max_instances: int
    copy_strategy: str


class TestResults(BaseModel):
    last_test_date: datetime
    passed: bool
    test_cases: int
    passed_cases: int


class ToolSchema(BaseModel):
    tool_id: str
    version: str
    name: str
    description: str
    capabilities: ToolCapabilities
    file_location: str
    index: ToolIndex
    license: ToolLicense
    dependencies: List[str]
    test_results: Optional[TestResults] = None
