"""
数据模型定义

与Rust层的类型保持一致
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PlanStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"


class StepStatus(str, Enum):
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCESS = "Success"
    FAILED = "Failed"
    SKIPPED = "Skipped"


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
    timestamp: datetime = Field(default_factory=datetime.utcnow)
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
    outputs: Optional[Dict[str, Any]] = None
    status: StepStatus = StepStatus.PENDING
    executed_at: Optional[datetime] = None


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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: PlanStatus = PlanStatus.PENDING
    segments: List[PlanSegment]
    implementation_list: List[str]
    dynamic_insertions: List[DynamicInsertion] = []


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
