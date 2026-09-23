/**
 * ExecutionView 组件测试
 *
 * 验证解析三分支：附着执行中的计划 / 回退最近计划 / 空状态。
 * api/client 整体 mock，使用真实 zustand store。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ExecutionView } from './ExecutionView';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';
import type { ExecutionPlan } from '../types';

vi.mock('../api/client', () => ({
  apiClient: {
    getServiceStatus: vi.fn(),
    getPlans: vi.fn(),
    getPlan: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

function makePlan(overrides: Partial<ExecutionPlan> = {}): ExecutionPlan {
  return {
    plan_id: 'plan-aaaa-bbbb',
    design_id: 'design-1',
    status: 'Pending',
    segments: [
      { segment_id: 'seg-1', description: '第一阶段', steps: [] },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ currentPlan: null, isExecuting: false, logs: [] });
});

describe('ExecutionView 解析分支', () => {
  it('current_execution 存在时附着执行中的计划', async () => {
    mockedApi.getServiceStatus.mockResolvedValue({
      status: 'running',
      llm_providers: [],
      tools_count: 0,
      current_execution: 'plan-aaaa-bbbb',
    });
    mockedApi.getPlan.mockResolvedValue(makePlan({ status: 'Running' }));

    render(<ExecutionView />);

    await waitFor(() =>
      expect(screen.getByText(/已附着执行中的计划/)).toBeInTheDocument()
    );
    expect(mockedApi.getPlan).toHaveBeenCalledWith('plan-aaaa-bbbb');
    expect(useAppStore.getState().currentPlan?.plan_id).toBe('plan-aaaa-bbbb');
  });

  it('无执行中任务时回退到最近计划', async () => {
    mockedApi.getServiceStatus.mockResolvedValue({
      status: 'running',
      llm_providers: [],
      tools_count: 0,
      current_execution: null,
    });
    mockedApi.getPlans.mockResolvedValue([makePlan()]);
    mockedApi.getPlan.mockResolvedValue(makePlan());

    render(<ExecutionView />);

    await waitFor(() => expect(screen.getByText(/显示最近的计划/)).toBeInTheDocument());
    expect(mockedApi.getPlans).toHaveBeenCalledWith(1);
  });

  it('无任何计划时显示空状态并可跳转新建任务', async () => {
    mockedApi.getServiceStatus.mockResolvedValue({
      status: 'running',
      llm_providers: [],
      tools_count: 0,
      current_execution: null,
    });
    mockedApi.getPlans.mockResolvedValue([]);

    const user = userEvent.setup();
    render(<ExecutionView />);

    expect(await screen.findByText('暂无执行任务')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '去新建任务' }));
    expect(useAppStore.getState().currentView).toBe('input');
  });
});
