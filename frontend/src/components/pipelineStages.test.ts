import { describe, expect, it } from 'vitest';
import { derivePipelineStages } from './pipelineStages';
import type { Design, ExecutionPlan } from '../types';

const designReady: Design = {
  design_id: 'd1',
  user_request: '做一个报表',
  user_approval: false,
  part1: {
    architecture: { components: [], data_flow: [], tech_stack: [] },
    difficulty: { level: 'easy', reasoning: '' },
  },
  part2: { available_tools: [], missing_tools: [], recommendations: [] },
};

const designBlocked: Design = {
  ...designReady,
  part2: {
    available_tools: [],
    missing_tools: [{ tool_id: 'ghost', purpose: 'x' }],
    recommendations: [],
  },
};

const planRunning: ExecutionPlan = {
  plan_id: 'p1',
  design_id: 'd1',
  status: 'Running',
  segments: [],
};

describe('derivePipelineStages', () => {
  it('marks tools blocked when missing_tools present', () => {
    const stages = derivePipelineStages({
      currentView: 'design',
      design: designBlocked,
      plan: null,
    });
    expect(stages.find((s) => s.id === 'tools')?.state).toBe('blocked');
    expect(stages.find((s) => s.id === 'tools')?.hint).toMatch(/缺/);
  });

  it('marks execute live when plan is running', () => {
    const stages = derivePipelineStages({
      currentView: 'execution',
      design: { ...designReady, user_approval: true },
      plan: planRunning,
      isExecuting: true,
    });
    expect(stages.find((s) => s.id === 'execute')?.state).toBe('live');
    expect(stages.find((s) => s.id === 'plan')?.state).toBe('done');
    expect(stages.find((s) => s.id === 'approve')?.state).toBe('done');
  });

  it('starts at request when empty', () => {
    const stages = derivePipelineStages({
      currentView: 'input',
      design: null,
      plan: null,
      inputText: '',
    });
    expect(stages.find((s) => s.id === 'request')?.state).toBe('current');
  });
});
