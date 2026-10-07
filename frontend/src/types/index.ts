/**
 * API 类型定义
 */

export interface Design {
  design_id: string;
  user_request: string;
  part1: DesignPart1;
  part2: ToolAssessment;
  created_at?: string;
  /** 设计书是否已批准（与出计划可分开） */
  user_approval?: boolean;
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
  current_branch?: number;
  resume_from_step_id?: string | null;
  created_at?: string;
}

export interface PlanSegment {
  segment_id: string;
  description: string;
  steps: Step[];
}

export interface Step {
  step_id: string;
  order?: number;
  description: string;
  tool: string;
  inputs: Record<string, any>;
  expected_outputs: string[];
  acceptance?: Record<string, any>[];
  status?: StepStatus;
  outputs?: Record<string, any> | null;
  executed_at?: string | null;
  /** WorkGraph 显式依赖（1.15）：指向同计划内其他步骤的 step_id */
  depends_on?: string[];
  /** 回滚分支版本（1.17 起随步骤行返回） */
  branch_version?: number;
}

export interface Tool {
  tool_id: string;
  name: string;
  description: string;
  category?: 'generation' | 'search' | 'template' | 'other';
  capabilities: string[];
  requirements: string[];
  /** 是否可出现在执行计划步骤里 */
  plan_allowed?: boolean;
  /** SQLite tool_catalog 状态：ready / missing / … */
  catalog_status?: string | null;
  catalog_source?: string | null;
}

export interface LogMessage {
  type: 'log';
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  timestamp?: string;
  id?: string;
}

export type ServiceConnectionState = 'connecting' | 'online' | 'reconnecting' | 'offline' | 'auth_error';

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
  desktop_scope?: {
    operation: 'list_windows' | 'inspect_window' | 'screenshot';
    summary: string;
    confirmation: string;
    identity: { window_id: string; pid: number; process_created: string } | null;
    output: string | null;
  };
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
  grant_id: string | null;
}

export interface ExecutionRun {
  run_id: string;
  plan_id: string;
  status: 'Pending' | 'Running' | 'Paused' | 'Completed' | 'Failed' | 'Interrupted' | 'Cancelled';
  trigger: string;
  branch_version: number;
  started_at?: string | null;
  finished_at?: string | null;
  heartbeat_at?: string | null;
  failure_reason?: string | null;
  created_at?: string;
}

export interface ExecutionAttempt {
  attempt_id: string;
  run_id: string;
  plan_id: string;
  step_id: string;
  attempt_no: number;
  status: 'Running' | 'Succeeded' | 'Failed' | 'Retrying' | 'Interrupted' | 'TimedOut' | 'Cancelled';
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  retryable?: boolean | null;
  failure_reason?: string | null;
  outputs?: Record<string, any> | null;
  created_at?: string;
}

export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  error?: string;
}

export type ToastLevel = 'error' | 'warning';

/** Toast 通知（右下角浮出，自动消失） */
export interface Toast {
  id: number;
  level: ToastLevel;
  message: string;
  timestamp: string;
}

/** Agent 运行记录（Session 层，1.18）：一次 agent_tool 步骤执行一条 */
export interface AgentSession {
  session_id: string;
  plan_id: string | null;
  run_id: string | null;
  step_id: string | null;
  agent_tool: string;
  status: 'running' | 'completed' | 'failed';
  turns_used: number;
  model_calls: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  started_at: string;
  finished_at?: string | null;
  error?: string | null;
}

/** Agent 运行记录（Turn 层）：session 内每轮模型调用一条 */
export interface AgentTurn {
  turn_row_id: number;
  session_id: string;
  turn_no: number;
  outcome: string;
  input_tokens: number;
  output_tokens: number;
  duration_ms?: number | null;
  detail?: Record<string, any>;
  created_at: string;
}

/** 产物元数据（1.18 Artifact Store） */
export interface Artifact {
  artifact_id: string;
  plan_id: string | null;
  run_id: string | null;
  step_id: string | null;
  kind: string;
  uri: string;
  content_hash?: string | null;
  size_bytes?: number | null;
  metadata?: Record<string, any>;
  created_at: string;
}

/** 任务模式识别结果（1.19 /api/quick-task） */
export interface QuickTaskResponse {
  success: boolean;
  mode: 'direct' | 'graph';
  tool_id?: string;
  instruction?: string;
  result?: Record<string, any> & { success?: boolean; content?: string; error?: string };
  reason?: string;
  next?: string;
  grant_usage?: Record<string, any>;
}

/** 动态重规划结果（1.18 /api/plan/{id}/replan） */
export interface ReplanResponse {
  success: boolean;
  new_branch: number;
  repair_steps: number;
  anchor: string;
}
