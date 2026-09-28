/**
 * PlanView 组件测试
 *
 * 使用真实 zustand store（useAppStore.setState 注入状态），
 * api/client 整体 mock，验证服务端步骤状态渲染与控制按钮行为。
 */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PlanView } from './PlanView';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';
import type { ExecutionPlan, Step } from '../types';

vi.mock('../api/client', () => ({
  apiClient: {
    getPlan: vi.fn(),
    getServiceStatus: vi.fn(),
    executePlan: vi.fn(),
    pausePlan: vi.fn(),
    resumePlan: vi.fn(),
    supplyStepInput: vi.fn(),
    getWorktree: vi.fn().mockResolvedValue({ worktree: null }),
    skipStep: vi.fn(),
    rollbackPlan: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

function makeStep(overrides: Partial<Step> & Pick<Step, 'step_id' | 'description'>): Step {
  return {
    tool: 'tool_a',
    inputs: {},
    expected_outputs: [],
    ...overrides,
  };
}

function makePlan(steps: Step[], overrides: Partial<ExecutionPlan> = {}): ExecutionPlan {
  return {
    plan_id: 'plan-1',
    design_id: 'design-1',
    status: 'Pending',
    segments: [
      { segment_id: 'seg-1', description: '第一阶段', steps },
    ],
    ...overrides,
  };
}

/** 注入 store 状态、配置默认 mock 并渲染，然后展开第一个阶段 */
async function renderPlan(plan: ExecutionPlan) {
  useAppStore.setState({ currentPlan: plan, isExecuting: false, logs: [] });
  mockedApi.getPlan.mockResolvedValue(plan);
  mockedApi.getServiceStatus.mockResolvedValue({
    status: 'running',
    llm_providers: [],
    tools_count: 0,
    current_execution: null,
  });

  render(<PlanView />);

  const user = userEvent.setup();
  // 点击阶段标题以展开步骤列表
  await user.click(screen.getByText('第一阶段'));
  return user;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  useAppStore.setState({ currentPlan: null, isExecuting: false, logs: [] });
});

describe('PlanView 步骤状态渲染', () => {
  it('按服务端状态渲染步骤图标（Success/Failed/Skipped）', async () => {
    const plan = makePlan(
      [
        makeStep({ step_id: 'step-1', description: '步骤一', status: 'Success' }),
        makeStep({ step_id: 'step-2', description: '步骤二', status: 'Failed' }),
        makeStep({ step_id: 'step-3', description: '步骤三', status: 'Skipped' }),
      ],
      { status: 'Completed' }
    );

    await renderPlan(plan);

    expect(screen.getByTitle('步骤状态: Success')).toBeInTheDocument();
    expect(screen.getByTitle('步骤状态: Failed')).toBeInTheDocument();
    expect(screen.getByTitle('步骤状态: Skipped')).toBeInTheDocument();
    expect(screen.getByLabelText('已跳过')).toBeInTheDocument();

    // 计划状态标签与重新执行按钮
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('开始执行')).toBeInTheDocument();

    // 进度：Success + Skipped 计入已完成 → 2/3
    expect(screen.getByText('2/3 步骤 (67%)')).toBeInTheDocument();
  });
});

describe('PlanView 计划级控制', () => {
  it('Failed 计划显示重试执行并复用 executePlan', async () => {
    const plan = makePlan(
      [makeStep({ step_id: 'step-1', description: '失败步骤', status: 'Failed' })],
      { status: 'Failed' }
    );
    const user = await renderPlan(plan);
    mockedApi.executePlan.mockResolvedValue(undefined);

    await user.click(screen.getByRole('button', { name: '重试执行' }));

    await waitFor(() => expect(mockedApi.executePlan).toHaveBeenCalledWith('plan-1'));
    expect(useAppStore.getState().logs.some((l) => l.message.includes('重试执行计划'))).toBe(true);
  });

  it('计划执行中显示暂停按钮，点击调用 pausePlan', async () => {
    const plan = makePlan(
      [makeStep({ step_id: 'step-1', description: '步骤一', status: 'Running' })],
      { status: 'Running' }
    );
    const user = await renderPlan(plan);
    mockedApi.pausePlan.mockResolvedValue(undefined);

    await user.click(screen.getByRole('button', { name: '暂停' }));

    await waitFor(() => expect(mockedApi.pausePlan).toHaveBeenCalledWith('plan-1'));
    await waitFor(() => {
      const logs = useAppStore.getState().logs;
      expect(logs.some(l => l.level === 'success' && l.message.includes('暂停请求已发出'))).toBe(true);
    });
  });
});

describe('PlanView 步骤级控制', () => {
  it('用户取消跳过确认后不向后端提交跳过', async () => {
    const user = await renderPlan(makePlan(
      [makeStep({ step_id: 'step-1', description: '步骤一' })],
      { status: 'Running' },
    ));
    vi.mocked(window.confirm).mockReturnValueOnce(false);

    await user.click(screen.getByRole('button', { name: '跳过' }));

    expect(window.confirm).toHaveBeenCalled();
    expect(mockedApi.skipStep).not.toHaveBeenCalled();
  });

  it('Pending 步骤显示跳过按钮，点击调用 skipStep', async () => {
    const plan = makePlan(
      [makeStep({ step_id: 'step-1', description: '步骤一' })],
      { status: 'Running' }
    );
    const user = await renderPlan(plan);
    mockedApi.skipStep.mockResolvedValue(undefined);

    await user.click(screen.getByRole('button', { name: '跳过' }));

    await waitFor(() => expect(mockedApi.skipStep).toHaveBeenCalledWith('plan-1', 'step-1'));
    await waitFor(() => {
      const logs = useAppStore.getState().logs;
      expect(logs.some(l => l.level === 'info' && l.message.includes('已标记为跳过'))).toBe(true);
    });
  });

  it('skipStep 抛错（409）时写入 error 日志', async () => {
    const plan = makePlan(
      [makeStep({ step_id: 'step-1', description: '步骤一' })],
      { status: 'Running' }
    );
    const user = await renderPlan(plan);
    mockedApi.skipStep.mockRejectedValueOnce(new Error('只能跳过待执行的步骤'));

    await user.click(screen.getByRole('button', { name: '跳过' }));
    await waitFor(() => {
      const logs = useAppStore.getState().logs;
      expect(
        logs.some(l => l.level === 'error' && l.message.includes('只能跳过待执行的步骤'))
      ).toBe(true);
    });
  });

  it('Success 步骤显示回滚按钮，确认后调用 rollbackPlan', async () => {
    const plan = makePlan(
      [
        makeStep({ step_id: 'step-1', description: '步骤一', status: 'Success' }),
        makeStep({ step_id: 'step-2', description: '步骤二', status: 'Failed' }),
      ],
      { status: 'Failed' }
    );
    const user = await renderPlan(plan);
    mockedApi.rollbackPlan.mockResolvedValue({
      new_branch: 2,
      resume_from: 'step-1',
      strategy: 'retry',
      marked_obsolete: 1,
    });

    // 点击第一个步骤（Success）的回滚按钮
    await user.click(screen.getAllByRole('button', { name: '回滚到此步' })[0]);

    // 确认对话框出现
    expect(await screen.findByText('确认回滚到此步骤')).toBeInTheDocument();
    expect(screen.getByText(/回滚将创建一个新的执行分支/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '确认回滚' }));

    await waitFor(() => expect(mockedApi.rollbackPlan).toHaveBeenCalledWith('plan-1', 'step-1'));
    await waitFor(() => {
      const logs = useAppStore.getState().logs;
      expect(logs.some(l => l.level === 'success' && l.message.includes('新分支 #2'))).toBe(true);
    });
    // 确认后对话框关闭
    await waitFor(() =>
      expect(screen.queryByText('确认回滚到此步骤')).not.toBeInTheDocument()
    );
  });
});

describe('PlanView Agent 补输入', () => {
  const waiting = makePlan([
    makeStep({ step_id: 'step-wait', description: 'Agent 提问', status: 'Pending',
               outputs: { needs_input: true, question: '请提供仓库地址' } }),
  ], { status: 'Paused', resume_from_step_id: 'step-wait' });

  it('展示问题、拒绝空白内容，保存输入但不擅自恢复执行', async () => {
    const user = await renderPlan(waiting);
    expect(screen.getByText('请提供仓库地址')).toBeInTheDocument();
    const submit = screen.getByRole('button', { name: '保存补充信息' });
    expect(submit).toBeDisabled();

    await user.type(screen.getByRole('textbox', { name: '补充信息' }), '  repo location  ');
    mockedApi.supplyStepInput.mockResolvedValue(undefined);
    await user.click(submit);

    await waitFor(() => expect(mockedApi.supplyStepInput)
      .toHaveBeenCalledWith('plan-1', 'step-wait', 'repo location'));
    expect(mockedApi.resumePlan).not.toHaveBeenCalled();
    expect(useAppStore.getState().isExecuting).toBe(false);
  });

  it('回滚分支的显示步骤与恢复目标 ID 不同时，提交到真实恢复步骤', async () => {
    const plan = makePlan([
      makeStep({ step_id: 'logical-step', description: '克隆步骤', status: 'Pending',
                 branch_version: 3, outputs: { needs_input: true, question: '需要新地址' } }),
    ], { status: 'Paused', current_branch: 3, resume_from_step_id: 'physical-clone' });
    const user = await renderPlan(plan);
    mockedApi.supplyStepInput.mockResolvedValue(undefined);

    expect(screen.getByText('需要新地址')).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: '补充信息' }), 'new address');
    await user.click(screen.getByRole('button', { name: '保存补充信息' }));

    await waitFor(() => expect(mockedApi.supplyStepInput)
      .toHaveBeenCalledWith('plan-1', 'physical-clone', 'new address'));
  });

  it('多个克隆候选无法唯一定位时不猜测提交对象', async () => {
    const plan = makePlan([
      makeStep({ step_id: 'logical-1', description: '问题一', status: 'Pending',
                 branch_version: 3, outputs: { needs_input: true, question: '第一个问题' } }),
      makeStep({ step_id: 'logical-2', description: '问题二', status: 'Pending',
                 branch_version: 3, outputs: { needs_input: true, question: '第二个问题' } }),
    ], { status: 'Paused', current_branch: 3, resume_from_step_id: 'physical-clone' });
    await renderPlan(plan);

    expect(screen.queryByRole('textbox', { name: '补充信息' })).not.toBeInTheDocument();
    expect(mockedApi.supplyStepInput).not.toHaveBeenCalled();
  });

  it('后端拒绝时显示错误且保留草稿', async () => {
    const user = await renderPlan(waiting);
    mockedApi.supplyStepInput.mockRejectedValueOnce(new Error('计划状态已变化'));
    const textbox = screen.getByRole('textbox', { name: '补充信息' });
    await user.type(textbox, 'keep this draft');
    await user.click(screen.getByRole('button', { name: '保存补充信息' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('计划状态已变化');
    expect(textbox).toHaveValue('keep this draft');
    expect(mockedApi.resumePlan).not.toHaveBeenCalled();
  });

  it('普通人工暂停不会误显 Agent 补输入表单', async () => {
    await renderPlan(makePlan([
      makeStep({ step_id: 'step-1', description: '下一步', status: 'Pending' }),
    ], { status: 'Paused', resume_from_step_id: 'step-1' }));

    expect(screen.queryByRole('textbox', { name: '补充信息' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复执行' })).toBeInTheDocument();
  });
});

describe('PlanView 依赖图', () => {
  it('按服务端依赖与当前分支展示计划层级', async () => {
    const plan = makePlan([
      makeStep({ step_id: 'step-1', description: '前置步骤', order: 1 }),
      makeStep({ step_id: 'step-2', description: '后续步骤', order: 2,
                 depends_on: ['step-1'], branch_version: 3 }),
    ], { status: 'Paused', current_branch: 3 });
    const user = await renderPlan(plan);

    expect(screen.getByText(/分支 #3/)).toBeInTheDocument();
    expect(screen.getByText('依赖 1 步')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /依赖分层视图/ }));
    expect(screen.getByText('层级 2')).toBeInTheDocument();
  });
});

describe('PlanView E2E 缺陷回归', () => {
  it('恢复成功后必须重新拉起轮询（isExecuting 置位）', async () => {
    // 回归：handleResume 曾只发请求不置 isExecuting，而 Paused 被轮询视为
    // 终态不轮询，依赖数组不变 → 后端已在执行、界面永远停留在"已暂停"。
    const pausedPlan = makePlan(
      [makeStep({ step_id: 'step-1', description: '步骤一', status: 'Success' })],
      { status: 'Paused' }
    );
    const user = await renderPlan(pausedPlan);
    // 后端恢复后计划会翻转为 Running
    mockedApi.getPlan.mockResolvedValue(
      makePlan(
        [makeStep({ step_id: 'step-1', description: '步骤一', status: 'Running' })],
        { status: 'Running' }
      )
    );
    mockedApi.resumePlan.mockResolvedValue(undefined);

    await user.click(screen.getByTitle('恢复执行'));

    await waitFor(() => expect(mockedApi.resumePlan).toHaveBeenCalledWith('plan-1'));
    // 核心断言：恢复成功后 isExecuting 必须置位（触发轮询 effect 重启）
    await waitFor(() => expect(useAppStore.getState().isExecuting).toBe(true));
  });

  it('历史列表的壳计划挂载时必须补全为全量数据', async () => {
    // 回归：/api/plans 返回壳计划（segments 空、无 status），从历史进入曾
    // 永远显示"0 个阶段 · 0 个步骤"，且非活动计划从不拉取全量数据。
    const stubPlan: ExecutionPlan = {
      plan_id: 'plan-1',
      design_id: 'design-1',
      segments: [],
    };
    const fullPlan = makePlan(
      [makeStep({ step_id: 'step-1', description: '步骤一', status: 'Pending' })],
      { status: 'Pending' }
    );
    useAppStore.setState({ currentPlan: stubPlan, isExecuting: false, logs: [] });
    mockedApi.getPlan.mockResolvedValue(fullPlan);
    mockedApi.getServiceStatus.mockResolvedValue({
      status: 'running',
      llm_providers: [],
      tools_count: 0,
      current_execution: null,
    });

    render(<PlanView />);

    // 挂载时的无条件首拉必须把壳计划替换为全量数据
    await waitFor(() =>
      expect(useAppStore.getState().currentPlan?.segments?.length).toBe(1)
    );
    expect(useAppStore.getState().currentPlan?.status).toBe('Pending');
    // 渲染出实际步骤（分段默认折叠，需展开）
    const user = userEvent.setup();
    await user.click(await screen.findByText('第一阶段'));
    expect(await screen.findByText('步骤一')).toBeInTheDocument();
  });
});
