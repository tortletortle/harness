/** 计划列表 / 系统概览 / 隔离分支相关类型 */

export interface PlanSummary {
  plan_id: string;
  design_id: string;
  status: string;
  display_name: string;
  archived: boolean;
}

export interface UsageRecord {
  ts: string;
  provider: string;
  model: string;
  phase: string;
  plan_id: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface UsageSummary {
  plan_id: string | null;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  by_phase: { phase: string; total_tokens: number; calls: number }[];
  recent: UsageRecord[];
  budget_max_tokens_per_plan: number;
}

export interface SkillInfo {
  tool_id: string;
  name: string;
  description: string;
  version: string;
  has_entry: boolean;
  runtime: string;
  side_effect: string;
  timeout: number;
  dir: string;
}

export interface SkillList {
  skills: SkillInfo[];
  skipped: { name?: string; dir?: string; reason?: string; error?: string }[];
}

export interface CapabilityInfo {
  id: string;
  version: string;
  kind: string;
  required_permissions: string[];
  side_effect: string;
  idempotent: boolean;
  budget_spec: Record<string, unknown>;
  can_spawn_agents: boolean;
}

export interface SecretList {
  available: boolean;
  count: number;
  entries: Record<string, unknown>[];
}

export interface ChatSessionStats {
  sessions: number;
  session_ids: string[];
}

export interface NotifyTestResult {
  channels: number;
  results: unknown[];
}

export interface WorktreeBinding {
  worktree_id: string;
  repo_path: string;
  worktree_path: string;
  branch: string;
  base_commit: string | null;
  created_at: string;
}

export interface WorktreeStatus {
  worktree: WorktreeBinding | null;
  diff?: { stat?: string | null; patch_truncated?: boolean; error?: string };
}

export interface WorktreeActionResult {
  success: boolean;
  branch?: string;
  output?: string;
  conflict?: boolean;
  message?: string;
  error?: string;
  detail?: string;
}
