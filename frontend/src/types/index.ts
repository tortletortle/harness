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

export interface ExecutionPlan {
  plan_id: string;
  design_id: string;
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

export interface ApiResponse<T = any> {
  success: boolean;
  data?: T;
  error?: string;
}
