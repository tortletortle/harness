/**
 * API 类型定义
 */

export interface Design {
  design_id: string;
  user_request: string;
  part1: DesignPart1;
  part2: ToolAssessment;
  created_at?: string;
}

export interface DesignPart1 {
  architecture: Architecture;
  difficulty: Difficulty;
  context?: any;
}

export interface Architecture {
  components: string[];
  data_flow: string[];
  tech_stack: string[];
}

export interface Difficulty {
  level: string;
  reasoning: string;
}

export interface ToolAssessment {
  available_tools: ToolRequirement[];
  missing_tools: ToolRequirement[];
  recommendations: string[];
}

export interface ToolRequirement {
  tool_id: string;
  purpose: string;
  quality?: string;
}

/** 计划状态（与后端 PlanStatus 枚举对应） */
export type PlanStatus =
  | 'Draft'
  | 'Pending'
  | 'Approved'
  | 'Running'
  | 'Paused'
  | 'Completed'
  | 'Failed'
  | 'RolledBack';

/** 步骤状态（与后端 StepStatus 枚举对应） */
export type StepStatus =
  | 'Pending'
  | 'Running'
  | 'Success'
  | 'Failed'
  | 'Retry'
  | 'Skipped'
  | 'RolledBack';

export interface ExecutionPlan {
  plan_id: string;
  design_id: string;
  status?: PlanStatus;
  segments: PlanSegment[];
  created_at?: string;
}

export interface PlanSegment {
  segment_id: string;
  description: string;
  steps: Step[];
}

export interface Step {
  step_id: string;
  description: string;
  tool: string;
  inputs: Record<string, any>;
  expected_outputs: string[];
  status?: StepStatus;
  outputs?: Record<string, any> | null;
  executed_at?: string | null;
}

export interface Tool {
  tool_id: string;
  name: string;
  description: string;
  category?: 'generation' | 'search' | 'template' | 'other';
  capabilities: string[];
  requirements: string[];
}

export interface LogMessage {
  type: 'log';
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp?: string;
}

/** WebSocket 推送的计划状态变更事件（type=plan_update） */
export interface PlanUpdateEvent {
  type: 'plan_update';
  plan_id: string;
  status: PlanStatus;
  steps: { step_id: string; status: StepStatus }[];
  /** P1-b 版本围栏：后端自增序号，前端丢弃过期推送 */
  version?: number;
}

/** WebSocket 推送的工具审批请求（type=approval_request） */
export interface ApprovalRequestEvent {
  type: 'approval_request';
  approval_id: string;
  plan_id: string;
  step_id: string;
  tool_id: string;
  instruction: string;
  inputs: Record<string, unknown>;
  created_at: string;
}

/** WebSocket 推送的审批决定（type=approval_resolved） */
export interface ApprovalResolvedEvent {
  type: 'approval_resolved';
  approval_id: string;
  approved: boolean;
}

/** 审计日志条目（后端 /api/audit-logs 返回的原始行） */
export interface AuditLogEntry {
  audit_id: number;
  timestamp: string;
  plan_id: string;
  step_id: string | null;
  tool_id: string | null;
  instruction: string | null;
  inputs: string | null;
  success: 0 | 1 | null;
  blocked: 0 | 1;
  block_reason: string | null;
  actor: string | null;
}

export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  error?: string;
}

/** Toast 通知级别 */
export type ToastLevel = 'error' | 'warning';

/** Toast 通知（右下角浮出，自动消失） */
export interface Toast {
  id: number;
  level: ToastLevel;
  message: string;
  timestamp: string;
}
