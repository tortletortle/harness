/**
 * 业务流水线阶段推导（需求→设计→工具→批准→计划→执行）。
 */
import type { Design, ExecutionPlan, PlanStatus } from '../types';

export type PipelineStageId =
  | 'request'
  | 'design'
  | 'tools'
  | 'approve'
  | 'plan'
  | 'execute';

export type PipelineStageState = 'idle' | 'current' | 'done' | 'blocked' | 'live' | 'failed';

export type PipelineStage = {
  id: PipelineStageId;
  label: string;
  state: PipelineStageState;
  view: 'input' | 'design' | 'plan' | 'execution';
  hint?: string;
};

export type PipelineInput = {
  currentView: string;
  inputText?: string;
  design: Design | null;
  plan: ExecutionPlan | null;
  isExecuting?: boolean;
};

const LABELS: Record<PipelineStageId, string> = {
  request: '需求',
  design: '设计',
  tools: '工具',
  approve: '批准',
  plan: '计划',
  execute: '执行',
};

function planStatus(plan: ExecutionPlan | null): PlanStatus | undefined {
  return plan?.status;
}

export function derivePipelineStages(input: PipelineInput): PipelineStage[] {
  const { currentView, inputText, design, plan, isExecuting } = input;
  const hasRequest = Boolean(design?.user_request || plan || (inputText || '').trim());
  const hasDesign = Boolean(design);
  const missing = design?.part2?.missing_tools?.length ?? 0;
  const toolsReady = hasDesign && missing === 0;
  const approved = Boolean(design?.user_approval) || Boolean(plan);
  const hasPlan = Boolean(plan);
  const status = planStatus(plan);
  const execFailed = status === 'Failed' || status === 'RolledBack';
  const execDone = status === 'Completed';
  const execLive =
    Boolean(isExecuting) || status === 'Running' || status === 'Paused';

  const pick = (
    id: PipelineStageId,
    state: PipelineStageState,
    view: PipelineStage['view'],
    hint?: string,
  ): PipelineStage => ({ id, label: LABELS[id], state, view, hint });

  // 当前高亮：优先对齐用户所在视图，其次卡在第一个未完成台阶
  let focus: PipelineStageId = 'request';
  if (currentView === 'execution' && hasPlan) focus = 'execute';
  else if (currentView === 'plan' && hasPlan) focus = 'plan';
  else if (currentView === 'design' && hasDesign) {
    if (!toolsReady) focus = 'tools';
    else if (!approved) focus = 'approve';
    else if (!hasPlan) focus = 'plan';
    else focus = 'approve';
  } else if (currentView === 'input') focus = 'request';
  else if (!hasRequest) focus = 'request';
  else if (!hasDesign) focus = 'design';
  else if (!toolsReady) focus = 'tools';
  else if (!approved) focus = 'approve';
  else if (!hasPlan) focus = 'plan';
  else focus = 'execute';

  const withFocus = (id: PipelineStageId, base: PipelineStageState): PipelineStageState => {
    if (base === 'done' || base === 'live' || base === 'failed' || base === 'blocked') return base;
    return id === focus ? 'current' : base;
  };

  return [
    pick('request', withFocus('request', hasRequest ? 'done' : 'idle'), 'input'),
    pick('design', withFocus('design', hasDesign ? 'done' : 'idle'), 'design'),
    pick(
      'tools',
      withFocus(
        'tools',
        !hasDesign ? 'idle' : toolsReady ? 'done' : 'blocked',
      ),
      'design',
      !hasDesign ? undefined : toolsReady ? undefined : `缺 ${missing} 个工具`,
    ),
    pick('approve', withFocus('approve', approved ? 'done' : hasDesign && toolsReady ? 'idle' : 'idle'), 'design'),
    pick('plan', withFocus('plan', hasPlan ? 'done' : 'idle'), 'plan'),
    pick(
      'execute',
      execFailed
        ? 'failed'
        : execDone
          ? 'done'
          : execLive
            ? 'live'
            : withFocus('execute', hasPlan ? 'idle' : 'idle'),
      'execution',
      execFailed ? '失败/回滚' : execLive ? (status === 'Paused' ? '已暂停' : '进行中') : undefined,
    ),
  ];
}
