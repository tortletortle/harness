/**
 * InputView 组件测试
 *
 * 验证快速执行（/api/quick-task）两条路径：
 * - direct：识别为直接工具 → 渲染工具结果（读取内容 / 失败原因）
 * - graph：识别为开发任务 → 渲染"进入设计书流程"引导
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { InputView } from './InputView';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';

vi.mock('../api/client', () => ({
  apiClient: {
    createDesign: vi.fn(),
    quickTask: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

function renderWithStore() {
  const state = useAppStore.getState();
  state.inputText = '';
  state.currentView = 'input';
  state.addLog = () => {};
  return render(<InputView />);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('InputView 快速执行', () => {
  it('direct 模式：读取文件成功时渲染工具输出', async () => {
    const user = userEvent.setup();
    mockedApi.quickTask.mockResolvedValue({
      success: true,
      mode: 'direct',
      tool_id: 'text_editor',
      instruction: 'read_file',
      result: { success: true, content: 'hello quick task' },
    });

    renderWithStore();
    await user.type(screen.getByPlaceholderText(/描述你想要实现的功能/), '读取 README.md');
    await user.click(screen.getByRole('button', { name: /快速执行/ }));

    await waitFor(() => {
      expect(screen.getByText(/快速执行结果/)).toBeInTheDocument();
      expect(screen.getByText(/hello quick task/)).toBeInTheDocument();
      expect(screen.getByText(/text_editor:read_file/)).toBeInTheDocument();
    });
  });

  it('direct 模式：工具失败时渲染错误原因', async () => {
    const user = userEvent.setup();
    mockedApi.quickTask.mockResolvedValue({
      success: true,
      mode: 'direct',
      tool_id: 'text_editor',
      instruction: 'read_file',
      result: { success: false, error: '被安全策略拒绝', blocked_reason: 'resource_denied' },
    });

    renderWithStore();
    await user.type(screen.getByPlaceholderText(/描述你想要实现的功能/), '读取 .env');
    await user.click(screen.getByRole('button', { name: /快速执行/ }));

    await waitFor(() => {
      expect(screen.getByText(/被安全策略拒绝/)).toBeInTheDocument();
    });
  });

  it('graph 模式：渲染"进入设计书流程"引导', async () => {
    const user = userEvent.setup();
    mockedApi.quickTask.mockResolvedValue({
      success: true,
      mode: 'graph',
      reason: '创建/修改任务，走设计书流程',
      next: '/api/design',
    });

    renderWithStore();
    await user.type(screen.getByPlaceholderText(/描述你想要实现的功能/), '创建一个新的报告文件');
    await user.click(screen.getByRole('button', { name: /快速执行/ }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /进入设计书流程/ })).toBeInTheDocument();
    });
  });

  it('graph 模式：点击"进入设计书流程"后调用 createDesign', async () => {
    const user = userEvent.setup();
    mockedApi.quickTask.mockResolvedValue({
      success: true,
      mode: 'graph',
      reason: '开发任务',
      next: '/api/design',
    });
    mockedApi.createDesign.mockResolvedValue({ design_id: 'design-1' } as any);

    renderWithStore();
    await user.type(screen.getByPlaceholderText(/描述你想要实现的功能/), '创建一个新的报告文件');
    await user.click(screen.getByRole('button', { name: /快速执行/ }));

    await waitFor(() => expect(screen.getByRole('button', { name: /进入设计书流程/ })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /进入设计书流程/ }));

    await waitFor(() => {
      expect(mockedApi.createDesign).toHaveBeenCalledWith('创建一个新的报告文件');
    });
  });
});
