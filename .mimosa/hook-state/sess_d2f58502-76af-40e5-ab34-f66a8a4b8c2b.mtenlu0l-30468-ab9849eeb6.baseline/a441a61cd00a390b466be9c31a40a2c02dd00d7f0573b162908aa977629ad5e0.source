/**
 * 全局 Toast 通知：error/warning 日志自动浮出，右下角堆叠，5 秒自动消失
 */

import { useEffect } from 'react';
import { AlertTriangle, AlertOctagon, X } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import type { Toast } from '../types';

const AUTO_DISMISS_MS = 5000;

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  // 挂载后 5 秒自动消失，卸载时清理定时器
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  const isError = toast.level === 'error';

  return (
    <div
      role="alert"
      className={`w-80 rounded-lg border shadow-lg px-4 py-3 flex items-start gap-2 ${
        isError
          ? 'bg-red-950/90 border-red-700 text-red-100'
          : 'bg-amber-950/90 border-amber-700 text-amber-100'
      }`}
    >
      {isError ? (
        <AlertOctagon className="w-4 h-4 mt-0.5 flex-shrink-0" />
      ) : (
        <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      )}
      <p className="flex-1 text-sm break-words">{toast.message}</p>
      <button
        onClick={() => onDismiss(toast.id)}
        className="opacity-70 hover:opacity-100 transition-opacity"
        aria-label="关闭"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

export function ToastHub() {
  const toasts = useAppStore((state) => state.toasts);
  const dismissToast = useAppStore((state) => state.dismissToast);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] space-y-2">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={dismissToast} />
      ))}
    </div>
  );
}
