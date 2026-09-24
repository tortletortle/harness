/**
 * ApprovalBanner 组件测试（P0-a）
 *
 * 使用真实 zustand store（useAppStore.setState 注入 pendingApproval），
 * api/client 整体 mock，验证批准/拒绝按钮调用对应端点并关闭卡片。
 */

import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApprovalBanner } from './ApprovalBanner';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';
import type { ApprovalRequestEvent } from '../types';

vi.mock('../api/client', () => ({
  apiClient: {
    getPendingApprovals: vi.fn(),
    approveToolCall: vi.fn(),
    rejectToolCall: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

const mockRequest: ApprovalRequestEvent = {
  type: 'approval_request',
  approval_id: 'ap-123',
  plan_id: 'plan-1',
  step_id: 'step-1',
  tool_id: 'command_runner',
  instruction: '运行测试命令',
  inputs: { command: 'npm test' },
  created_at: '2026-08-29T00:00:00Z',
};

describe('ApprovalBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getPendingApprovals.mockResolvedValue([]);
    useAppStore.setState({ pendingApproval: null, logs: [], toasts: [] });
  });

  it('无待审批请求时不渲染', () => {
    render(<ApprovalBanner />);
    expect(screen.queryByTestId('approval-banner')).not.toBeInTheDocument();
  });

  it('展示待审批的工具与参数', () => {
    mockedApi.getPendingApprovals.mockResolvedValueOnce([mockRequest]);
    useAppStore.setState({ pendingApproval: mockRequest });
    render(<ApprovalBanner />);
    expect(screen.getByTestId('approval-banner')).toBeInTheDocument();
    expect(screen.getByText(/command_runner/)).toBeInTheDocument();
    expect(screen.getByTestId('approval-approve')).toBeInTheDocument();
    expect(screen.getByTestId('approval-reject')).toBeInTheDocument();
  });

  it('批准时调用 approve 端点并关闭卡片', async () => {
    mockedApi.getPendingApprovals.mockResolvedValueOnce([mockRequest]);
    mockedApi.approveToolCall.mockResolvedValueOnce(undefined);
    useAppStore.setState({ pendingApproval: mockRequest });
    render(<ApprovalBanner />);

    await userEvent.type(screen.getByTestId('approval-comment'), '看起来安全');
    await userEvent.click(screen.getByTestId('approval-approve'));

    await waitFor(() => {
      expect(mockedApi.approveToolCall).toHaveBeenCalledWith('plan-1', 'ap-123', '看起来安全');
      expect(useAppStore.getState().pendingApproval).toBeNull();
    });
  });

  it('拒绝时调用 reject 端点并关闭卡片', async () => {
    mockedApi.getPendingApprovals.mockResolvedValueOnce([mockRequest]);
    mockedApi.rejectToolCall.mockResolvedValueOnce(undefined);
    useAppStore.setState({ pendingApproval: mockRequest });
    render(<ApprovalBanner />);

    await userEvent.click(screen.getByTestId('approval-reject'));

    await waitFor(() => {
      expect(mockedApi.rejectToolCall).toHaveBeenCalledWith('plan-1', 'ap-123', undefined);
      expect(useAppStore.getState().pendingApproval).toBeNull();
    });
  });

  it('请求失败时显示错误且卡片保留', async () => {
    mockedApi.getPendingApprovals.mockResolvedValueOnce([mockRequest]);
    mockedApi.approveToolCall.mockRejectedValueOnce(new Error('审批请求已被处理'));
    useAppStore.setState({ pendingApproval: mockRequest });
    render(<ApprovalBanner />);

    await userEvent.click(screen.getByTestId('approval-approve'));

    await waitFor(() => {
      expect(screen.getByText('审批请求已被处理')).toBeInTheDocument();
      expect(useAppStore.getState().pendingApproval).not.toBeNull();
    });
  });

  it('批准后忽略发起于批准前的旧轮询响应', async () => {
    let resolveRefresh!: (requests: ApprovalRequestEvent[]) => void;
    mockedApi.getPendingApprovals.mockReturnValueOnce(
      new Promise<ApprovalRequestEvent[]>((resolve) => { resolveRefresh = resolve; }),
    );
    mockedApi.approveToolCall.mockResolvedValueOnce(undefined);
    useAppStore.setState({ pendingApproval: mockRequest });
    render(<ApprovalBanner />);

    await userEvent.click(screen.getByTestId('approval-approve'));
    await waitFor(() => expect(useAppStore.getState().pendingApproval).toBeNull());

    await act(async () => { resolveRefresh([mockRequest]); });
    expect(useAppStore.getState().pendingApproval).toBeNull();
    expect(screen.queryByTestId('approval-banner')).not.toBeInTheDocument();
  });

  it('待审批轮询失败会写入可见错误日志', async () => {
    mockedApi.getPendingApprovals.mockRejectedValueOnce(new Error('offline'));
    render(<ApprovalBanner />);

    await waitFor(() => {
      expect(useAppStore.getState().logs.some(
        (entry) => entry.level === 'error' && entry.message.includes('offline'),
      )).toBe(true);
    });
  });
});
