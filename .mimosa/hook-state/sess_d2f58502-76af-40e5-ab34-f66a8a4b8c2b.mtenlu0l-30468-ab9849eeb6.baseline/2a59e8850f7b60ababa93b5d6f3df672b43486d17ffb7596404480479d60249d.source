/**
 * AuditView 组件测试
 *
 * 验证表格渲染、结果徽章、拦截原因展示、关键字过滤与刷新。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuditView } from './AuditView';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';
import type { AuditLogEntry } from '../types';

vi.mock('../api/client', () => ({
  apiClient: {
    getAuditLogs: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

function makeEntry(overrides: Partial<AuditLogEntry> & Pick<AuditLogEntry, 'audit_id'>): AuditLogEntry {
  return {
    timestamp: '2026-08-29T08:00:00',
    plan_id: 'plan-aaaa-bbbb',
    step_id: null,
    tool_id: 'file_creator',
    instruction: 'create_file',
    inputs: '{}',
    success: 1,
    blocked: 0,
    block_reason: null,
    actor: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ logs: [], toasts: [] });
});

describe('AuditView', () => {
  it('渲染混合结果徽章与拦截原因', async () => {
    mockedApi.getAuditLogs.mockResolvedValue([
      makeEntry({ audit_id: 1, success: 1 }),
      makeEntry({ audit_id: 2, blocked: 1, success: null, block_reason: '命中危险命令模式', instruction: 'rm -rf /' }),
      makeEntry({ audit_id: 3, success: 0 }),
      makeEntry({ audit_id: 4, success: null, blocked: 0 }),
    ]);

    render(<AuditView />);

    await waitFor(() => expect(screen.getByText('成功')).toBeInTheDocument());
    expect(screen.getByText('已拦截')).toBeInTheDocument();
    expect(screen.getAllByText('失败').length).toBeGreaterThan(0);
    expect(screen.getByText('未知')).toBeInTheDocument();
    expect(screen.getByText('命中危险命令模式')).toBeInTheDocument();
    expect(screen.getByText('4 / 4 条')).toBeInTheDocument();
  });

  it('关键字过滤缩小结果', async () => {
    mockedApi.getAuditLogs.mockResolvedValue([
      makeEntry({ audit_id: 1, tool_id: 'file_creator', instruction: 'create_file' }),
      makeEntry({ audit_id: 2, tool_id: 'command_runner', instruction: 'run_command' }),
    ]);

    const user = userEvent.setup();
    render(<AuditView />);
    await waitFor(() => expect(screen.getByText('2 / 2 条')).toBeInTheDocument());

    await user.type(screen.getByPlaceholderText('搜索指令、工具或计划 ID...'), 'command');

    await waitFor(() => expect(screen.getByText('1 / 2 条')).toBeInTheDocument());
    expect(screen.getByText('command_runner')).toBeInTheDocument();
    expect(screen.queryByText('file_creator')).not.toBeInTheDocument();
  });

  it('刷新按钮重新拉取', async () => {
    mockedApi.getAuditLogs.mockResolvedValue([]);

    const user = userEvent.setup();
    render(<AuditView />);
    await waitFor(() => expect(mockedApi.getAuditLogs).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole('button', { name: /刷新/ }));
    await waitFor(() => expect(mockedApi.getAuditLogs).toHaveBeenCalledTimes(2));
  });

  it('加载失败写入 error 日志', async () => {
    mockedApi.getAuditLogs.mockRejectedValue(new Error('boom'));

    render(<AuditView />);

    await waitFor(() => {
      const logs = useAppStore.getState().logs;
      expect(logs.some((l) => l.level === 'error' && l.message.includes('加载审计日志失败'))).toBe(true);
    });
  });
});
