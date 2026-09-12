/**
 * AgentSessionsView 组件测试
 *
 * 验证 session 列表渲染、状态徽章、turns 钻取与错误横幅。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentSessionsView } from './AgentSessionsView';
import { apiClient } from '../api/client';
import type { AgentSession } from '../types';

vi.mock('../api/client', () => ({
  apiClient: {
    getAgentSessions: vi.fn(),
    getAgentTurns: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

function makeSession(overrides: Partial<AgentSession> & Pick<AgentSession, 'session_id'>): AgentSession {
  return {
    plan_id: 'plan-aaaa',
    run_id: null,
    step_id: 'step-1',
    agent_tool: 'coding_agent',
    status: 'completed',
    turns_used: 2,
    model_calls: 2,
    tool_calls: 4,
    input_tokens: 240,
    output_tokens: 90,
    started_at: '2026-09-12T10:00:00',
    finished_at: '2026-09-12T10:01:00',
    error: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AgentSessionsView', () => {
  it('renders sessions with status and usage badges', async () => {
    mockedApi.getAgentSessions.mockResolvedValue([
      makeSession({ session_id: 'session-1ab2c3d4' }),
      makeSession({ session_id: 'session-2ab34cd5', status: 'failed', error: '模型调用失败' }),
    ]);

    render(<AgentSessionsView />);

    await waitFor(() => {
      expect(screen.getAllByText('coding_agent')).toHaveLength(2);
      expect(screen.getByText('已完成')).toBeInTheDocument();
      expect(screen.getByText('已失败')).toBeInTheDocument();
    });
  });

  it('expands turns on click with outcome labels and tokens', async () => {
    const user = userEvent.setup();
    mockedApi.getAgentSessions.mockResolvedValue([makeSession({ session_id: 'session-1ab2c3d4' })]);
    mockedApi.getAgentTurns.mockResolvedValue([
      {
        turn_row_id: 1,
        session_id: 'session-1',
        turn_no: 1,
        outcome: 'patch_error',
        input_tokens: 120,
        output_tokens: 45,
        duration_ms: 900,
        detail: {},
        created_at: '2026-09-12T10:00:10',
      },
      {
        turn_row_id: 2,
        session_id: 'session-1',
        turn_no: 2,
        outcome: 'applied',
        input_tokens: 130,
        output_tokens: 50,
        duration_ms: 800,
        detail: {},
        created_at: '2026-09-12T10:00:30',
      },
    ]);

    render(<AgentSessionsView />);
    await waitFor(() => expect(screen.getByText('coding_agent')).toBeInTheDocument());

    await user.click(screen.getByText('coding_agent'));
    await waitFor(() => {
      expect(screen.getByText('轮次记录')).toBeInTheDocument();
      expect(screen.getByText('补丁错误')).toBeInTheDocument();
      expect(screen.getByText('已写回')).toBeInTheDocument();
      expect(screen.getByText('120↑ 45↓')).toBeInTheDocument();
    });
    expect(mockedApi.getAgentTurns).toHaveBeenCalledWith('session-1ab2c3d4');
  });

  it('shows empty state when no sessions exist', async () => {
    mockedApi.getAgentSessions.mockResolvedValue([]);
    render(<AgentSessionsView />);
    await waitFor(() => {
      expect(screen.getByText(/还没有 Agent 运行记录/)).toBeInTheDocument();
    });
  });

  it('shows error banner when listing fails', async () => {
    mockedApi.getAgentSessions.mockRejectedValue(new Error('服务不可用'));
    render(<AgentSessionsView />);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/服务不可用/);
    });
  });
});
