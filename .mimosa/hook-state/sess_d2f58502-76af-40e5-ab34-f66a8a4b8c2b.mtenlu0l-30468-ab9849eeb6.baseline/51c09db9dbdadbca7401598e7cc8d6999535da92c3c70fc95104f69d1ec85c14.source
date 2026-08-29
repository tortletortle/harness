/**
 * P0-a 工具审批卡片
 *
 * 后端执行协程在调用高风险工具前挂起等待（asyncio.Event），本卡片展示
 * 待审批的工具调用详情；批准/拒绝调用对应 REST 端点 resolve 挂起的等待。
 * 决定经 WebSocket approval_resolved 事件同步关闭（Cline 式挂起-恢复语义）。
 */

import { useState } from 'react';
import { ShieldAlert, Check, X } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

export function ApprovalBanner() {
  const pendingApproval = useAppStore((s) => s.pendingApproval);
  const setPendingApproval = useAppStore((s) => s.setPendingApproval);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!pendingApproval) return null;

  const decide = async (approved: boolean) => {
    setSubmitting(true);
    setError(null);
    try {
      if (approved) {
        await apiClient.approveToolCall(pendingApproval.plan_id, pendingApproval.approval_id, comment || undefined);
      } else {
        await apiClient.rejectToolCall(pendingApproval.plan_id, pendingApproval.approval_id, comment || undefined);
      }
      // 乐观关闭：服务端 approval_resolved 事件会再同步一次
      setPendingApproval(null);
      setComment('');
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="approval-banner"
      className="border border-amber-600/60 bg-amber-950/40 px-4 py-3"
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-amber-200">
            工具调用等待审批：{pendingApproval.tool_id}
          </div>
          <div className="mt-1 truncate text-xs text-zinc-400" title={pendingApproval.instruction}>
            步骤 {pendingApproval.step_id.slice(0, 8)}…：{pendingApproval.instruction}
          </div>
          {Object.keys(pendingApproval.inputs || {}).length > 0 && (
            <pre className="mt-1 max-h-24 overflow-auto rounded bg-black/40 p-2 text-[11px] leading-snug text-zinc-300">
              {JSON.stringify(pendingApproval.inputs, null, 2)}
            </pre>
          )}

          <div className="mt-2 flex items-center gap-2">
            <input
              data-testid="approval-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="备注（可选）"
              className="h-7 w-48 rounded border border-zinc-700 bg-zinc-900 px-2 text-xs text-white placeholder-zinc-600 focus:border-amber-500 focus:outline-none"
              disabled={submitting}
            />
            <button
              data-testid="approval-approve"
              onClick={() => decide(true)}
              disabled={submitting}
              className="flex h-7 items-center gap-1 rounded bg-emerald-700 px-3 text-xs font-medium text-white hover:bg-emerald-600 disabled:opacity-50"
            >
              <Check className="h-3.5 w-3.5" />
              批准执行
            </button>
            <button
              data-testid="approval-reject"
              onClick={() => decide(false)}
              disabled={submitting}
              className="flex h-7 items-center gap-1 rounded bg-red-800 px-3 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              <X className="h-3.5 w-3.5" />
              拒绝
            </button>
          </div>
          {error && <div className="mt-1 text-xs text-red-400">{error}</div>}
        </div>
      </div>
    </div>
  );
}
