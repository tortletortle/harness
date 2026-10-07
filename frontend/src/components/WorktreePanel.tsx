/**
 * 计划的隔离分支（git worktree）面板：查看绑定与变更、创建、合并回主仓库、丢弃
 */

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, GitBranch, GitMerge, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { WorktreeStatus } from '../types/system';

type Busy = 'load' | 'create' | 'merge' | 'discard' | null;

export function WorktreePanel({ planId, planStatus }: { planId: string; planStatus?: string }) {
  const addLog = useAppStore((s) => s.addLog);
  const [status, setStatus] = useState<WorktreeStatus | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy('load');
    setError(null);
    try {
      const result = await apiClient.getWorktree(planId);
      setStatus(result);
      if (result.worktree) setOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }, [planId]);

  useEffect(() => {
    setStatus(null);
    setConflict(null);
    setOpen(false);
    void load();
  }, [load]);

  const running = planStatus === 'Running';
  const wt = status?.worktree ?? null;

  const create = async () => {
    if (!window.confirm('为这个计划创建隔离分支？\n\n之后的改动会写在独立的 git worktree 里，确认无误后再合并回主仓库。')) return;
    setBusy('create'); setError(null);
    try {
      await apiClient.createWorktree(planId);
      addLog({ type: 'log', level: 'success', message: `计划 ${planId.slice(0, 8)} 已创建隔离分支` });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  };

  const merge = async () => {
    if (!wt || !window.confirm(`把分支 ${wt.branch} 合并回主仓库？\n\n如有冲突不会自动处理，会显示冲突信息。`)) return;
    setBusy('merge'); setError(null); setConflict(null);
    const result = await apiClient.mergeWorktree(planId);
    if (result.success) {
      addLog({ type: 'log', level: 'success', message: `分支 ${result.branch ?? wt.branch} 已合并回主仓库` });
      await load();
    } else {
      setBusy(null);
      if (result.conflict) setConflict(result.output || result.message || '合并出现冲突');
      else setError(result.error || '合并失败');
    }
  };

  const discard = async () => {
    if (!wt || !window.confirm(`丢弃隔离分支 ${wt.branch}？\n\n会删除 worktree 目录，里面未合并的改动将丢失。`)) return;
    setBusy('discard'); setError(null); setConflict(null);
    const result = await apiClient.discardWorktree(planId);
    if (result.success) {
      addLog({ type: 'log', level: 'warning', message: `已丢弃计划 ${planId.slice(0, 8)} 的隔离分支` });
      await load();
    } else {
      setBusy(null);
      setError(result.error || '丢弃失败');
    }
  };

  const btn = 'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40';

  return (
    <section aria-label="隔离分支" className="rounded-xl border border-white/[0.08] bg-[#111419]/90">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <button type="button" onClick={() => setOpen((v) => !v)} className="flex min-w-0 items-center gap-2 text-left">
          {open ? <ChevronDown className="h-4 w-4 text-zinc-500" /> : <ChevronRight className="h-4 w-4 text-zinc-500" />}
          <GitBranch className="h-4 w-4 helm-accent" />
          <span className="text-sm font-medium text-white">隔离分支</span>
          <span className="truncate text-xs text-zinc-500">
            {busy === 'load' && !status ? '读取中…' : wt ? wt.branch : '未启用'}
          </span>
        </button>
        <div className="flex shrink-0 items-center gap-2">
          <button type="button" onClick={() => void load()} disabled={busy !== null} title="刷新" aria-label="刷新隔离分支状态"
            className="rounded-md p-1.5 text-zinc-500 hover:bg-white/[0.06] hover:text-white disabled:opacity-40">
            <RefreshCw className={`h-3.5 w-3.5 ${busy === 'load' ? 'animate-spin' : ''}`} />
          </button>
          {!wt && status && (
            <button type="button" onClick={() => void create()} disabled={busy !== null}
              className={`${btn} helm-chip helm-hover-brand`}>
              {busy === 'create' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}创建隔离分支
            </button>
          )}
        </div>
      </div>

      {error && <div role="status" className="mx-4 mb-3 rounded-lg border border-red-400/20 bg-red-400/[0.08] px-3 py-2 text-xs text-red-200">{error}</div>}

      {open && wt && (
        <div className="space-y-3 border-t border-white/[0.06] px-4 py-3 text-xs">
          <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5">
            <dt className="text-zinc-500">分支</dt><dd className="font-mono text-zinc-200">{wt.branch}</dd>
            <dt className="text-zinc-500">隔离目录</dt><dd className="break-all font-mono text-zinc-300">{wt.worktree_path}</dd>
            <dt className="text-zinc-500">主仓库</dt><dd className="break-all font-mono text-zinc-400">{wt.repo_path}</dd>
            {wt.base_commit && <><dt className="text-zinc-500">基于提交</dt><dd className="font-mono text-zinc-400">{wt.base_commit.slice(0, 10)}</dd></>}
            <dt className="text-zinc-500">创建时间</dt><dd className="text-zinc-400">{wt.created_at.slice(0, 19).replace('T', ' ')}</dd>
          </dl>
          <div>
            <div className="mb-1 text-zinc-500">相对主仓库的改动</div>
            {status?.diff?.error ? (
              <p className="text-amber-200">无法读取改动：{status.diff.error}</p>
            ) : status?.diff?.stat ? (
              <pre className="max-h-48 overflow-auto rounded-lg border border-white/[0.06] bg-black/30 p-3 font-mono text-[11px] leading-5 text-zinc-300">{status.diff.stat}</pre>
            ) : (
              <p className="text-zinc-500">暂无改动</p>
            )}
          </div>
          {conflict && (
            <div role="alert" className="rounded-lg border border-amber-400/25 bg-amber-400/[0.08] p-3">
              <div className="mb-1 font-medium text-amber-200">合并冲突，需要手动处理</div>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-zinc-300">{conflict}</pre>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button type="button" onClick={() => void merge()} disabled={busy !== null || running}
              title={running ? '计划执行中，暂不能合并' : undefined}
              className={`${btn} border-emerald-400/30 bg-emerald-400/10 text-emerald-200 hover:bg-emerald-400/20`}>
              {busy === 'merge' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <GitMerge className="h-3.5 w-3.5" />}合并回主仓库
            </button>
            <button type="button" onClick={() => void discard()} disabled={busy !== null || running}
              title={running ? '计划执行中，暂不能丢弃' : undefined}
              className={`${btn} border-red-400/25 bg-red-400/[0.06] text-red-200 hover:bg-red-400/15`}>
              {busy === 'discard' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}丢弃分支
            </button>
            {running && <span className="text-zinc-500">计划执行中，结束后才能合并或丢弃</span>}
          </div>
        </div>
      )}

      {open && !wt && status && (
        <p className="border-t border-white/[0.06] px-4 py-3 text-xs text-zinc-500">
          这个计划直接在主工作区执行。创建隔离分支后，改动会先写到独立目录，确认无误再合并。
        </p>
      )}
    </section>
  );
}
