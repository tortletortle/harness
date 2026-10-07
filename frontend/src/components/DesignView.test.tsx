/**
 * DesignView 组件测试
 *
 * 使用真实 zustand store + mock api/client，覆盖 approve 成功后解析并响应
 * secrets_prompt 的逻辑：设计声明了项目密钥且 prompt_shown=true 时应打开本地
 * 录入页并写日志；否则不弹窗、不影响既有批准流程。
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
  },
}));

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const design: any = {
  design_id: 'design-1',
  user_request: '构建一个需要 SECRET_API_TOKEN 的工具',
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

function setDesign() {
  useAppStore.setState({ currentDesign: design, currentPlan: null, currentView: 'design', logs: [] });
}

beforeEach(() => {
  vi.clearAllMocks();
  setDesign();
});

describe('DesignView secrets_prompt 处理', () => {
  it('批准且 prompt_shown+entry_url 时打开本地录入页并记日志', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(apiClient.approveDesign).mockResolvedValue({
      plan: { plan_id: 'plan-1' } as any,
      secrets_prompt: {
        prompt_shown: true, available: true,
        entry_url: 'http://localhost/secret-entry',
        pending: ['SECRET_API_TOKEN'], already_set: [],
      },
    } as any);

    render(<DesignView />);
    await userEvent.setup().click(screen.getByRole('button', { name: '批准并生成计划' }));

    await waitFor(() =>
      expect(open).toHaveBeenCalledWith('http://localhost/secret-entry', '_blank', 'noopener,noreferrer'));
    expect(useAppStore.getState().logs.some((l) => l.message.includes('已打开本地录入页面'))).toBe(true);
    expect(useAppStore.getState().currentPlan).toMatchObject({ plan_id: 'plan-1' });
  });

  it('prompt_shown=false 时不打开录入页', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(apiClient.approveDesign).mockResolvedValue({
      plan: { plan_id: 'plan-2' } as any,
      secrets_prompt: { prompt_shown: false, available: false, entry_url: '', pending: [], already_set: [] },
    } as any);

    render(<DesignView />);
    await userEvent.setup().click(screen.getByRole('button', { name: '批准并生成计划' }));

    await waitFor(() => expect(useAppStore.getState().currentPlan).toBeTruthy());
    expect(open).not.toHaveBeenCalled();
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
    expect(screen.getByRole('button', { name: '批准并生成计划' })).not.toBeDisabled();
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
    await userEvent.setup().click(screen.getByRole('button', { name: '批准并生成计划' }));
    await waitFor(() =>
      expect(useAppStore.getState().currentDesign?.part2?.missing_tools?.[0]?.tool_id).toBe('csv_convert'),
    );
    expect(useAppStore.getState().currentPlan).toBeNull();
  });
});