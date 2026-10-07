/**
 * 历史记录页面：浏览设计书与执行计划，并支持重命名 / 归档 / 删除
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { KeyboardEvent, MouseEvent } from 'react';
import { Archive, ArchiveRestore, Check, Clock, FileText, Loader2, Pencil, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { Design } from '../types';
import type { PlanSummary } from '../types/system';

const STATUS_LABELS: Record<string, string> = {
  Draft: '草稿', Pending: '待执行', Approved: '已批准', Running: '执行中',
  Paused: '已暂停', Completed: '已完成', Failed: '已失败', RolledBack: '已回滚',
};

const statusTone = (status: string) => {
  switch (status) {
    case 'Completed': return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200';
    case 'Failed': return 'border-red-400/25 bg-red-400/10 text-red-200';
    case 'Running': return 'helm-chip';
    case 'Paused': return 'border-amber-400/25 bg-amber-400/10 text-amber-200';
    default: return 'border-white/[0.1] bg-white/[0.04] text-zinc-400';
  }
};

const iconBtn = 'rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-white/[0.08] hover:text-white disabled:cursor-not-allowed disabled:opacity-40';

export function HistoryView() {
  const [designs, setDesigns] = useState<Design[]>([]);
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'designs' | 'plans'>('designs');
  const [searchQuery, setSearchQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ planId: string; name: string } | null>(null);
  const { setCurrentDesign, setCurrentPlan, setCurrentView, addLog } = useAppStore();

  const loadHistory = useCallback(async () => {
    setIsLoading(true);
    try {
      const [designsList, plansList] = await Promise.all([
        apiClient.getDesigns(50),
        apiClient.getPlanSummaries(100),
      ]);
      setDesigns(designsList);
      setPlans(plansList);
    } catch (error) {
      addLog({ type: 'log', level: 'error', message: `加载历史记录失败: ${error instanceof Error ? error.message : String(error)}` });
    } finally {
      setIsLoading(false);
    }
  }, [addLog]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const planCountByDesign = useMemo(() => {
    const map = new Map<string, number>();
    plans.forEach((p) => map.set(p.design_id, (map.get(p.design_id) ?? 0) + 1));
    return map;
  }, [plans]);

  const filteredDesigns = useMemo(() => {
    if (!searchQuery.trim()) return designs;
    const q = searchQuery.toLowerCase();
    return designs.filter((d) => d.user_request.toLowerCase().includes(q) || d.design_id.toLowerCase().includes(q));
  }, [designs, searchQuery]);

  const archivedCount = plans.filter((p) => p.archived).length;
  const filteredPlans = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return plans
      .filter((p) => showArchived || !p.archived)
      .filter((p) => !q || p.plan_id.toLowerCase().includes(q) || p.design_id.toLowerCase().includes(q) || p.display_name.toLowerCase().includes(q));
  }, [plans, searchQuery, showArchived]);

  const fail = (action: string, error: unknown) => {
    addLog({ type: 'log', level: 'error', message: `${action}: ${error instanceof Error ? error.message : String(error)}` });
  };

  const handleSelectDesign = async (design: Design) => {
    setBusyId(design.design_id);
    try {
      // 列表接口只返回摘要，打开前拉取完整设计书
      setCurrentDesign(await apiClient.getDesign(design.design_id));
      setCurrentView('design');
    } catch (error) {
      fail('打开设计书失败', error);
    } finally {
      setBusyId(null);
    }
  };

  const handleSelectPlan = async (plan: PlanSummary) => {
    setBusyId(plan.plan_id);
    try {
      setCurrentPlan(await apiClient.getPlan(plan.plan_id));
      setCurrentView('plan');
    } catch (error) {
      fail('打开执行计划失败', error);
    } finally {
      setBusyId(null);
    }
  };

  const stop = (event: MouseEvent) => event.stopPropagation();
  const rowKey = (action: () => void) => (event: KeyboardEvent) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); action(); }
  };

  const handleDeleteDesign = async (design: Design) => {
    if (!window.confirm(`确定删除这份设计书？\n\n${design.user_request.slice(0, 80)}\n\n删除后无法恢复。`)) return;
    setBusyId(design.design_id);
    try {
      await apiClient.deleteDesign(design.design_id);
      setDesigns((list) => list.filter((d) => d.design_id !== design.design_id));
      addLog({ type: 'log', level: 'success', message: `已删除设计书 ${design.design_id.slice(0, 8)}` });
    } catch (error) {
      fail('删除设计书失败', error);
    } finally {
      setBusyId(null);
    }
  };

  const submitRename = async () => {
    if (!renaming) return;
    const name = renaming.name.trim();
    if (!name) { setRenaming(null); return; }
    setBusyId(renaming.planId);
    try {
      await apiClient.renamePlan(renaming.planId, name);
      setPlans((list) => list.map((p) => (p.plan_id === renaming.planId ? { ...p, display_name: name } : p)));
      setRenaming(null);
    } catch (error) {
      fail('重命名失败', error);
    } finally {
      setBusyId(null);
    }
  };

  const handleArchive = async (plan: PlanSummary) => {
    setBusyId(plan.plan_id);
    try {
      await apiClient.archivePlan(plan.plan_id, !plan.archived);
      setPlans((list) => list.map((p) => (p.plan_id === plan.plan_id ? { ...p, archived: !plan.archived } : p)));
      addLog({ type: 'log', level: 'info', message: `${plan.archived ? '已取消归档' : '已归档'}计划 ${plan.display_name || plan.plan_id.slice(0, 8)}` });
    } catch (error) {
      fail(plan.archived ? '取消归档失败' : '归档失败', error);
    } finally {
      setBusyId(null);
    }
  };

  const handleDeletePlan = async (plan: PlanSummary) => {
    if (!window.confirm(`确定删除执行计划「${plan.display_name || plan.plan_id.slice(0, 8)}」？\n\n会同时删除全部步骤和事件记录（审计日志保留），无法恢复。`)) return;
    setBusyId(plan.plan_id);
    try {
      await apiClient.deletePlan(plan.plan_id);
      setPlans((list) => list.filter((p) => p.plan_id !== plan.plan_id));
      const current = useAppStore.getState().currentPlan;
      if (current?.plan_id === plan.plan_id) setCurrentPlan(null);
      addLog({ type: 'log', level: 'success', message: `已删除执行计划 ${plan.plan_id.slice(0, 8)}` });
    } catch (error) {
      fail('删除计划失败', error);
    } finally {
      setBusyId(null);
    }
  };

  if (isLoading && designs.length === 0 && plans.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center py-24">
        <Loader2 className="h-8 w-8 animate-spin text-zinc-600" />
      </div>
    );
  }

  const tabClass = (active: boolean) =>
    `px-4 py-2 text-sm font-medium border-b-2 transition-colors ${active ? 'helm-tab-active' : 'border-transparent text-zinc-400 hover:text-white'}`;
  const rowClass = 'helm-hover-brand group flex w-full cursor-pointer items-start gap-3 rounded-xl border border-white/[0.08] bg-[#111419]/90 p-4 text-left hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--app-brand)]';

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-white">历史记录</h2>
            <p className="mt-1 text-sm text-zinc-500">查看过往的设计书和执行计划，重命名、归档或清理不再需要的记录</p>
          </div>
          <button type="button" onClick={() => void loadHistory()} disabled={isLoading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.1] bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-white/[0.08] disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? 'animate-spin' : ''}`} />刷新
          </button>
        </div>

        <div className="mb-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索设计书或执行计划（需求、名称、ID）..."
              className="helm-field w-full rounded-xl border border-white/[0.1] bg-[#111419]/80 py-2.5 pl-10 pr-4 text-sm text-white placeholder:text-zinc-600 focus:outline-none"
            />
          </div>
        </div>

        <div className="mb-6 flex items-end justify-between gap-2 border-b border-zinc-800">
          <div className="flex gap-2">
            <button type="button" onClick={() => setActiveTab('designs')} className={tabClass(activeTab === 'designs')}>
              设计书 ({filteredDesigns.length})
            </button>
            <button type="button" onClick={() => setActiveTab('plans')} className={tabClass(activeTab === 'plans')}>
              执行计划 ({filteredPlans.length})
            </button>
          </div>
          {activeTab === 'plans' && (
            <label className="mb-2 flex cursor-pointer items-center gap-2 text-xs text-zinc-400">
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} className="accent-[var(--app-brand)]" />
              显示已归档{archivedCount > 0 && `（${archivedCount}）`}
            </label>
          )}
        </div>

        {activeTab === 'designs' && (
          <div className="space-y-3">
            {filteredDesigns.length === 0 ? (
              <div className="py-12 text-center text-zinc-500">
                <FileText className="mx-auto mb-3 h-12 w-12 opacity-50" />
                <p>{searchQuery ? '无匹配结果' : '暂无历史设计书'}</p>
              </div>
            ) : (
              filteredDesigns.map((design) => {
                const linkedPlans = planCountByDesign.get(design.design_id) ?? 0;
                const busy = busyId === design.design_id;
                return (
                  <div key={design.design_id} role="button" tabIndex={0}
                    onClick={() => void handleSelectDesign(design)} onKeyDown={rowKey(() => void handleSelectDesign(design))}
                    className={rowClass}>
                    <div className="min-w-0 flex-1">
                      <div className="mb-2 flex items-start justify-between gap-2">
                        <h3 className="line-clamp-1 text-sm font-medium text-white">{design.user_request}</h3>
                        {design.created_at && (
                          <span className="ml-2 flex-shrink-0 text-xs text-zinc-500">{new Date(design.created_at).toLocaleDateString()}</span>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="font-mono text-xs text-zinc-600">{design.design_id}</code>
                        {linkedPlans > 0 && <span className="helm-chip rounded px-2 py-0.5 text-[11px]">已生成 {linkedPlans} 个计划</span>}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1" onClick={stop}>
                      {busy && <Loader2 className="h-4 w-4 animate-spin helm-accent" />}
                      <button type="button" disabled={busy || linkedPlans > 0}
                        onClick={() => void handleDeleteDesign(design)}
                        title={linkedPlans > 0 ? '已生成执行计划，需先删除对应计划' : '删除设计书'}
                        aria-label="删除设计书" className={`${iconBtn} hover:text-red-300`}>
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}

        {activeTab === 'plans' && (
          <div className="space-y-3">
            {filteredPlans.length === 0 ? (
              <div className="py-12 text-center text-zinc-500">
                <Clock className="mx-auto mb-3 h-12 w-12 opacity-50" />
                <p>{searchQuery ? '无匹配结果' : archivedCount > 0 && !showArchived ? '没有未归档的计划' : '暂无历史执行计划'}</p>
              </div>
            ) : (
              filteredPlans.map((plan) => {
                const busy = busyId === plan.plan_id;
                const isRenaming = renaming?.planId === plan.plan_id;
                const running = plan.status === 'Running' || plan.status === 'Paused';
                return (
                  <div key={plan.plan_id} role="button" tabIndex={0}
                    onClick={() => { if (!isRenaming) void handleSelectPlan(plan); }}
                    onKeyDown={rowKey(() => { if (!isRenaming) void handleSelectPlan(plan); })}
                    className={`${rowClass} ${plan.archived ? 'opacity-60' : ''}`}>
                    <div className="min-w-0 flex-1">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        {isRenaming ? (
                          <div className="flex items-center gap-1" onClick={stop}>
                            <input autoFocus value={renaming.name} maxLength={100}
                              onChange={(e) => setRenaming({ planId: plan.plan_id, name: e.target.value })}
                              onKeyDown={(e) => { if (e.key === 'Enter') void submitRename(); if (e.key === 'Escape') setRenaming(null); }}
                              placeholder="给这个计划起个名字"
                              className="helm-field w-64 rounded-md border border-white/[0.12] bg-black/30 px-2 py-1 text-sm text-white outline-none" />
                            <button type="button" onClick={() => void submitRename()} aria-label="保存名称" className={iconBtn}><Check className="h-4 w-4 text-emerald-300" /></button>
                            <button type="button" onClick={() => setRenaming(null)} aria-label="取消重命名" className={iconBtn}><X className="h-4 w-4" /></button>
                          </div>
                        ) : (
                          <span className="truncate text-sm font-medium text-white">{plan.display_name || '未命名计划'}</span>
                        )}
                        {plan.status && (
                          <span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusTone(plan.status)}`}>{STATUS_LABELS[plan.status] ?? plan.status}</span>
                        )}
                        {plan.archived && <span className="rounded-full border border-zinc-600/40 px-2 py-0.5 text-[10px] text-zinc-400">已归档</span>}
                      </div>
                      <code className="font-mono text-xs text-zinc-600">{plan.plan_id}</code>
                    </div>
                    <div className="flex shrink-0 items-center gap-1" onClick={stop}>
                      {busy && <Loader2 className="h-4 w-4 animate-spin helm-accent" />}
                      <button type="button" disabled={busy} onClick={() => setRenaming({ planId: plan.plan_id, name: plan.display_name })}
                        title="重命名" aria-label="重命名计划" className={iconBtn}><Pencil className="h-4 w-4" /></button>
                      <button type="button" disabled={busy} onClick={() => void handleArchive(plan)}
                        title={plan.archived ? '取消归档' : '归档（从列表隐藏，不删除数据）'} aria-label={plan.archived ? '取消归档' : '归档计划'} className={iconBtn}>
                        {plan.archived ? <ArchiveRestore className="h-4 w-4" /> : <Archive className="h-4 w-4" />}
                      </button>
                      <button type="button" disabled={busy || running} onClick={() => void handleDeletePlan(plan)}
                        title={running ? '计划正在执行或已暂停，需先结束后再删除' : '删除计划'} aria-label="删除计划" className={`${iconBtn} hover:text-red-300`}>
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}
