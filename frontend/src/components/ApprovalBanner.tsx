/**
 * P0-a 工具审批卡片
 *
 * 后端执行协程在调用高风险工具前挂起等待（asyncio.Event），本卡片展示
 * 待审批的工具调用详情；批准/拒绝调用对应 REST 端点 resolve 挂起的等待。
 * 决定经 WebSocket approval_resolved 事件同步关闭（Cline 式挂起-恢复语义）。
 */

import { useEffect, useState } from 'react';
import { ShieldAlert, Check, X } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

export function ApprovalBanner() {
  const pendingApproval = useAppStore((s) => s.pendingApproval);
  const setPendingApproval = useAppStore((s) => s.setPendingApproval);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [confirmedId, setConfirmedId] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    let inFlight = false;
    let failing = false;
    const refresh = async () => {
      if (!active || inFlight) return;
      inFlight = true;
      // 只允许仍对应请求开始时那条审批的响应修改 store；旧 GET 不得复活已处理项。
      const requestedApprovalId = useAppStore.getState().pendingApproval?.approval_id ?? null;
      try {
        const requests = await apiClient.getPendingApprovals();
        if (!active) return;
        failing = false;
        const current = useAppStore.getState().pendingApproval;
        if ((current?.approval_id ?? null) !== requestedApprovalId) return;
        if (!current || !requests.some((r) => r.approval_id === current.approval_id)) {
          useAppStore.getState().setPendingApproval(requests[0] || null);
        }
      } catch (error) {
        // 一次连续故障只写一条日志；请求失败不授权，也不清除当前审批。
        if (active && !failing) {
          failing = true;
          useAppStore.getState().addLog({
            type: 'log',
            level: 'error',
            message: `刷新待审批请求失败: ${error instanceof Error ? error.message : String(error)}`,
          });
        }
      } finally {
        inFlight = false;
      }
    };
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 5000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  const desktop = pendingApproval?.tool_id === 'desktop_observe';
  const scope = pendingApproval?.desktop_scope;
  if (!pendingApproval) return null;

  const decide = async (approved: boolean) => {
    if (approved && desktop && (!scope || confirmedId !== pendingApproval.approval_id)) return;
    setSubmitting(true);
    setError(null);
    try {
      if (approved) {
        if (desktop && scope) {
          await apiClient.approveToolCall(pendingApproval.plan_id, pendingApproval.approval_id, comment || undefined, scope.confirmation);
        } else {
          await apiClient.approveToolCall(pendingApproval.plan_id, pendingApproval.approval_id, comment || undefined);
        }
      } else {
        await apiClient.rejectToolCall(pendingApproval.plan_id, pendingApproval.approval_id, comment || undefined);
      }
      // 乐观关闭：服务端 approval_resolved 事件会再同步一次
      if (useAppStore.getState().pendingApproval?.approval_id === pendingApproval.approval_id) setPendingApproval(null);
      setConfirmedId(null);
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
      className="fixed inset-x-4 top-4 z-50 mx-auto max-w-3xl rounded-xl border border-amber-400/30 bg-[#211b12]/95 px-4 py-3 shadow-2xl shadow-black/40 backdrop-blur-xl sm:inset-x-auto sm:right-6 sm:left-auto sm:w-[min(640px,calc(100vw-3rem))]"
    >
      <div className="flex items-start gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-amber-100">
            工具调用等待审批：{pendingApproval.tool_id}
          </div>
          <div className="mt-1 truncate text-xs text-zinc-400" title={pendingApproval.instruction}>
            步骤 {pendingApproval.step_id.slice(0, 8)}…：{pendingApproval.instruction}
          </div>
          {desktop && (
            <div data-testid="desktop-approval-scope" className="mt-2 rounded border border-amber-500/40 p-3 text-xs text-amber-100">
              <strong>桌面隐私授权 · 仅本次操作</strong>
              <p className="mt-1">{scope?.summary || '缺少可信授权范围，请拒绝并重新请求。'}</p>
              {scope?.identity && <p>窗口 HWND：{scope.identity.window_id} · PID：{scope.identity.pid}</p>}
              {scope?.output && <p className="break-all">新截图：{scope.output}</p>}
              <p className="mt-1">不点击、不输入、不切换焦点。窗口标题与步骤描述不能代替授权范围。截图不会自动遮挡隐私。</p>
              <label className="mt-2 flex items-center gap-2">
                <input data-testid="desktop-confirm" type="checkbox" disabled={submitting || !scope}
                  checked={confirmedId === pendingApproval.approval_id}
                  onChange={(e) => setConfirmedId(e.target.checked ? pendingApproval.approval_id : null)} />
                我已核对上述窗口/范围，仅批准本次访问
              </label>
            </div>
          )}
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
              disabled={submitting || (desktop && (!scope || confirmedId !== pendingApproval.approval_id))}
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
