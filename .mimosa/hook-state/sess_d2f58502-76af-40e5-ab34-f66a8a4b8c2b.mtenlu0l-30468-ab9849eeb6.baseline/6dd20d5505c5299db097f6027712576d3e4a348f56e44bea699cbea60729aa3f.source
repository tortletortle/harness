/**
 * 日志面板
 */

import { useEffect, useRef } from 'react';
import { Terminal, X } from 'lucide-react';
import { useAppStore } from '../stores/appStore';

export function LogPanel() {
  const { logs, clearLogs } = useAppStore();
  const logContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 自动滚动到底部
    if (logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs]);

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'success':
        return 'text-green-400';
      case 'error':
        return 'text-red-400';
      case 'warning':
        return 'text-yellow-400';
      default:
        return 'text-zinc-400';
    }
  };

  return (
    <div className="h-48 bg-zinc-950 border-t border-zinc-800 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-800">
        <div className="flex items-center gap-2 text-sm font-medium text-white">
          <Terminal className="w-4 h-4" />
          <span>实时日志</span>
          <span className="text-xs text-zinc-500">({logs.length})</span>
        </div>
        <button
          onClick={clearLogs}
          className="text-zinc-400 hover:text-white transition-colors"
          title="清空日志"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Logs */}
      <div 
        ref={logContainerRef}
        className="flex-1 overflow-y-auto p-2 font-mono text-xs"
      >
        {logs.length === 0 ? (
          <div className="text-zinc-600 text-center py-8">暂无日志</div>
        ) : (
          logs.map((log, index) => (
            <div key={index} className="mb-1">
              <span className="text-zinc-600">
                {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}
              </span>
              {' '}
              <span className={getLevelColor(log.level)}>
                [{log.level.toUpperCase()}]
              </span>
              {' '}
              <span className="text-zinc-300">{log.message}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
