/**
 * Agent 运行记录视图（1.18 Session/Turn 分层的展示层）。
 *
 * 列出每次 agent_tool 步骤执行的 Session（轮数 / 模型与工具调用 / Token / 终态），
 * 点击展开该 Session 的逐轮 Turn 记录（模型调用、结果、Token、耗时）。
 */
import { useCallback, useEffect, useState } from 'react';
import { Bot, ChevronDown, ChevronRight, Coins, RefreshCw, Timer } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { AgentSession, AgentTurn } from '../types';

const SESSION_STATUS_LABELS: Record<AgentSession['status'], { label: string; tone: string }> = {
  running: { label: '运行中', tone: 'border-blue-400/25 bg-blue-400/10 text-blue-200' },
  completed: { label: '已完成', tone: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200' },
  failed: { label: '已失败', tone: 'border-red-400/25 bg-red-400/10 text-red-200' },
};

const TURN_OUTCOME_LABELS: Record<string, { label: string; tone: string }> = {
  model_ok: { label: '模型调用成功', tone: 'text-zinc-300' },
  applied: { label: '已写回', tone: 'text-emerald-300' },
  selftest_passed: { label: '自测通过', tone: 'text-emerald-300' },
  selftest_failed: { label: '自测未通过', tone: 'text-amber-300' },
  patch_error: { label: '补丁错误', tone: 'text-red-300' },
  json_error: { label: 'JSON 解析失败', tone: 'text-red-300' },
  invalid_output: { label: '输出非法', tone: 'text-red-300' },
  path_redirect: { label: '路径重定向被拒', tone: 'text-red-300' },
  empty_rejected: { label: '空内容被拒', tone: 'text-amber-300' },
  write_denied: { label: '写入被拒', tone: 'text-red-300' },
  model_error: { label: '模型调用失败', tone: 'text-red-300' },
  model_denied: { label: '预算拒绝', tone: 'text-red-300' },
};

function outcomeTone(outcome: string): string {
  return (TURN_OUTCOME_LABELS[outcome] ?? { tone: 'text-zinc-300' }).tone;
}

function outcomeLabel(outcome: string): string {
  return TURN_OUTCOME_LABELS[outcome]?.label ?? outcome;
}

export function AgentSessionsView() {
  const { currentPlan, addLog } = useAppStore();
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planFilter, setPlanFilter] = useState<string>(currentPlan?.plan_id ?? '');
  const [expandedSession, setExpandedSession] = useState<string | null>(null);
  const [turns, setTurns] = useState<Record<string, AgentTurn[]>>({});
  const [turnsLoading, setTurnsLoading] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSessions(await apiClient.getAgentSessions(planFilter || undefined, 200));
    } catch (err) {
      const message = `获取 Agent 运行记录失败: ${err instanceof Error ? err.message : String(err)}`;
      setError(message);
      addLog({ type: 'log', level: 'error', message });
    } finally {
      setLoading(false);
    }
  }, [planFilter, addLog]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const toggleTurns = async (sessionId: string) => {
    if (expandedSession === sessionId) {
      setExpandedSession(null);
      return;
    }
    setExpandedSession(sessionId);
    if (!turns[sessionId]) {
      setTurnsLoading(sessionId);
      try {
        const data = await apiClient.getAgentTurns(sessionId);
        setTurns((previous) => ({ ...previous, [sessionId]: data }));
      } catch (err) {
        const message = `获取轮次记录失败: ${err instanceof Error ? err.message : String(err)}`;
        addLog({ type: 'log', level: 'error', message });
      } finally {
        setTurnsLoading(null);
      }
    }
  };

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">Agent 运行记录</h2>
            <p className="mt-1 text-xs text-zinc-500">每次 agent_tool 步骤执行一个 Session；展开查看逐轮模型调用（Turn）。</p>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={planFilter}
              onChange={(event) => setPlanFilter(event.target.value)}
              placeholder="按 plan_id 过滤（留空显示全部）"
              aria-label="按 plan_id 过滤"
              className="w-64 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 font-mono text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-violet-400/40 focus:outline-none"
            />
            <button
              onClick={() => void refresh()}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-xs font-semibold text-zinc-200 transition-colors hover:bg-white/[0.08] disabled:opacity-40"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>
        </div>

        {error && (
          <div role="alert" className="rounded-lg border border-red-400/20 bg-red-400/[0.08] px-3 py-2 text-xs text-red-200">{error}</div>
        )}

        {!loading && sessions.length === 0 && !error && (
          <div className="rounded-xl border border-dashed border-white/[0.12] bg-white/[0.02] p-6 text-center text-sm text-zinc-400">
            还没有 Agent 运行记录。执行包含 coding_agent 步骤的计划后会在这里出现。
          </div>
        )}

        <div className="space-y-2">
          {sessions.map((session) => {
            const statusMeta = SESSION_STATUS_LABELS[session.status] ?? SESSION_STATUS_LABELS.completed;
            const expanded = expandedSession === session.session_id;
            const sessionTurns = turns[session.session_id] ?? [];
            return (
              <div key={session.session_id} className="rounded-xl border border-white/[0.08] bg-[#111419]/90">
                <button
                  onClick={() => void toggleTurns(session.session_id)}
                  className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-white/[0.025]"
                  aria-expanded={expanded}
                >
                  {expanded ? <ChevronDown className="h-4 w-4 shrink-0 text-zinc-500" /> : <ChevronRight className="h-4 w-4 shrink-0 text-zinc-500" />}
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-violet-400/10 text-violet-300">
                    <Bot className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-zinc-100">{session.agent_tool}</span>
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusMeta.tone}`}>{statusMeta.label}</span>
                      <span className="font-mono text-[10px] text-zinc-600">{session.session_id.slice(0, 8)}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-zinc-500">
                      <span className="inline-flex items-center gap-1"><Coins className="h-3 w-3" />{session.input_tokens + session.output_tokens} tokens</span>
                      <span>模型 ×{session.model_calls}</span>
                      <span>工具 ×{session.tool_calls}</span>
                      <span className="inline-flex items-center gap-1"><Timer className="h-3 w-3" />{session.turns_used} 轮</span>
                    </div>
                  </div>
                </button>

                {expanded && (
                  <div className="border-t border-white/[0.07] px-4 pb-4 pt-3">
                    {session.error && (
                      <div className="mb-3 rounded-lg border border-red-400/20 bg-red-400/[0.06] px-3 py-2 text-xs text-red-200">{session.error}</div>
                    )}
                    {turnsLoading === session.session_id ? (
                      <div className="py-2 text-xs text-zinc-500">加载轮次记录...</div>
                    ) : sessionTurns.length === 0 ? (
                      <div className="py-2 text-xs text-zinc-500">没有轮次记录。</div>
                    ) : (
                      <div className="space-y-1.5">
                        <div className="text-[10px] uppercase tracking-[0.12em] text-zinc-600">轮次记录</div>
                        {sessionTurns.map((turn) => (
                          <div key={turn.turn_row_id} className="flex items-center gap-3 rounded-lg border border-white/[0.06] bg-black/20 px-3 py-2">
                            <span className="font-mono text-[10px] text-zinc-600">T{String(turn.turn_no).padStart(2, '0')}</span>
                            <span className={`min-w-0 flex-1 truncate text-xs ${outcomeTone(turn.outcome)}`}>{outcomeLabel(turn.outcome)}</span>
                            <span className="inline-flex items-center gap-1 text-[10px] text-zinc-500"><Coins className="h-3 w-3" />{turn.input_tokens}↑ {turn.output_tokens}↓</span>
                            {turn.duration_ms != null && <span className="inline-flex items-center gap-1 text-[10px] text-zinc-500"><Timer className="h-3 w-3" />{Math.round(turn.duration_ms / 100) / 10}s</span>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
