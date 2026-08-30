/**
 * 审计日志页面
 *
 * 展示工具调用审计记录（含被安全拦截的调用）。
 * 数据源：GET /api/audit-logs，支持条数限制与客户端关键字过滤。
 */

import { useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw, ScrollText, Search, ShieldAlert } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { AuditLogEntry } from '../types';

const LIMIT_OPTIONS = [50, 100, 200, 500] as const;

function ResultBadge({ entry }: { entry: AuditLogEntry }) {
  if (entry.blocked === 1) {
    return (
      <span className="px-2 py-0.5 rounded text-xs bg-red-950/60 border border-red-800 text-red-300 whitespace-nowrap">
        已拦截
      </span>
    );
  }
  if (entry.success === 1) {
    return (
      <span className="px-2 py-0.5 rounded text-xs bg-green-950/60 border border-green-800 text-green-300 whitespace-nowrap">
        成功
      </span>
    );
  }
  if (entry.success === 0) {
    return (
      <span className="px-2 py-0.5 rounded text-xs bg-red-950/60 border border-red-800 text-red-300 whitespace-nowrap">
        失败
      </span>
    );
  }
  return (
    <span className="px-2 py-0.5 rounded text-xs bg-zinc-900 border border-zinc-700 text-zinc-400 whitespace-nowrap">
      未知
    </span>
  );
}

export function AuditView() {
  const { addLog } = useAppStore();
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [limit, setLimit] = useState<number>(100);
  const [search, setSearch] = useState('');

  const load = async (currentLimit: number) => {
    setIsLoading(true);
    try {
      const logs = await apiClient.getAuditLogs(currentLimit);
      setEntries(logs);
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `加载审计日志失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load(limit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [limit]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) =>
      [e.instruction, e.tool_id, e.plan_id, e.block_reason]
        .some((field) => (field ?? '').toLowerCase().includes(q))
    );
  }, [entries, search]);

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-5xl mx-auto space-y-4">
        {/* 头部 */}
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-2xl font-bold text-white flex items-center gap-2">
            <ScrollText className="w-6 h-6 text-zinc-400" />
            审计日志
            <span className="text-sm font-normal text-zinc-500">
              {filtered.length} / {entries.length} 条
            </span>
          </h2>
          <div className="flex items-center gap-2">
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-300 focus:outline-none focus:border-blue-600"
            >
              {LIMIT_OPTIONS.map((n) => (
                <option key={n} value={n}>最近 {n} 条</option>
              ))}
            </select>
            <button
              onClick={() => void load(limit)}
              disabled={isLoading}
              className="flex items-center gap-1.5 px-3 py-2 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 text-white rounded-lg text-sm transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>
        </div>

        {/* 搜索框 */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索指令、工具或计划 ID..."
            className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-10 pr-4 py-2 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-blue-600"
          />
        </div>

        {/* 内容 */}
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-8 h-8 text-zinc-600 animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-zinc-500">
            <ScrollText className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>暂无审计日志</p>
          </div>
        ) : (
          <div className="bg-zinc-950 border border-zinc-800 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-zinc-500 border-b border-zinc-800">
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">工具</th>
                  <th className="px-3 py-2 font-medium">指令</th>
                  <th className="px-3 py-2 font-medium">计划</th>
                  <th className="px-3 py-2 font-medium">结果</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry) => (
                  <tr key={entry.audit_id} className="border-b border-zinc-800/60 hover:bg-zinc-900/50">
                    <td className="px-3 py-2 text-xs text-zinc-400 whitespace-nowrap align-top">
                      {new Date(entry.timestamp).toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-zinc-300 whitespace-nowrap align-top">
                      {entry.tool_id || '-'}
                    </td>
                    <td className="px-3 py-2 align-top max-w-[24rem]">
                      <div className="font-mono text-xs text-zinc-300 truncate" title={entry.instruction ?? ''}>
                        {entry.instruction || '-'}
                      </div>
                      {entry.blocked === 1 && entry.block_reason && (
                        <div className="mt-0.5 flex items-start gap-1 text-xs text-red-400">
                          <ShieldAlert className="w-3 h-3 mt-0.5 shrink-0" />
                          <span className="break-all">{entry.block_reason}</span>
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 align-top">
                      <code className="text-xs text-zinc-600 font-mono">
                        {entry.plan_id ? entry.plan_id.slice(0, 8) : '-'}
                      </code>
                    </td>
                    <td className="px-3 py-2 align-top">
                      <ResultBadge entry={entry} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
