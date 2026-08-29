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
import type { ExecutionPlan } from '../types';

type AttachKind = 'executing' | 'latest';

export function ExecutionView() {
  const { setCurrentView } = useAppStore();
  const [resolving, setResolving] = useState(true);
  const [attachedPlan, setAttachedPlan] = useState<ExecutionPlan | null>(null);
  const [attachKind, setAttachKind] = useState<AttachKind>('latest');

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
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-zinc-600 animate-spin" />
      </div>
    );
  }

  if (!attachedPlan) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500">
        <div className="text-center">
          <Activity className="w-12 h-12 mx-auto mb-3 opacity-50" />
          <p className="mb-4">暂无执行任务</p>
          <button
            onClick={() => setCurrentView('input')}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
          >
            去新建任务
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div
        className={`px-6 py-2 text-xs flex items-center gap-2 border-b ${
          attachKind === 'executing'
            ? 'bg-blue-950/40 border-blue-900 text-blue-300'
            : 'bg-zinc-900/60 border-zinc-800 text-zinc-400'
        }`}
      >
        {attachKind === 'executing' ? (
          <>
            <Play className="w-3.5 h-3.5" />
            <span>已附着执行中的计划: {attachedPlan.plan_id.slice(0, 8)}</span>
          </>
        ) : (
          <>
            <Activity className="w-3.5 h-3.5" />
            <span>显示最近的计划: {attachedPlan.plan_id.slice(0, 8)}</span>
          </>
        )}
      </div>
      <PlanView />
    </div>
  );
}
