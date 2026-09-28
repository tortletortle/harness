/**
 * 系统概览：模型用量、技能包、能力清单、项目密钥、AI 对话会话、通知通道测试
 */

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Bell, Gauge, KeyRound, Loader2, MessagesSquare, Puzzle, RefreshCw, ShieldCheck } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { CapabilityInfo, ChatSessionStats, SecretList, SkillList, UsageSummary } from '../types/system';

const PHASE_LABELS: Record<string, string> = {
  designer: '设计书', planner: '计划生成', executor: '执行', adhoc: '对话/临时', chat: '对话', replan: '重规划',
};
const SIDE_EFFECT_LABELS: Record<string, string> = {
  none: '只读', reversible: '可撤销', external: '外部副作用', irreversible: '不可撤销',
};
const sideEffectTone = (v: string) =>
  v === 'none' ? 'text-emerald-300' : v === 'reversible' ? 'text-sky-300' : v === 'external' ? 'text-amber-300' : 'text-red-300';

const fmt = (n: number) => n.toLocaleString();

function Card({ icon, title, extra, children }: { icon: ReactNode; title: string; extra?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-white/[0.08] bg-[#111419]/90">
      <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-medium text-white">{icon}{title}</div>
        {extra}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

type Loaded<T> = { data: T | null; error: string | null };
const empty = { data: null, error: null };

async function settle<T>(p: Promise<T>): Promise<Loaded<T>> {
  try { return { data: await p, error: null }; } catch (e) { return { data: null, error: e instanceof Error ? e.message : String(e) }; }
}

export function SystemView() {
  const addLog = useAppStore((s) => s.addLog);
  const [loading, setLoading] = useState(false);
  const [usage, setUsage] = useState<Loaded<UsageSummary>>(empty);
  const [skills, setSkills] = useState<Loaded<SkillList>>(empty);
  const [caps, setCaps] = useState<Loaded<CapabilityInfo[]>>(empty);
  const [secrets, setSecrets] = useState<Loaded<SecretList>>(empty);
  const [chats, setChats] = useState<Loaded<ChatSessionStats>>(empty);
  const [notifying, setNotifying] = useState(false);
  const [notifyResult, setNotifyResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const [u, s, c, k, ch] = await Promise.all([
      settle(apiClient.getUsage(15)), settle(apiClient.getSkills()), settle(apiClient.getCapabilities()),
      settle(apiClient.getSecrets()), settle(apiClient.getChatSessions()),
    ]);
    setUsage(u); setSkills(s); setCaps(c); setSecrets(k); setChats(ch);
    setLoading(false);
  }, []);

  useEffect(() => { void load(); }, [load]);

  const testNotify = async () => {
    setNotifying(true); setNotifyResult(null);
    try {
      const r = await apiClient.testNotify();
      const msg = r.channels > 0 ? `已向 ${r.channels} 个通知通道发送测试消息，请到对应 IM 查看` : '没有配置 IM 通道，测试消息已写入本地审计日志';
      setNotifyResult(msg);
      addLog({ type: 'log', level: 'success', message: msg });
    } catch (e) {
      const msg = `发送测试通知失败: ${e instanceof Error ? e.message : String(e)}`;
      setNotifyResult(msg);
      addLog({ type: 'log', level: 'error', message: msg });
    } finally {
      setNotifying(false);
    }
  };

  const err = (e: string | null) => e && <p className="text-xs text-red-300">读取失败：{e}</p>;
  const u = usage.data;
  const maxPhase = Math.max(1, ...(u?.by_phase ?? []).map((p) => p.total_tokens));

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl space-y-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-white">系统概览</h2>
            <p className="mt-1 text-sm text-zinc-500">模型用量、已加载的技能和能力、项目密钥与通知通道</p>
          </div>
          <button type="button" onClick={() => void load()} disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/[0.1] bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-white/[0.08] disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />刷新
          </button>
        </div>

        <Card icon={<Gauge className="h-4 w-4 text-violet-300" />} title="模型用量"
          extra={u && u.budget_max_tokens_per_plan > 0 ? <span className="text-[11px] text-zinc-500">单计划预算 {fmt(u.budget_max_tokens_per_plan)} tokens</span> : null}>
          {err(usage.error)}
          {!u && !usage.error && <Loader2 className="h-4 w-4 animate-spin text-zinc-600" />}
          {u && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[['调用次数', u.calls], ['总 tokens', u.total_tokens], ['输入', u.input_tokens], ['输出', u.output_tokens]].map(([label, value]) => (
                  <div key={label as string} className="rounded-lg border border-white/[0.07] bg-white/[0.025] px-3 py-2">
                    <div className="text-[10px] text-zinc-500">{label}</div>
                    <div className="mt-1 text-lg font-semibold text-white">{fmt(value as number)}</div>
                  </div>
                ))}
              </div>
              {u.by_phase.length > 0 && (
                <div className="space-y-1.5">
                  {u.by_phase.map((p) => (
                    <div key={p.phase} className="flex items-center gap-3 text-xs">
                      <span className="w-20 shrink-0 text-zinc-400">{PHASE_LABELS[p.phase] ?? p.phase}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
                        <div className="h-full rounded-full bg-gradient-to-r from-violet-500 to-sky-400" style={{ width: `${(p.total_tokens / maxPhase) * 100}%` }} />
                      </div>
                      <span className="w-36 shrink-0 text-right font-mono text-zinc-500">{fmt(p.total_tokens)} · {p.calls} 次</span>
                    </div>
                  ))}
                </div>
              )}
              {u.recent.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-zinc-500"><tr>
                      <th className="py-1.5 pr-3 font-medium">时间</th><th className="py-1.5 pr-3 font-medium">模型</th>
                      <th className="py-1.5 pr-3 font-medium">阶段</th><th className="py-1.5 text-right font-medium">tokens（入/出）</th>
                    </tr></thead>
                    <tbody>
                      {u.recent.map((r, i) => (
                        <tr key={`${r.ts}-${i}`} className="border-t border-white/[0.05] text-zinc-300">
                          <td className="py-1.5 pr-3 whitespace-nowrap text-zinc-500">{r.ts.replace('T', ' ').slice(0, 19)}</td>
                          <td className="py-1.5 pr-3"><span className="text-zinc-500">{r.provider}/</span>{r.model}</td>
                          <td className="py-1.5 pr-3 text-zinc-400">{PHASE_LABELS[r.phase] ?? r.phase}</td>
                          <td className="py-1.5 text-right font-mono">{fmt(r.total_tokens)} <span className="text-zinc-600">({fmt(r.input_tokens)}/{fmt(r.output_tokens)})</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </Card>

        <div className="grid gap-5 lg:grid-cols-2">
          <Card icon={<Puzzle className="h-4 w-4 text-violet-300" />} title={`技能包${skills.data ? `（${skills.data.skills.length}）` : ''}`}>
            {err(skills.error)}
            {skills.data && skills.data.skills.length === 0 && <p className="text-xs text-zinc-500">没有加载任何技能包（放在项目的 skills/ 目录下）</p>}
            <ul className="space-y-2.5">
              {skills.data?.skills.map((s) => (
                <li key={s.tool_id} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-white">{s.name}</span>
                    <span className="font-mono text-[10px] text-zinc-600">v{s.version}</span>
                    <span className={`text-[10px] ${sideEffectTone(s.side_effect)}`}>{SIDE_EFFECT_LABELS[s.side_effect] ?? s.side_effect}</span>
                    <span className="text-[10px] text-zinc-500">{s.has_entry ? `可执行 · ${s.runtime}` : '纯知识包'}</span>
                  </div>
                  <p className="mt-1 text-xs leading-5 text-zinc-400">{s.description}</p>
                </li>
              ))}
            </ul>
            {skills.data && skills.data.skipped.length > 0 && (
              <div className="mt-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.05] p-3 text-xs text-amber-200">
                {skills.data.skipped.length} 个技能包加载失败：
                <ul className="mt-1 list-disc pl-4 text-zinc-400">
                  {skills.data.skipped.map((k, i) => <li key={i}>{k.name || k.dir || '未知'}：{k.reason || k.error || '原因未知'}</li>)}
                </ul>
              </div>
            )}
          </Card>

          <Card icon={<ShieldCheck className="h-4 w-4 text-violet-300" />} title={`能力清单${caps.data ? `（${caps.data.length}）` : ''}`}>
            {err(caps.error)}
            {caps.data && caps.data.length === 0 && <p className="text-xs text-zinc-500">能力注册表为空</p>}
            <div className="max-h-80 overflow-y-auto">
              <table className="w-full text-left text-xs">
                <tbody>
                  {caps.data?.map((c) => (
                    <tr key={c.id} className="border-b border-white/[0.05] last:border-0">
                      <td className="py-1.5 pr-2 font-mono text-zinc-200">{c.id}</td>
                      <td className="py-1.5 pr-2 font-mono text-[10px] text-zinc-600">v{c.version}</td>
                      <td className={`py-1.5 pr-2 ${sideEffectTone(c.side_effect)}`}>{SIDE_EFFECT_LABELS[c.side_effect] ?? c.side_effect}</td>
                      <td className="py-1.5 text-right text-zinc-500">
                        {c.idempotent && '幂等 '}{c.can_spawn_agents && '可派生 Agent'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        <div className="grid gap-5 lg:grid-cols-3">
          <Card icon={<KeyRound className="h-4 w-4 text-violet-300" />} title="项目密钥">
            {err(secrets.error)}
            {secrets.data && !secrets.data.available && <p className="text-xs text-zinc-500">密钥仓未启用</p>}
            {secrets.data?.available && secrets.data.count === 0 && <p className="text-xs text-zinc-500">还没有写入项目密钥。审批设计书时如需密钥会提示录入。</p>}
            <ul className="space-y-1">
              {secrets.data?.entries.map((e, i) => {
                const name = String(e.name ?? e.key ?? e.id ?? Object.values(e).find((v) => typeof v === 'string') ?? `密钥 ${i + 1}`);
                return <li key={i} className="font-mono text-xs text-zinc-300">{name} <span className="text-zinc-600">••••••</span></li>;
              })}
            </ul>
            <p className="mt-3 text-[11px] text-zinc-600">只显示名称，不会返回密钥值。</p>
          </Card>

          <Card icon={<MessagesSquare className="h-4 w-4 text-violet-300" />} title="AI 对话会话">
            {err(chats.error)}
            {chats.data && (
              <>
                <div className="text-2xl font-semibold text-white">{chats.data.sessions}</div>
                <p className="mt-1 text-xs text-zinc-500">后端内存中保留的 Copilot 会话数，重启后清空</p>
                {chats.data.session_ids.length > 0 && (
                  <ul className="mt-2 space-y-0.5">{chats.data.session_ids.slice(0, 6).map((id) => <li key={id} className="font-mono text-[11px] text-zinc-600">{id}</li>)}</ul>
                )}
              </>
            )}
          </Card>

          <Card icon={<Bell className="h-4 w-4 text-violet-300" />} title="通知通道">
            <p className="text-xs text-zinc-500">发送一条测试消息，检查 IM 通知是否配置成功。</p>
            <button type="button" onClick={() => void testNotify()} disabled={notifying}
              className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-violet-400/30 bg-violet-400/10 px-3 py-1.5 text-xs font-medium text-violet-200 transition-colors hover:bg-violet-400/20 disabled:opacity-50">
              {notifying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bell className="h-3.5 w-3.5" />}发送测试通知
            </button>
            {notifyResult && <p className="mt-2 text-xs text-zinc-300">{notifyResult}</p>}
          </Card>
        </div>
      </div>
    </div>
  );
}
