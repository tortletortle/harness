/**
 * AI Copilot for the current workspace.
 */

import { useRef, useState } from 'react';
import { Bot, ChevronRight, Loader2, MessageSquarePlus, Send, Sparkles, X } from 'lucide-react';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';

interface ChatMessage { role: 'user' | 'assistant'; content: string }

const GREETING: ChatMessage = { role: 'assistant', content: '你好，我是你的星舵 Copilot。可以帮你分析任务、检查风险，或解释执行结果。' };

const suggestions = ['总结当前任务', '检查执行风险', '下一步该做什么？'];

const copilotStatus: Record<string, string> = {
  online: '已连接',
  connecting: '连接中',
  reconnecting: '正在重连',
  offline: '未连接',
  auth_error: '认证失败',
};

export function AiPanel() {
  const { toggleAiPanel, currentPlan, connectionState } = useAppStore();
  const online = connectionState === 'online';
  const [messages, setMessages] = useState<ChatMessage[]>([GREETING]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const startNewChat = () => {
    sessionIdRef.current = null;
    setInput('');
    setMessages([GREETING]);
  };

  const handleSend = async (preset?: string) => {
    const userMessage = (preset ?? input).trim();
    if (!userMessage || sending) return;
    setInput('');
    setSending(true);
    setMessages((prev) => [...prev, { role: 'user', content: userMessage }]);
    try {
      const result = await apiClient.chat(userMessage, sessionIdRef.current);
      sessionIdRef.current = result.sessionId || sessionIdRef.current;
      const toolNote = result.toolCalls.length ? `\n已调用：${result.toolCalls.map((tc) => tc.tool + (tc.ok ? '' : ' ×')).join('、')}` : '';
      setMessages((prev) => [...prev, { role: 'assistant', content: result.reply + toolNote }]);
    } catch (error) {
      setMessages((prev) => [...prev, { role: 'assistant', content: `抱歉，暂时无法完成：${error}` }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <aside className="helm-ai-panel flex h-full min-h-0 w-full flex-col bg-[#101216]">
      <div className="helm-ai-header flex h-[72px] shrink-0 items-center justify-between border-b border-white/[0.08] px-4">
        <div className="flex items-center gap-3"><div className="helm-icon-well flex h-8 w-8 items-center justify-center rounded-lg"><Bot className="h-4 w-4" /></div><div><div className="text-[13px] font-semibold text-white">AI 副驾</div><div className="flex items-center gap-1.5 text-[10px] text-zinc-500"><span className={`h-1.5 w-1.5 rounded-full ${online ? 'bg-emerald-400' : 'bg-zinc-500'}`} />Helm Copilot · {copilotStatus[connectionState] ?? '未连接'}</div></div></div>
        <div className="flex items-center gap-1"><button type="button" onClick={startNewChat} disabled={sending} title="新对话" aria-label="开始新对话" className="rounded-md p-1.5 text-zinc-500 hover:bg-white/[0.06] hover:text-white disabled:opacity-40"><MessageSquarePlus className="h-4 w-4" /></button><button onClick={toggleAiPanel} title="收起" aria-label="收起 AI 面板" className="rounded-md p-1.5 text-zinc-500 hover:bg-white/[0.06] hover:text-white"><X className="h-4 w-4" /></button></div>
      </div>
      {currentPlan && <div className="border-b border-white/[0.06] px-4 py-3"><div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.15em] text-zinc-600">当前上下文</div><div className="flex items-center gap-2 text-xs text-zinc-300"><span className="h-1.5 w-1.5 rounded-full bg-[var(--app-brand)]" />计划 {currentPlan.plan_id.slice(0, 8)}</div></div>}
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {messages.map((message, index) => <div key={index} className={`flex gap-2.5 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}><div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${message.role === 'user' ? 'bg-white/[0.08] text-zinc-400' : 'helm-icon-well'}`}>{message.role === 'user' ? <span className="text-[10px] font-semibold">你</span> : <Sparkles className="h-3 w-3" />}</div><div className={`max-w-[84%] whitespace-pre-wrap rounded-xl px-3 py-2.5 text-xs leading-relaxed ${message.role === 'user' ? 'helm-bubble-user' : 'border border-white/[0.07] bg-white/[0.035] text-zinc-300'}`}>{message.content}</div></div>)}
        {sending && <div className="flex items-center gap-2 text-xs text-zinc-500"><Loader2 className="h-3.5 w-3.5 animate-spin helm-accent" />Copilot 正在思考…</div>}
      </div>
      <div className="helm-ai-compose shrink-0 border-t border-white/[0.08] p-3"><div className="mb-2 flex flex-wrap gap-1.5">{suggestions.map((suggestion) => <button key={suggestion} type="button" onClick={() => void handleSend(suggestion)} disabled={sending} className="helm-hover-brand flex items-center gap-1 rounded-full border border-white/[0.08] px-2.5 py-1 text-[10px] text-zinc-500 hover:text-[var(--app-brand)] disabled:opacity-40">{suggestion}<ChevronRight className="h-2.5 w-2.5" /></button>)}</div><div className="helm-field flex items-center gap-2 rounded-xl border border-white/[0.1] bg-black/20 p-1.5"><input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void handleSend(); }} placeholder="询问 Copilot…" className="min-w-0 flex-1 bg-transparent px-2 text-xs text-white placeholder:text-zinc-600 focus:outline-none" />{(input.trim() || sending) && <button type="button" onClick={() => void handleSend()} disabled={!input.trim() || sending} title="发送" aria-label="发送" className="helm-btn-primary flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"><Send className="h-3.5 w-3.5" /></button>}</div></div>
    </aside>
  );
}