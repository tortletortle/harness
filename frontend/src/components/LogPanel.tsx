/**
 * Activity stream dock: live execution logs with level badges and auto-scroll.
 */

import { useEffect, useRef } from 'react';
import { TerminalSquare, X } from 'lucide-react';
import { useAppStore } from '../stores/appStore';

const levelStyles: Record<string, string> = {
  success: 'bg-emerald-400/15 text-emerald-300 border-emerald-400/20',
  error: 'bg-red-400/15 text-red-300 border-red-400/20',
  warning: 'bg-amber-400/15 text-amber-300 border-amber-400/20',
  info: 'bg-white/[0.06] text-zinc-300 border-white/[0.08]',
};

const levelLabel: Record<string, string> = {
  success: '成功',
  error: '错误',
  warning: '警告',
  info: '信息',
};

export function LogPanel() {
  const { logs, clearLogs } = useAppStore();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#0b0d11]">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-white/[0.07] px-4">
        <div className="flex items-center gap-2 text-[12px] font-medium text-zinc-200">
          <TerminalSquare className="h-3.5 w-3.5 text-emerald-300" />
          <span>活动流</span>
          <span className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">{logs.length}</span>
        </div>
        <button onClick={clearLogs} title="清空日志" className="rounded p-1 text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-white">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div ref={containerRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3 font-mono text-[11px] leading-relaxed">
        {logs.length === 0 ? (
          <div className="flex h-full items-center justify-center text-zinc-700">
            暂无日志 · 执行任务后事件会实时出现在这里
          </div>
        ) : (
          <div className="space-y-1.5">
            {logs.map((log, index) => (
              <div key={index} className="flex items-start gap-2">
                <span className="shrink-0 pt-px text-zinc-600">{log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}</span>
                <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] ${levelStyles[log.level] || levelStyles.info}`}>
                  {levelLabel[log.level] || log.level.toUpperCase()}
                </span>
                <span className={log.level === 'error' ? 'text-red-200/90' : log.level === 'warning' ? 'text-amber-100/80' : 'text-zinc-300'}>
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}