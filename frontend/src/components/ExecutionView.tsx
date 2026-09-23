/**
 * 独立执行页面
 *
 * 解析当前应展示的计划（执行中 > 最近创建），随后复用 PlanView 的
 * 完整渲染与控制能力——状态轮询、暂停/恢复/跳过/回滚全部由 PlanView
 * 自带，这里只负责"决定显示哪个计划"。
 */

import { useEffect, useState } from 'react';
import { Activity, Loader2, Play } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import { PlanView } from './PlanView';
import type { ExecutionPlan, ExecutionRun } from '../types';

type AttachKind = 'executing' | 'latest';

export function ExecutionView() {
  const { setCurrentView } = useAppStore();
  const [resolving, setResolving] = useState(true);
  const [attachedPlan, setAttachedPlan] = useState<ExecutionPlan | null>(null);
  const [attachKind, setAttachKind] = useState<AttachKind>('latest');
  const [runHistory, setRunHistory] = useState<ExecutionRun[]>([]);
  const [attemptCount, setAttemptCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const resolve = async () => {
      setResolving(true);
      try {
        const status = await apiClient.getServiceStatus();
        let planId = status.current_execution;
        let kind: AttachKind = 'executing';

        if (!planId) {
          const plans = await apiClient.getPlans(1);
          planId = plans[0]?.plan_id ?? null;
          kind = 'latest';
        }

        if (cancelled) return;
        if (planId) {
          const plan = await apiClient.getPlan(planId);
          if (cancelled) return;
          useAppStore.getState().setCurrentPlan(plan);
          setAttachKind(kind);
          setAttachedPlan(plan);
          try {
            const runs = await apiClient.getPlanRuns(plan.plan_id, 5);
            if (!cancelled) {
              setRunHistory(runs);
              if (runs[0]) {
                const attempts = await apiClient.getRunAttempts(runs[0].run_id, 100);
                if (!cancelled) setAttemptCount(attempts.length);
              }
            }
          } catch (historyError) {
            useAppStore.getState().addLog({
              type: 'log',
              level: 'warning',
              message: `加载运行历史失败: ${historyError instanceof Error ? historyError.message : String(historyError)}`,
            });
          }
        } else {
          setAttachedPlan(null);
        }
      } catch (error) {
        if (cancelled) return;
        useAppStore.getState().addLog({
          type: 'log',
          level: 'error',
          message: `解析执行目标失败: ${error instanceof Error ? error.message : String(error)}`,
        });
        setAttachedPlan(null);
      } finally {
        if (!cancelled) setResolving(false);
      }
    };

    void resolve();
    return () => {
      cancelled = true;
    };
  }, []);

  if (resolving) {
    return (
      <div className="flex min-h-full items-center justify-center">
        <div className="flex items-center gap-3 text-xs text-zinc-500"><Loader2 className="h-4 w-4 animate-spin text-violet-300" />正在连接执行状态…</div>
      </div>
    );
  }

  if (!attachedPlan) {
    return (
      <div className="flex min-h-full items-center justify-center px-4 text-zinc-500">
        <div className="w-full max-w-md rounded-2xl border border-dashed border-white/[0.12] bg-white/[0.025] p-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-500/10 text-violet-300"><Activity className="h-5 w-5" /></div>
          <h2 className="text-base font-semibold text-white">暂无执行任务</h2>
          <p className="mt-2 text-xs leading-5 text-zinc-500">创建并批准一个设计书后，执行状态会实时显示在这里。</p>
          <button onClick={() => setCurrentView('input')} className="mt-5 inline-flex items-center gap-2 rounded-lg bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-violet-500">去新建任务</button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-full">
      <div className={`flex items-center gap-2 border-b px-4 py-2.5 text-xs sm:px-8 ${attachKind === 'executing' ? 'border-blue-400/15 bg-blue-400/[0.06] text-blue-200' : 'border-white/[0.07] bg-white/[0.025] text-zinc-500'}`}>
        {attachKind === 'executing' ? <Play className="h-3.5 w-3.5" /> : <Activity className="h-3.5 w-3.5" />}
        <span>{attachKind === 'executing' ? '已附着执行中的计划' : '显示最近的计划'} · <span className="font-mono">{attachedPlan.plan_id.slice(0, 8)}</span></span>
      </div>
      <PlanView />
      {runHistory.length > 0 && (
        <section className="mx-4 mb-6 rounded-xl border border-white/[0.08] bg-white/[0.025] p-4 sm:mx-8">
          <div className="flex items-center justify-between">
            <div><h3 className="text-sm font-semibold text-white">运行观测</h3><p className="mt-1 text-xs text-zinc-500">最近 {runHistory.length} 次运行 · 最新运行 {attemptCount} 次步骤尝试</p></div>
            <span className="rounded-full border border-violet-400/20 bg-violet-400/10 px-2 py-1 text-[10px] text-violet-200">Run history</span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{runHistory.slice(0, 3).map((run) => <div key={run.run_id} className="rounded-lg border border-white/[0.07] bg-black/15 p-3"><div className="flex items-center justify-between"><span className="font-mono text-[10px] text-zinc-500">{run.run_id.slice(0, 8)}</span><span className="text-[10px] text-zinc-400">{run.status}</span></div><div className="mt-2 text-[10px] text-zinc-600">触发：{run.trigger} · 分支 #{run.branch_version}</div>{run.failure_reason && <div className="mt-1 truncate text-[10px] text-red-300" title={run.failure_reason}>{run.failure_reason}</div>}</div>)}</div>
        </section>
      )}
    </div>
  );
}
