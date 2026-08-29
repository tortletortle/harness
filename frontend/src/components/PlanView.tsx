/**
 * 执行计划查看页面
 *
 * 步骤状态来自后端权威数据（plan.status / step.status，由 usePlanPolling 轮询同步），
 * 不再从日志文本推断。
 */

import { useState, useMemo } from 'react';
import {
  Play, Pause, SkipForward, RotateCcw, Loader2, ChevronDown, ChevronRight, Search,
  CheckCircle, XCircle, Clock, MinusCircle,
} from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import { usePlanPolling } from '../hooks/usePlanPolling';
import type { ExecutionPlan, PlanStatus, Step, StepStatus } from '../types';

/** 计划终态集合 */
const TERMINAL_PLAN_STATUSES: PlanStatus[] = ['Completed', 'Failed', 'Paused', 'RolledBack'];

/** 计划状态中文标签 */
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

/** 控制请求类型（同一时刻只允许一个控制请求在途） */
type ControlAction = 'pause' | 'resume' | 'skip' | 'rollback' | null;

function isTerminalPlan(plan: ExecutionPlan): boolean {
  return plan.status ? TERMINAL_PLAN_STATUSES.includes(plan.status) : false;
}

export function PlanView() {
  const { currentPlan, isExecuting, setIsExecuting, setCurrentPlan, addLog, logs } = useAppStore();
  const [expandedSegments, setExpandedSegments] = useState<Set<string>>(new Set());
  const [logSearch, setLogSearch] = useState('');
  const [controlInFlight, setControlInFlight] = useState<ControlAction>(null);
  const [rollbackTarget, setRollbackTarget] = useState<Step | null>(null);

  // 权威状态轮询（Running/Pending 时每 2s 同步一次）
  usePlanPolling();

  const filteredLogs = useMemo(() => {
    if (!logSearch.trim()) return logs;
    const q = logSearch.toLowerCase();
    return logs.filter(l => l.message.toLowerCase().includes(q));
  }, [logs, logSearch]);

  if (!currentPlan) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500">
        <div className="text-center">
          <p className="mb-2">暂无执行计划</p>
          <button
            onClick={() => useAppStore.getState().setCurrentView('design')}
            className="text-blue-500 hover:text-blue-400 text-sm"
          >
            去审批设计书
          </button>
        </div>
      </div>
    );
  }

  const planStatus = currentPlan.status;
  const terminal = isTerminalPlan(currentPlan);

  // 汇总所有步骤，进度由服务端步骤状态推导（Skipped 计入已完成）
  const allSteps = currentPlan.segments.flatMap(seg => seg.steps);
  const totalSteps = allSteps.length;
  const completedSteps = allSteps.filter(s => s.status === 'Success' || s.status === 'Skipped').length;
  const failedSteps = allSteps.filter(s => s.status === 'Failed').length;
  const progressPercent = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;
  const hasProgress = isExecuting || completedSteps > 0 || failedSteps > 0;

  const getStepStatusIcon = (status: StepStatus) => {
    switch (status) {
      case 'Running':
      case 'Retry':
        return <Loader2 className="w-4 h-4 text-blue-400 animate-spin" aria-label="执行中" />;
      case 'Success':
        return <CheckCircle className="w-4 h-4 text-green-400" aria-label="成功" />;
      case 'Failed':
        return <XCircle className="w-4 h-4 text-red-400" aria-label="失败" />;
      case 'Skipped':
        return <MinusCircle className="w-4 h-4 text-zinc-500" aria-label="已跳过" />;
      case 'RolledBack':
        return <RotateCcw className="w-4 h-4 text-orange-400" aria-label="已回滚" />;
      default:
        return <Clock className="w-4 h-4 text-zinc-600" aria-label="待执行" />;
    }
  };

  const getStepStatusColor = (status: StepStatus) => {
    switch (status) {
      case 'Running':
      case 'Retry':
        return 'border-blue-500';
      case 'Success':
        return 'border-green-500';
      case 'Failed':
        return 'border-red-500';
      case 'Skipped':
        return 'border-zinc-500';
      case 'RolledBack':
        return 'border-orange-500';
      default:
        return 'border-zinc-700';
    }
  };

  const getSegmentStatus = (segment: { steps: Step[] }) => {
    const statuses = segment.steps.map(s => s.status ?? 'Pending');

    if (statuses.some(s => s === 'Failed')) return 'error';
    if (statuses.some(s => s === 'Running' || s === 'Retry')) return 'running';
    if (statuses.every(s => s === 'Success' || s === 'Skipped')) return 'success';
    if (statuses.some(s => s === 'Success' || s === 'Skipped')) return 'partial';
    return 'pending';
  };

  const getSegmentIcon = (segmentId: string, steps: Step[]) => {
    const status = getSegmentStatus({ steps });
    switch (status) {
      case 'running':
        return <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />;
      case 'success':
        return <CheckCircle className="w-5 h-5 text-green-400" />;
      case 'error':
        return <XCircle className="w-5 h-5 text-red-400" />;
      case 'partial':
        return (
          <div className="w-5 h-5 rounded-full border-2 border-blue-400 flex items-center justify-center">
            <div className="w-2 h-2 rounded-full bg-blue-400" />
          </div>
        );
      default:
        return (
          <span className="flex items-center justify-center w-5 h-5 rounded-full bg-zinc-700 text-white text-xs font-bold">
            {currentPlan.segments.findIndex(s => s.segment_id === segmentId) + 1}
          </span>
        );
    }
  };

  const toggleSegment = (segmentId: string) => {
    const newExpanded = new Set(expandedSegments);
    if (newExpanded.has(segmentId)) {
      newExpanded.delete(segmentId);
    } else {
      newExpanded.add(segmentId);
    }
    setExpandedSegments(newExpanded);
  };

  const handleExecute = async () => {
    setIsExecuting(true);
    addLog({
      type: 'log',
      level: 'info',
      message: `开始执行计划: ${currentPlan.plan_id}`,
    });

    try {
      await apiClient.executePlan(currentPlan.plan_id);
      // 立即刷新一次计划状态，后续更新由 usePlanPolling 接管
      try {
        const latest = await apiClient.getPlan(currentPlan.plan_id);
        if (useAppStore.getState().currentPlan?.plan_id === currentPlan.plan_id) {
          setCurrentPlan(latest);
        }
      } catch {
        // 刷新失败忽略，轮询会继续重试
      }
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `执行失败: ${error}`,
      });
      setIsExecuting(false);
    }
  };

  const handlePause = async () => {
    if (!currentPlan) return;
    setControlInFlight('pause');
    addLog({
      type: 'log',
      level: 'info',
      message: `发送暂停请求: ${currentPlan.plan_id}`,
    });

    try {
      await apiClient.pausePlan(currentPlan.plan_id);
      addLog({
        type: 'log',
        level: 'success',
        message: '暂停请求已发出，当前步骤完成后将暂停',
      });
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `暂停失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const handleResume = async () => {
    if (!currentPlan) return;
    setControlInFlight('resume');
    addLog({
      type: 'log',
      level: 'info',
      message: `发送恢复请求: ${currentPlan.plan_id}`,
    });

    try {
      await apiClient.resumePlan(currentPlan.plan_id);
      // Paused 被轮询视为终态而停止轮询；恢复成功后必须重新拉起轮询，
      // 否则后端已在执行、界面却永远停留在"已暂停"。
      setIsExecuting(true);
      addLog({
        type: 'log',
        level: 'success',
        message: '恢复请求已发出，计划将从断点继续执行',
      });
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `恢复失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const handleSkipStep = async (step: Step) => {
    if (!currentPlan) return;
    setControlInFlight('skip');

    try {
      await apiClient.skipStep(currentPlan.plan_id, step.step_id);
      addLog({
        type: 'log',
        level: 'info',
        message: `步骤 ${step.step_id} 已标记为跳过`,
      });
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `跳过步骤失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  const confirmRollback = async () => {
    if (!currentPlan || !rollbackTarget) return;
    const target = rollbackTarget;
    setControlInFlight('rollback');

    try {
      const result = await apiClient.rollbackPlan(currentPlan.plan_id, target.step_id);
      addLog({
        type: 'log',
        level: 'success',
        message:
          `已回滚到步骤 ${result.resume_from}，新分支 #${result.new_branch}` +
          (result.marked_obsolete > 0 ? `，${result.marked_obsolete} 个步骤被标记为废弃` : ''),
      });
      setRollbackTarget(null);

      // 立即刷新计划（回滚后计划回到 Pending）
      try {
        const latest = await apiClient.getPlan(currentPlan.plan_id);
        if (useAppStore.getState().currentPlan?.plan_id === currentPlan.plan_id) {
          setCurrentPlan(latest);
        }
      } catch {
        // 刷新失败忽略
      }
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `回滚失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setControlInFlight(null);
    }
  };

  // 步骤级操作可见性
  const canSkipStep = (step: Step) => {
    const status = step.status ?? 'Pending';
    return !terminal && (status === 'Pending' || status === 'Retry');
  };
  const canRollbackToStep = (step: Step) => {
    const status = step.status ?? 'Pending';
    return status === 'Success' || status === 'Failed';
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Plan content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          {/* Header */}
          <div className="flex items-center justify-between gap-6">
            <div className="flex-1 min-w-0">
              <h2 className="text-2xl font-bold text-white mb-2">执行计划</h2>
              <p className="text-sm text-zinc-400">
                ID: {currentPlan.plan_id} · {currentPlan.segments.length} 个阶段 · {totalSteps} 个步骤
                {planStatus && (
                  <>
                    {' · 状态: '}
                    <span className={
                      planStatus === 'Running' ? 'text-blue-400' :
                      planStatus === 'Failed' ? 'text-red-400' :
                      planStatus === 'Completed' ? 'text-green-400' :
                      'text-zinc-300'
                    }>
                      {PLAN_STATUS_LABELS[planStatus] ?? planStatus}
                    </span>
                  </>
                )}
              </p>

              {/* 进度条 */}
              {hasProgress && (
                <div className="mt-3">
                  <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
                    <span>执行进度</span>
                    <span>{completedSteps}/{totalSteps} 步骤 ({progressPercent}%)</span>
                  </div>
                  <div className="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gradient-to-r from-blue-600 to-blue-400 transition-all duration-300"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                  {failedSteps > 0 && (
                    <div className="text-xs text-red-400 mt-1">
                      {failedSteps} 个步骤失败
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Header actions */}
            <div className="flex items-center gap-3 shrink-0">
              {planStatus === 'Running' && (
                <button
                  onClick={handlePause}
                  disabled={controlInFlight !== null}
                  title="暂停执行"
                  className="flex items-center gap-2 bg-amber-600 hover:bg-amber-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium px-6 py-3 rounded-lg transition-colors"
                >
                  {controlInFlight === 'pause' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Pause className="w-4 h-4" />
                  )}
                  <span>暂停</span>
                </button>
              )}

              {planStatus === 'Paused' && (
                <button
                  onClick={handleResume}
                  disabled={controlInFlight !== null}
                  title="恢复执行"
                  className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium px-6 py-3 rounded-lg transition-colors"
                >
                  {controlInFlight === 'resume' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Play className="w-4 h-4" />
                  )}
                  <span>恢复</span>
                </button>
              )}

              {planStatus !== 'Running' && planStatus !== 'Paused' && (
                <button
                  onClick={handleExecute}
                  disabled={isExecuting || controlInFlight !== null}
                  className="flex items-center gap-2 bg-green-600 hover:bg-green-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium px-6 py-3 rounded-lg transition-colors"
                >
                  {isExecuting ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>执行中...</span>
                    </>
                  ) : (
                    <>
                      <Play className="w-4 h-4" />
                      <span>开始执行</span>
                    </>
                  )}
                </button>
              )}
            </div>
          </div>

          {/* Segments */}
          <div className="space-y-4">
            {currentPlan.segments.map((segment) => (
              <div key={segment.segment_id} className="bg-zinc-900 border border-zinc-800 rounded-lg">
                {/* Segment Header */}
                <button
                  onClick={() => toggleSegment(segment.segment_id)}
                  className="w-full flex items-center justify-between p-4 hover:bg-zinc-800/50 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    {getSegmentIcon(segment.segment_id, segment.steps)}
                    <div className="text-left">
                      <h3 className="text-sm font-medium text-white">{segment.description}</h3>
                      <p className="text-xs text-zinc-500">{segment.steps.length} 个步骤</p>
                    </div>
                  </div>
                  {expandedSegments.has(segment.segment_id) ? (
                    <ChevronDown className="w-4 h-4 text-zinc-400" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-zinc-400" />
                  )}
                </button>

                {/* Steps */}
                {expandedSegments.has(segment.segment_id) && (
                  <div className="px-4 pb-4 space-y-2">
                    {segment.steps.map((step) => {
                      const status: StepStatus = step.status ?? 'Pending';
                      return (
                        <div key={step.step_id} className={`bg-zinc-950 rounded p-3 border-l-2 ${getStepStatusColor(status)}`}>
                          <div className="flex items-start gap-3">
                            <span title={`步骤状态: ${status}`}>{getStepStatusIcon(status)}</span>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2 mb-2">
                                <h4 className="text-sm font-medium text-white">{step.description}</h4>
                                <span className="text-xs px-2 py-0.5 bg-blue-600/20 text-blue-400 rounded">
                                  {step.tool}
                                </span>

                                {/* Per-step actions */}
                                <div className="ml-auto flex items-center gap-1">
                                  {canSkipStep(step) && (
                                    <button
                                      onClick={() => handleSkipStep(step)}
                                      disabled={controlInFlight !== null}
                                      title="跳过"
                                      aria-label="跳过"
                                      className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                                    >
                                      <SkipForward className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                  {canRollbackToStep(step) && (
                                    <button
                                      onClick={() => setRollbackTarget(step)}
                                      disabled={controlInFlight !== null}
                                      title="回滚到此步"
                                      aria-label="回滚到此步"
                                      className="p-1.5 rounded hover:bg-zinc-800 text-zinc-400 hover:text-orange-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                                    >
                                      <RotateCcw className="w-3.5 h-3.5" />
                                    </button>
                                  )}
                                </div>
                              </div>

                              {/* Inputs */}
                              {Object.keys(step.inputs).length > 0 && (
                                <div className="mb-2">
                                  <div className="text-xs text-zinc-500 mb-1">输入参数:</div>
                                  <div className="bg-zinc-900 rounded p-2 font-mono text-xs text-zinc-300">
                                    {JSON.stringify(step.inputs, null, 2)}
                                  </div>
                                </div>
                              )}

                              {/* Expected Outputs */}
                              {step.expected_outputs.length > 0 && (
                                <div>
                                  <div className="text-xs text-zinc-500 mb-1">预期输出:</div>
                                  <div className="space-y-0.5">
                                    {step.expected_outputs.map((output, i) => (
                                      <div key={i} className="text-xs text-zinc-400">• {output}</div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Deterministic acceptance evidence */}
                              {step.outputs?.acceptance && (
                                <div className="mt-2 rounded border border-zinc-800 bg-zinc-900/60 p-2">
                                  <div className="flex items-center justify-between text-xs">
                                    <span className="text-zinc-500">验收结果</span>
                                    <span className={step.outputs.acceptance_status === 'passed' ? 'text-emerald-400' : 'text-red-400'}>
                                      {step.outputs.acceptance_status === 'passed' ? '通过' : '未通过'}
                                      {typeof step.outputs.reward === 'number' ? ` · ${step.outputs.reward}` : ''}
                                    </span>
                                  </div>
                                  <div className="mt-1 space-y-0.5">
                                    {(step.outputs.acceptance.checks || []).map((check: any, i: number) => (
                                      <div key={i} className={`text-xs ${check.passed ? 'text-emerald-400' : 'text-red-400'}`}>
                                        {check.passed ? '✓' : '✗'} {check.type}{check.reason ? `：${check.reason}` : ''}
                                      </div>
                                    ))}
                                  </div>
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
            ))}
          </div>
        </div>
      </div>

      {/* 回滚确认对话框 */}
      {rollbackTarget && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-6 max-w-md w-full mx-4">
            <div className="flex items-center gap-2 mb-4">
              <RotateCcw className="w-5 h-5 text-orange-400" />
              <h3 className="text-lg font-medium text-white">确认回滚到此步骤</h3>
            </div>
            <p className="text-sm text-zinc-300 mb-2">
              {rollbackTarget.description}
            </p>
            <p className="text-sm text-zinc-400 mb-4">
              回滚将创建一个新的执行分支，该步骤之后已执行的步骤将被标记为废弃，计划回到待执行状态后将从该步骤重新开始。
            </p>
            <div className="flex gap-3 mt-4">
              <button
                onClick={() => setRollbackTarget(null)}
                disabled={controlInFlight === 'rollback'}
                className="flex-1 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white rounded transition-colors"
              >
                取消
              </button>
              <button
                onClick={confirmRollback}
                disabled={controlInFlight === 'rollback'}
                className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white rounded transition-colors flex items-center justify-center gap-2"
              >
                {controlInFlight === 'rollback' ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  '确认回滚'
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Log search panel */}
      <div className="h-48 border-t border-zinc-800 bg-zinc-950 flex flex-col">
        <div className="flex items-center gap-2 px-4 py-2 border-b border-zinc-800">
          <Search className="w-4 h-4 text-zinc-500" />
          <input
            type="text"
            value={logSearch}
            onChange={(e) => setLogSearch(e.target.value)}
            placeholder="搜索执行日志..."
            className="flex-1 bg-transparent text-sm text-white placeholder-zinc-600 focus:outline-none"
          />
          <span className="text-xs text-zinc-500">{filteredLogs.length} 条</span>
        </div>
        <div className="flex-1 overflow-y-auto p-2 font-mono text-xs">
          {filteredLogs.length === 0 ? (
            <div className="text-zinc-600 text-center py-4">暂无日志</div>
          ) : (
            filteredLogs.map((log, index) => (
              <div key={index} className="mb-1">
                <span className="text-zinc-600">
                  {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}
                </span>
                {' '}
                <span className={
                  log.level === 'success' ? 'text-green-400' :
                  log.level === 'error' ? 'text-red-400' :
                  log.level === 'warning' ? 'text-yellow-400' :
                  'text-zinc-400'
                }>
                  [{log.level.toUpperCase()}]
                </span>
                {' '}
                <span className="text-zinc-300">{log.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
