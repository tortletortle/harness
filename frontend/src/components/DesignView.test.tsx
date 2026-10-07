/**
 * DesignView：批准与生成计划解耦后的交互。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DesignView } from './DesignView';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';

vi.mock('../api/client', () => ({
  apiClient: {
    approveDesign: vi.fn(),
    fulfillDesignTools: vi.fn(),
    generatePlanFromDesign: vi.fn(),
  },
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const design: any = {
  design_id: 'design-1',
  user_request: '构建一个需要 SECRET_API_TOKEN 的工具',
  user_approval: false,
  part1: {
    difficulty: { level: 'medium', reasoning: '中' },
    architecture: { components: ['后端'], tech_stack: ['python'], data_flow: ['req -> api'] },
  },
  part2: {
    available_tools: [{ tool_id: 'file_creator' }],
    missing_tools: [],
    recommendations: [],
    required_tools: [],
  },
};

function setDesign(overrides: Record<string, unknown> = {}) {
  useAppStore.setState({
    currentDesign: { ...design, ...overrides },
    currentPlan: null,
    currentView: 'design',
    logs: [],
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setDesign();
});

describe('DesignView 批准 / 生成计划', () => {
  it('批准成功只落批准态，不立刻出计划', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    vi.mocked(apiClient.approveDesign).mockResolvedValue({
      approved: true,
      design: { ...design, user_approval: true },
      secrets_prompt: {
        prompt_shown: true,
        available: true,
        entry_url: 'http://localhost/secret-entry',
        pending: ['SECRET_API_TOKEN'],
        already_set: [],
      },
    } as any);

    render(<DesignView />);
    await userEvent.setup().click(screen.getByRole('button', { name: '批准' }));

    await waitFor(() =>
      expect(open).toHaveBeenCalledWith('http://localhost/secret-entry', '_blank', 'noopener,noreferrer'),
    );
    expect(useAppStore.getState().currentPlan).toBeNull();
    expect(useAppStore.getState().currentDesign?.user_approval).toBe(true);
    expect(apiClient.generatePlanFromDesign).not.toHaveBeenCalled();
  });

  it('已批准后可单独生成计划', async () => {
    setDesign({ user_approval: true });
    vi.mocked(apiClient.generatePlanFromDesign).mockResolvedValue({
      plan: { plan_id: 'plan-1' } as any,
      secrets_prompt: { prompt_shown: false },
    } as any);

    render(<DesignView />);
    await userEvent.setup().click(screen.getByRole('button', { name: '生成执行计划' }));

    await waitFor(() => expect(useAppStore.getState().currentPlan).toMatchObject({ plan_id: 'plan-1' }));
    expect(useAppStore.getState().currentView).toBe('plan');
  });

  it('有缺失工具时显示重试补齐，批准仍可点', async () => {
    useAppStore.setState({
      currentDesign: {
        ...design,
        part2: {
          ...design.part2,
          missing_tools: [{ tool_id: 'csv_convert', purpose: '导出' }],
        },
      },
    });
    vi.mocked(apiClient.fulfillDesignTools).mockResolvedValue({
      design: {
        ...design,
        part2: { ...design.part2, missing_tools: [] },
      },
      missing_tools: [],
      ready: true,
    } as any);

    render(<DesignView />);
    expect(screen.getByRole('button', { name: '批准' })).not.toBeDisabled();
    expect(screen.getByText(/还有 1 个工具未就绪/)).toBeTruthy();
    await userEvent.setup().click(screen.getByRole('button', { name: '重试补齐工具' }));
    await waitFor(() => expect(apiClient.fulfillDesignTools).toHaveBeenCalledWith('design-1'));
    await waitFor(() =>
      expect(useAppStore.getState().currentDesign?.part2?.missing_tools?.length ?? -1).toBe(0),
    );
  });

  it('批准返回 toolsIncomplete 时刷新设计书', async () => {
    vi.mocked(apiClient.approveDesign).mockResolvedValue({
      toolsIncomplete: true,
      message: '工具未齐：csv_convert',
      missing_tools: ['csv_convert'],
      design: {
        ...design,
        part2: {
          ...design.part2,
          missing_tools: [{ tool_id: 'csv_convert', purpose: '导出' }],
        },
      },
    } as any);

    render(<DesignView />);
    await userEvent.setup().click(screen.getByRole('button', { name: '批准' }));
    await waitFor(() =>
      expect(useAppStore.getState().currentDesign?.part2?.missing_tools?.[0]?.tool_id).toBe('csv_convert'),
    );
    expect(useAppStore.getState().currentPlan).toBeNull();
  });
});
