/**
 * 执行计划查看页面
 *
 * 步骤状态来自后端权威数据（plan.status / step.status，由 usePlanPolling 轮询同步），
 * 不再从日志文本推断。
 */

import { useState } from 'react';
import {
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Clock,
  GitBranch,
  Link2,
  Loader2,
  MinusCircle,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  SkipForward,
  XCircle,
} from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import { usePlanPolling } from '../hooks/usePlanPolling';
import { WorktreePanel } from './WorktreePanel';
import { PlanFlowGraph } from './PlanFlowGraph';
import type { ExecutionPlan, PlanStatus, Step, StepStatus } from '../types';

const TERMINAL_PLAN_STATUSES: PlanStatus[] = ['Completed', 'Failed', 'Paused', 'RolledBack'];

const PLAN_STATUS_LABELS: Record<PlanStatus, string> = {
  Draft: '草稿',
  Pending: '待执行',
  Approved: '已批准',
  Running: '执行中',
  Paused: '已暂停',
  Completed: '已完成',
  Failed: '已失败',
  RolledBack: '已回滚',
};

type ControlAction = 'pause' | 'resume' | 'input' | 'skip' | 'rollback' | 'replan' | null;

function isTerminalPlan(plan: ExecutionPlan): boolean {
  return plan.status ? TERMINAL_PLAN_STATUSES.includes(plan.status) : false;
}

function statusTone(status?: PlanStatus): string {
  switch (status) {
    case 'Running':
      return 'border-blue-400/25 bg-blue-400/10 text-blue-200';
    case 'Failed':
      return 'border-red-400/25 bg-red-400/10 text-red-200';
    case 'Completed':
      return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200';
    case 'Paused':
      return 'border-amber-400/25 bg-amber-400/10 text-amber-200';
    default:
      return 'border-white/[0.1] bg-white/[0.05] text-zinc-300';
  }
}

export function PlanView() {
  const {
    currentPlan,
    isExecuting,
    setIsExecuting,
    setCurrentPlan,
    addLog,
  } = useAppStore();
  const [expandedSegments, setExpandedSegments] = useState<Set<string>>(new Set());
  const [controlInFlight, setControlInFlight] = useState<ControlAction>(null);
  const [rollbackTarget, setRollbackTarget] = useState<Step | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [stepInputDraft, setStepInputDraft] = useState({ planId: '', stepId: '', text: '' });
  const [allExpanded, setAllExpanded] = useState(false);
  /** 默认流程图（Dify 风格节点连线）；可切回阶段列表。测试环境默认列表，避免 jsdom+ReactFlow 噪音。 */
  const [viewMode, setViewMode] = useState<'flow' | 'list'>(
    import.meta.env.MODE === 'test' ? 'list' : 'flow',
  );
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  usePlanPolling();

  if (!currentPlan) {
    return (
      <div className="flex min-h-full items-center justify-center text-zinc-500">
        <div className="text-center">
          <p className="mb-2">暂无执行计划</p>
          <button
            onClick={() => useAppStore.getState().setCurrentView('design')}
            className="text-sm helm-link"
          >
            去审批设计书
          </button>
        </div>
      </div>
    );
  }

  const planStatus = currentPlan.status;
  const terminal = isTerminalPlan(currentPlan);
  const allSteps = currentPlan.segments.flatMap((segment) => segment.steps);
  const inputTargetStepId = currentPlan.resume_from_step_id ?? '';
  // 回滚后 segments 仍显示逻辑步骤 ID，恢复断点却指向活动分支克隆的物理 ID。
  // 只有唯一候选时才回退到当前分支的投影，不能猜测要把输入写给哪一步。
  const inputCandidates = planStatus === 'Paused' && inputTargetStepId
    ? allSteps.filter((step) => step.status === 'Pending' &&
        step.outputs?.needs_input === true &&
        (step.step_id === inputTargetStepId ||
          step.branch_version === (currentPlan.current_branch ?? 1)))
    : [];
  const waitingInputStep = inputCandidates.find((step) => step.step_id === inputTargetStepId)
    ?? (inputCandidates.length === 1 ? inputCandidates[0] : undefined);
  const inputDraft = waitingInputStep &&
    stepInputDraft.planId === currentPlan.plan_id &&
    stepInputDraft.stepId === inputTargetStepId
      ? stepInputDraft.text : '';
  const totalSteps = allSteps.length;
  const completedSteps = allSteps.filter(
    (step) => step.status === 'Success' || step.status === 'Skipped',
  ).length;
  const failedSteps = allSteps.filter((step) => step.status === 'Failed').length;
  const progressPercent = totalSteps ? Math.round((completedSteps / totalSteps) * 100) : 0;
  const hasProgress = isExecuting || completedSteps > 0 || failedSteps > 0;

  const stepIcon = (status: StepStatus) => {
    if (status === 'Running' || status === 'Retry') {
      return <Loader2 className="h-4 w-4 animate-spin text-blue-300" aria-label="执行中" />;
    }
    if (status === 'Success') {
      return <CheckCircle className="h-4 w-4 text-emerald-300" aria-label="成功" />;
    }
    if (status === 'Failed') {
      return <XCircle className="h-4 w-4 text-red-300" aria-label="失败" />;
    }
    if (status === 'Skipped') {
      return <MinusCircle className="h-4 w-4 text-zinc-500" aria-label="已跳过" />;
    }
    if (status === 'RolledBack') {
      return <RotateCcw className="h-4 w-4 text-orange-300" aria-label="已回滚" />;
    }
    return <Clock className="h-4 w-4 text-zinc-600" aria-label="待执行" />;
  };

  const stepBorder = (status: StepStatus): string => ({
    Running: 'border-l-blue-400',
    Retry: 'border-l-blue-400',
    Success: 'border-l-emerald-400',
    Failed: 'border-l-red-400',
    Skipped: 'border-l-zinc-500',
    RolledBack: 'border-l-orange-400',
    Pending: 'border-l-zinc-700',
  })[status];

  const segmentStatus = (steps: Step[]) => {
    const statuses = steps.map((step) => step.status ?? 'Pending');
    if (statuses.some((status) => status === 'Failed')) return 'error';
    if (statuses.some((status) => status === 'Running' || status === 'Retry')) return 'running';
    if (statuses.every((status) => status === 'Success' || status === 'Skipped')) return 'success';
    if (statuses.some((status) => status === 'Success' || status === 'Skipped')) return 'partial';
    return 'pending';
  };

  const segmentIcon = (index: number, steps: Step[]) => {
    const status = segmentStatus(steps);
    if (status === 'running') return <Loader2 className="h-5 w-5 animate-spin text-blue-300" />;
    if (status === 'success') return <CheckCircle className="h-5 w-5 text-emerald-300" />;
    if (status === 'error') return <XCircle className="h-5 w-5 text-red-300" />;
    if (status === 'partial') {
      return (
        <span className="flex h-5 w-5 items-center justify-center rounded-full border-2 border-blue-300">
          <span className="h-2 w-2 rounded-full bg-blue-300" />
        </span>
      );
    }
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/[0.1] text-xs font-semibold text-zinc-300">
        {index + 1}
      </span>
    );
  };

  const handleExecute = async () => {
    setActionError(null);
    setIsExecuting(true);
    addLog({
      type: 'log',
      level: 'info',
      message: `${planStatus === 'Failed' ? '重试' : '开始'}执行计划: ${currentPlan.plan_id}`,
    });
    try {
      await apiClient.executePlan(currentPlan.plan_id);
      try {
        setCurrentPlan(await apiClient.getPlan(currentPlan.plan_id));
      } catch {
        // 轮询会继续尝试刷新权威状态。
      }
    } catch (error) {
      const message = `执行失败: ${error}`;
      setActionError(message);
      addLog({ type: 'log', level: 'error', message });
      setIsExecuting(false);
    }
  };

  const handlePause = async () => {
    setActionError(null);
    setControlInFlight('pause');
    addLog({ type: 'log', level: 'info', message: `发送暂停请求: ${currentPlan.plan_id}` });
    try {
      await apiClient.pausePlan(currentPlan.plan_id);
      addLog({ type: 'log', level: 'success', message: '暂停请求已发出，当前步骤完成后将暂停' });
    } catch (error) {
      const message = `暂停失败: ${error instanceof Error ? error.message : String(error)}`;
      setActionError(message);
      addLog({
        type: 'log',
        level: 'error',
        message,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const handleResume = async () => {
    setActionError(null);
    setControlInFlight('resume');
    addLog({ type: 'log', level: 'info', message: `发送恢复请求: ${currentPlan.plan_id}` });
    try {
      await apiClient.resumePlan(currentPlan.plan_id);
      setIsExecuting(true);
      addLog({ type: 'log', level: 'success', message: '恢复请求已发出，计划将从断点继续执行' });
    } catch (error) {
      const message = `恢复失败: ${error instanceof Error ? error.message : String(error)}`;
      setActionError(message);
      addLog({
        type: 'log',
        level: 'error',
        message,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const handleSupplyInput = async () => {
    const input = inputDraft.trim();
    if (!input || !inputTargetStepId) return;
    setActionError(null);
    setControlInFlight('input');
    try {
      await apiClient.supplyStepInput(currentPlan.plan_id, inputTargetStepId, input);
      setStepInputDraft({ planId: '', stepId: '', text: '' });
      addLog({ type: 'log', level: 'success', message: `步骤 ${inputTargetStepId} 已保存补充信息，等待手动恢复执行` });
      try {
        setCurrentPlan(await apiClient.getPlan(currentPlan.plan_id));
      } catch {
        addLog({ type: 'log', level: 'warning', message: '补充信息已保存，但刷新计划失败；可重新进入计划页查看状态' });
      }
    } catch (error) {
      const message = `保存补充信息失败: ${error instanceof Error ? error.message : String(error)}`;
      setActionError(message);
      addLog({ type: 'log', level: 'error', message });
    } finally {
      setControlInFlight(null);
    }
  };

  const handleSkip = async (step: Step) => {
    setActionError(null);
    setControlInFlight('skip');
    try {
      await apiClient.skipStep(currentPlan.plan_id, step.step_id);
      addLog({ type: 'log', level: 'info', message: `步骤 ${step.step_id} 已标记为跳过` });
    } catch (error) {
      const message = `跳过步骤失败: ${error instanceof Error ? error.message : String(error)}`;
      setActionError(message);
      addLog({
        type: 'log',
        level: 'error',
        message,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const handleReplan = async () => {
    setActionError(null);
    setControlInFlight('replan');
    addLog({ type: 'log', level: 'info', message: `对失败计划发起重规划: ${currentPlan.plan_id}` });
    try {
      const result = await apiClient.replanPlan(currentPlan.plan_id);
      addLog({
        type: 'log',
        level: 'success',
        message: `重规划完成：生成 ${result.repair_steps} 个修复步骤（新分支 #${result.new_branch}），可重新执行`,
      });
      try {
        setCurrentPlan(await apiClient.getPlan(currentPlan.plan_id));
      } catch {
        // 轮询会继续刷新权威状态。
      }
    } catch (error) {
      const message = `重规划失败: ${error instanceof Error ? error.message : String(error)}`;
      setActionError(message);
      addLog({ type: 'log', level: 'error', message });
    } finally {
      setControlInFlight(null);
    }
  };

  const confirmRollback = async () => {    if (!rollbackTarget) return;
    setActionError(null);
    setControlInFlight('rollback');
    try {
      const result = await apiClient.rollbackPlan(currentPlan.plan_id, rollbackTarget.step_id);
      addLog({
        type: 'log',
        level: 'success',
        message:
          `已回滚到步骤 ${result.resume_from}，新分支 #${result.new_branch}` +
          (result.marked_obsolete > 0 ? `，${result.marked_obsolete} 个步骤被标记为废弃` : ''),
      });
      setRollbackTarget(null);
      try {
        setCurrentPlan(await apiClient.getPlan(currentPlan.plan_id));
      } catch {
        // 回滚请求已成功，刷新失败时交给轮询恢复。
      }
    } catch (error) {
      const message = `回滚失败: ${error instanceof Error ? error.message : String(error)}`;
      setActionError(message);
      addLog({
        type: 'log',
        level: 'error',
        message,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const canSkip = (step: Step) => {
    const status = step.status ?? 'Pending';
    return !terminal && (status === 'Pending' || status === 'Retry');
  };

  const canRollback = (step: Step) => step.status === 'Success' || step.status === 'Failed';

  const stepDeps = (step: Step): Step[] =>
    (step.depends_on || [])
      .map((depId) => allSteps.find((s) => s.step_id === depId))
      .filter((s): s is Step => Boolean(s));
  const hasDependencies = allSteps.some((step) => (step.depends_on || []).length > 0);
  const stepDepsLabel = (step: Step): string =>
    stepDeps(step)
      .map((dep, index) => `${index + 1}. ${dep.description}`)
      .join('\n');
  const selectedStep = selectedStepId
    ? allSteps.find((s) => s.step_id === selectedStepId) ?? null
    : null;

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl space-y-5">
        <div className="sticky top-0 z-10 -mx-4 border-b border-white/[0.07] bg-[#0d0f13]/90 px-4 py-4 backdrop-blur-xl sm:-mx-8 sm:px-8">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="helm-chip rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.14em]">
                  执行计划
                </span>
                {planStatus && (
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${statusTone(planStatus)}`}>
                    {PLAN_STATUS_LABELS[planStatus] ?? planStatus}
                  </span>
                )}
              </div>
              <h2 className="truncate text-2xl font-semibold tracking-tight text-white">{currentPlan.plan_id}</h2>
              <p className="mt-1 text-xs text-zinc-500">
                {currentPlan.segments.length} 个阶段 · {totalSteps} 个步骤 · 分支 #{currentPlan.current_branch ?? 1}
              </p>
            </div>

            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {planStatus === 'Running' && (
                <button
                  onClick={() => void handlePause()}
                  disabled={controlInFlight !== null}
                  title="暂停执行"
                  className="inline-flex items-center gap-2 rounded-lg border border-amber-400/20 bg-amber-400/10 px-3.5 py-2 text-xs font-semibold text-amber-100 transition-colors hover:bg-amber-400/20 disabled:opacity-40"
                >
                  {controlInFlight === 'pause' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}
                  暂停
                </button>
              )}
              {planStatus === 'Paused' && (
                <button
                  onClick={() => void handleResume()}
                  disabled={controlInFlight !== null}
                  title="恢复执行"
                  className="helm-btn-primary inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-semibold disabled:opacity-40"
                >
                  {controlInFlight === 'resume' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                  恢复执行
                </button>
              )}
              {planStatus === 'Failed' && (
                <button
                  onClick={() => void handleReplan()}
                  disabled={isExecuting || controlInFlight !== null}
                  title="生成修复步骤并挂入新分支（动态重规划）"
                  className="inline-flex items-center gap-2 rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-3.5 py-2 text-xs font-semibold text-cyan-100 transition-colors hover:bg-cyan-400/20 disabled:opacity-40"
                >
                  {controlInFlight === 'replan' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  重规划
                </button>
              )}
              {planStatus !== 'Running' && planStatus !== 'Paused' && (
                <button
                  onClick={() => void handleExecute()}
                  disabled={isExecuting || controlInFlight !== null}
                  className="helm-btn-primary inline-flex items-center gap-2 rounded-lg px-3.5 py-2 text-xs font-semibold disabled:opacity-40"
                >
                  {isExecuting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                  {isExecuting ? '执行中...' : planStatus === 'Failed' ? '重试执行' : '开始执行'}
                </button>
              )}
            </div>
          </div>

          <div className="helm-plan-mini-metrics mt-4 grid max-w-md grid-cols-3 gap-2">
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2">
              <div className="text-[10px] text-zinc-600">完成数</div>
              <div className="mt-1 text-sm font-semibold text-emerald-300">{completedSteps}<span className="ml-1 text-[10px] font-normal text-zinc-600">/ {totalSteps}</span></div>
            </div>
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2">
              <div className="text-[10px] text-zinc-600">失败</div>
              <div className="mt-1 text-sm font-semibold text-red-300">{failedSteps}</div>
            </div>
            <div className="rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2">
              <div className="text-[10px] text-zinc-600">进度</div>
              <div className="mt-1 text-sm font-semibold text-white">{progressPercent}%</div>
              <span className="sr-only">{completedSteps}/{totalSteps} 步骤 ({progressPercent}%)</span>
            </div>
          </div>
          {hasProgress && (
            <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/[0.07]">
              <div
                className={`h-full rounded-full transition-all duration-500 ${failedSteps ? 'bg-red-400' : 'bg-gradient-to-r from-teal-600 to-emerald-300'}`}
                style={{ width: `${progressPercent}%` }}
              />
            </div>
          )}
          {actionError && <div role="alert" className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-red-400/20 bg-red-400/[0.08] px-3 py-2 text-xs text-red-200"><span>{actionError}</span><button type="button" onClick={() => setActionError(null)} aria-label="关闭错误提示" className="text-red-300 hover:text-white">×</button></div>}
        </div>

        {waitingInputStep && (
          <section aria-label="步骤等待补充输入" className="rounded-xl border border-amber-400/25 bg-amber-400/[0.06] p-4 sm:p-5">
            <h3 className="text-sm font-semibold text-amber-200">步骤需要补充输入 · {waitingInputStep.description}</h3>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm text-zinc-200">
              {typeof waitingInputStep.outputs?.question === 'string'
                ? waitingInputStep.outputs.question : '请补充当前步骤需要的信息。'}
            </p>
            <label htmlFor="step-extra-input" className="mt-4 block text-xs font-medium text-zinc-300">补充信息</label>
            <textarea
              id="step-extra-input"
              rows={3}
              value={inputDraft}
              onChange={(event) => setStepInputDraft({
                planId: currentPlan.plan_id, stepId: inputTargetStepId, text: event.target.value,
              })}
              disabled={controlInFlight !== null}
              className="helm-field mt-2 w-full resize-y rounded-lg border border-white/[0.12] bg-black/25 p-3 text-sm text-white outline-none disabled:opacity-40"
            />
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" onClick={() => void handleSupplyInput()}
                disabled={!inputDraft.trim() || controlInFlight !== null}
                className="helm-btn-primary rounded-lg px-4 py-2 text-xs font-semibold disabled:opacity-40">
                {controlInFlight === 'input' ? '保存中...' : '保存补充信息'}
              </button>
              <p className="text-xs text-zinc-400">保存后仍需点击上方「恢复执行」；不会自动执行步骤。</p>
            </div>
          </section>
        )}

        <div className="helm-plan-body">
          <div className="helm-plan-context"><WorktreePanel planId={currentPlan.plan_id} planStatus={planStatus} /></div>
          <div className="helm-plan-steps">
        {totalSteps === 0 ? (
          <div className="rounded-xl border border-dashed border-amber-400/20 bg-amber-400/[0.04] p-6 text-center text-sm text-zinc-400">这个计划还没有可执行步骤。</div>
        ) : viewMode === 'flow' ? (
          <div>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                <GitBranch className="h-3.5 w-3.5 helm-accent" />
                流程图 · 节点=步骤，箭头=依赖
                {hasDependencies ? '（可并行同层）' : '（按顺序）'}
              </div>
              <button type="button" onClick={() => setViewMode('list')} className="text-xs helm-link">
                阶段列表
              </button>
            </div>
            <PlanFlowGraph steps={allSteps} onSelectStep={setSelectedStepId} />
            {selectedStep && (
              <div className="mt-3 rounded-xl border border-white/[0.08] bg-[#111419]/90 p-4 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  {stepIcon(selectedStep.status ?? 'Pending')}
                  <span className="font-medium text-white">{selectedStep.description}</span>
                  <span className="helm-chip rounded-md px-1.5 py-0.5 text-[10px]">{selectedStep.tool}</span>
                </div>
                {(selectedStep.depends_on || []).length > 0 && (
                  <p className="mt-2 whitespace-pre-wrap text-xs text-zinc-500" title={stepDepsLabel(selectedStep)}>
                    依赖：{stepDeps(selectedStep).map((d) => d.description).join(' → ') || selectedStep.depends_on?.join(', ')}
                  </p>
                )}
              </div>
            )}
          </div>
        ) : (
          <div>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="text-xs text-zinc-500">阶段列表</div>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => setViewMode('flow')}
                  className="inline-flex items-center gap-1.5 text-xs helm-link"
                >
                  <GitBranch className="h-3.5 w-3.5" />
                  流程图
                </button>
                <button type="button" onClick={() => {
                  const next = !allExpanded;
                  setAllExpanded(next);
                  setExpandedSegments(next ? new Set(currentPlan.segments.map((segment) => segment.segment_id)) : new Set());
                }} className="text-xs helm-link">{allExpanded ? '全部收起' : '全部展开'}</button>
              </div>
            </div>
            <div className="relative space-y-3 pl-0 before:absolute before:bottom-5 before:left-[13px] before:top-5 before:hidden before:w-px before:bg-white/[0.08] sm:pl-7 sm:before:block">

          {currentPlan.segments.map((segment, segmentIndex) => {
            const expanded = expandedSegments.has(segment.segment_id);
            const state = segmentStatus(segment.steps);
            return (
              <div key={segment.segment_id} className="relative rounded-xl border border-white/[0.08] bg-[#111419]/90 shadow-xl shadow-black/5">
                <span className="absolute -left-7 top-5 hidden h-7 w-7 items-center justify-center rounded-full border border-white/[0.1] bg-[#111419] text-[11px] font-semibold text-zinc-400 sm:flex">
                  {segmentIndex + 1}
                </span>
                <button
                  onClick={() => setExpandedSegments((previous) => {
                    const next = new Set(previous);
                    if (next.has(segment.segment_id)) next.delete(segment.segment_id);
                    else next.add(segment.segment_id);
                    return next;
                  })}
                  className="flex w-full items-center justify-between gap-4 px-4 py-4 text-left transition-colors hover:bg-white/[0.025] sm:px-5"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    {segmentIcon(segmentIndex, segment.steps)}
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold text-white">{segment.description}</h3>
                      <p className="mt-1 text-xs text-zinc-600">
                        {segment.steps.length} 个步骤 · {state === 'success' ? '全部完成' : state === 'error' ? '需要关注' : state === 'running' ? '正在执行' : '等待执行'}
                      </p>
                    </div>
                  </div>
                  {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-zinc-500" /> : <ChevronRight className="h-4 w-4 shrink-0 text-zinc-500" />}
                </button>

                {expanded && (
                  <div className="space-y-2 border-t border-white/[0.07] px-3 pb-3 pt-3 sm:px-4">
                    {segment.steps.map((step, stepIndex) => {
                      const status = step.status ?? 'Pending';
                      return (
                        <div key={step.step_id} className={`rounded-xl border border-white/[0.07] border-l-2 bg-black/20 p-3.5 ${stepBorder(status)}`}>
                          <div className="flex items-start gap-3">
                            <span className="mt-0.5" title={`步骤状态: ${status}`}>{stepIcon(status)}</span>
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-start gap-2">
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-2">
                                    <span className="font-mono text-[10px] text-zinc-600">{String(stepIndex + 1).padStart(2, '0')}</span>
                                    <h4 className="text-sm font-medium text-zinc-100">{step.description}</h4>
                                  </div>
                                  <span className="helm-chip mt-1 inline-flex rounded-md px-2 py-0.5 text-[10px]">{step.tool}</span>
                                  {(step.depends_on || []).length > 0 && (
                                    <span
                                      title={stepDepsLabel(step)}
                                      className="mt-1 inline-flex items-center gap-1 rounded-md border border-sky-400/20 bg-sky-400/[0.08] px-2 py-0.5 text-[10px] text-sky-200"
                                    >
                                      <Link2 className="h-3 w-3" />
                                      依赖 {(step.depends_on || []).length} 步
                                    </span>
                                  )}
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                  {canSkip(step) && (
                                    <button onClick={() => { if (window.confirm('确定跳过此步骤？该步骤不会调用工具。')) void handleSkip(step); }} disabled={controlInFlight !== null} title="跳过" aria-label="跳过" className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-white/[0.08] hover:text-zinc-200 disabled:opacity-40">
                                      <SkipForward className="h-3.5 w-3.5" />
                                    </button>
                                  )}
                                  {canRollback(step) && (
                                    <button onClick={() => setRollbackTarget(step)} disabled={controlInFlight !== null} title="回滚到此步" aria-label="回滚到此步" className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-orange-400/10 hover:text-orange-300 disabled:opacity-40">
                                      <RotateCcw className="h-3.5 w-3.5" />
                                    </button>
                                  )}
                                </div>
                              </div>

                              {Object.keys(step.inputs).length > 0 && (
                                <div className="mt-3">
                                  <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-zinc-600">输入参数</div>
                                  <pre className="overflow-x-auto rounded-lg border border-white/[0.06] bg-black/20 p-2.5 font-mono text-[11px] leading-5 text-zinc-400">{JSON.stringify(step.inputs, null, 2)}</pre>
                                </div>
                              )}
                              {step.expected_outputs.length > 0 && (
                                <div className="mt-3">
                                  <div className="mb-1 text-[10px] uppercase tracking-[0.12em] text-zinc-600">预期输出</div>
                                  <div className="space-y-1">{step.expected_outputs.map((output, index) => <div key={index} className="text-xs text-zinc-400">• {output}</div>)}</div>
                                </div>
                              )}
                              {step.outputs?.acceptance && (
                                <div className="mt-3 rounded-lg border border-white/[0.07] bg-white/[0.025] p-3">
                                  <div className="flex items-center justify-between text-xs">
                                    <span className="font-medium text-zinc-400">验收证据</span>
                                    <span className={step.outputs.acceptance_status === 'passed' ? 'text-emerald-300' : 'text-red-300'}>
                                      {step.outputs.acceptance_status === 'passed' ? '通过' : '未通过'}{typeof step.outputs.reward === 'number' ? ` · ${step.outputs.reward}` : ''}
                                    </span>
                                  </div>
                                  {(step.outputs.acceptance.checks || []).map((check: any, index: number) => (
                                    <div key={index} className={`mt-1 text-xs ${check.passed ? 'text-emerald-300' : 'text-red-300'}`}>
                                      {check.passed ? '✓' : '✗'} {check.type}{check.reason ? `：${check.reason}` : ''}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
            </div>
          </div>
        )}
          </div>
        </div>
      </div>

      {rollbackTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-white/[0.1] bg-[#171a20] p-6 shadow-2xl">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-orange-400/10 text-orange-300"><RotateCcw className="h-4 w-4" /></div>
              <div><h3 className="text-base font-semibold text-white">确认回滚到此步骤</h3><p className="mt-0.5 text-xs text-zinc-500">这会创建一个新的执行分支</p></div>
            </div>
            <p className="mt-5 rounded-lg border border-white/[0.07] bg-black/15 p-3 text-sm text-zinc-300">{rollbackTarget.description}</p>
            <p className="mt-3 text-xs leading-5 text-zinc-500">回滚将创建一个新的执行分支，该步骤之后已执行的步骤将被标记为废弃，计划回到待执行状态后将从该步骤重新开始。</p>
            <div className="mt-6 flex gap-3">
              <button onClick={() => setRollbackTarget(null)} disabled={controlInFlight === 'rollback'} className="flex-1 rounded-lg border border-white/[0.1] bg-white/[0.04] px-4 py-2.5 text-sm text-zinc-300 transition-colors hover:bg-white/[0.08] disabled:opacity-40">取消</button>
              <button onClick={() => void confirmRollback()} disabled={controlInFlight === 'rollback'} className="flex-1 rounded-lg bg-orange-500 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-orange-400 disabled:opacity-40">{controlInFlight === 'rollback' ? <Loader2 className="mx-auto h-4 w-4 animate-spin" /> : '确认回滚'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
