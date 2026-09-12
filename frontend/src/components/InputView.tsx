/**
 * 输入需求页面
 */

import { useState } from 'react';
import { ArrowUpRight, Bot, FileText, Loader2, Send, ShieldCheck, Sparkles, Zap } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { QuickTaskResponse } from '../types';

const examples = [
  '构建一个支持团队协作的知识库，包含搜索、权限和版本历史',
  '为现有 API 增加限流、审计日志和可观测性指标',
  '设计一个从数据采集到报表导出的自动化工作流',
];

export function InputView() {
  const [isLoading, setIsLoading] = useState(false);
  const [quickLoading, setQuickLoading] = useState(false);
  const [quickResult, setQuickResult] = useState<QuickTaskResponse | null>(null);
  const { inputText, setInputText, setCurrentDesign, setCurrentView, addLog } = useAppStore();

  const runDesign = async () => {
    if (!inputText.trim()) return;

    setIsLoading(true);
    setQuickResult(null);
    addLog({ type: 'log', level: 'info', message: '开始生成设计书...' });

    try {
      const design = await apiClient.createDesign(inputText);
      setCurrentDesign(design);
      setCurrentView('design');
      addLog({
        type: 'log',
        level: 'success',
        message: `设计书生成完成: ${design.design_id}`,
      });
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `生成失败: ${error}`,
      });
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void runDesign();
  };

  const handleQuickTask = async () => {

    if (!inputText.trim()) return;

    setQuickLoading(true);
    setQuickResult(null);
    addLog({ type: 'log', level: 'info', message: `任务模式识别: ${inputText}` });

    try {
      const result = await apiClient.quickTask(inputText);
      setQuickResult(result);
      if (result.mode === 'direct' && result.result?.success) {
        addLog({ type: 'log', level: 'success', message: `快速任务完成: ${result.tool_id}:${result.instruction}` });
      } else if (result.mode === 'direct') {
        addLog({ type: 'log', level: 'warning', message: `快速任务被拦截或失败: ${result.result?.error || result.result?.blocked_reason || '未知'}` });
      } else {
        addLog({ type: 'log', level: 'info', message: '识别为开发任务，进入设计书流程' });
      }
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `快速执行失败: ${error}`,
      });
    } finally {
      setQuickLoading(false);
    }
  };

  return (
    <div className="min-h-full px-4 py-8 sm:px-8 sm:py-12">
      <div className="mx-auto max-w-5xl">
        <section className="mb-8 max-w-2xl">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-violet-400/20 bg-violet-400/10 px-3 py-1.5 text-[11px] font-medium text-violet-200">
            <Sparkles className="h-3.5 w-3.5" />
            从需求到可执行计划
          </div>
          <h2 className="text-3xl font-semibold tracking-[-0.03em] text-white sm:text-4xl">
            描述你想要构建的东西
          </h2>
          <p className="mt-3 max-w-xl text-sm leading-6 text-zinc-400 sm:text-[15px]">
            EAS 会先拆解目标、评估技术方案和工具边界，再生成一份可以审阅、批准和恢复的执行计划。
          </p>
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_260px]">
          <form onSubmit={handleSubmit} className="panel-surface overflow-hidden shadow-2xl shadow-black/10">
            <div className="border-b border-white/[0.07] px-5 py-4 sm:px-6">
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-violet-500/15 text-violet-300">
                  <FileText className="h-4 w-4" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-white">任务说明</h3>
                  <p className="mt-0.5 text-xs text-zinc-500">目标越清晰，设计书越可执行</p>
                </div>
              </div>
            </div>

            <div className="p-5 sm:p-6">
              <textarea
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                placeholder="描述你想要实现的功能和技术要求..."
                className="min-h-[250px] w-full resize-none rounded-xl border border-white/[0.1] bg-black/20 p-4 text-sm leading-6 text-white placeholder:text-zinc-600 transition-colors focus:border-violet-400/60 focus:bg-black/30 focus:outline-none sm:min-h-[300px]"
                disabled={isLoading}
              />

              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-[11px] text-zinc-600">
                <span>{inputText.length > 0 ? `${inputText.length} 个字符` : '支持自然语言描述'}</span>
                <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-3.5 w-3.5 text-emerald-400/80" />生成前可审阅与修改</span>
              </div>

              <div className="mt-5 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
                <span className="text-xs text-zinc-600">⌘ Enter 也可以提交设计书</span>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <button
                    type="button"
                    onClick={() => void handleQuickTask()}
                    disabled={quickLoading || !inputText.trim()}
                    title="识别为直接工具（读取/搜索/运行测试/语法检查）时立即执行并展示结果；否则进入设计书流程"
                    className="inline-flex items-center justify-center gap-2 rounded-xl border border-emerald-400/25 bg-emerald-400/[0.06] px-4 py-3 text-sm font-semibold text-emerald-200 transition-colors hover:bg-emerald-400/15 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {quickLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
                    <span>快速执行</span>
                  </button>
                  <button
                    type="submit"
                    disabled={isLoading || !inputText.trim()}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-600 px-5 py-3 text-sm font-semibold text-white shadow-lg shadow-violet-950/30 transition-all hover:bg-violet-500 disabled:cursor-not-allowed disabled:bg-white/[0.07] disabled:text-zinc-600 disabled:shadow-none"
                  >
                    {isLoading ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span>生成中...</span>
                      </>
                    ) : (
                      <>
                        <Send className="h-4 w-4" />
                        <span>生成设计书</span>
                        <ArrowUpRight className="h-3.5 w-3.5" />
                      </>
                    )}
                  </button>
                </div>
              </div>

              {quickResult && (
                <div className="mt-4 rounded-xl border border-white/[0.08] bg-black/15 p-4">
                  <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-zinc-300">
                    <Bot className="h-3.5 w-3.5 text-emerald-300" />
                    快速执行结果
                  </div>
                  {quickResult.mode === 'graph' ? (
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-xs leading-5 text-zinc-400">
                        识别为<span className="mx-1 text-violet-300">开发任务</span>
                        <span className="text-zinc-600">{quickResult.reason}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => void runDesign()}
                        disabled={isLoading}
                        className="shrink-0 rounded-lg bg-violet-600 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-violet-500 disabled:opacity-40"
                      >
                        进入设计书流程
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2 text-xs">
                        <span className="rounded-md border border-emerald-400/20 bg-emerald-400/[0.08] px-2 py-0.5 text-emerald-200">direct</span>
                        <span className="font-mono text-zinc-300">{quickResult.tool_id}:{quickResult.instruction}</span>
                        {quickResult.result?.success ? (
                          <span className="ml-auto text-emerald-300">成功</span>
                        ) : (
                          <span className="ml-auto text-red-300">失败/拦截</span>
                        )}
                      </div>
                      {quickResult.result?.success ? (
                        (() => {
                          const r = quickResult.result;
                          const text = typeof r.content === 'string' ? r.content
                            : (r.path ? `写入: ${r.path}` : null);
                          return text ? (
                            <pre className="overflow-x-auto rounded-lg border border-white/[0.06] bg-black/20 p-3 font-mono text-[11px] leading-5 text-emerald-100/90">{text}</pre>
                          ) : (
                            <div className="text-xs text-zinc-400">{JSON.stringify(r, null, 2)}</div>
                          );
                        })()
                      ) : (
                        <div className="text-xs text-red-300/90">{quickResult.result?.error || quickResult.result?.blocked_reason || '执行未成功（可能被安全策略拒绝）'}</div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </form>

          <aside className="space-y-4">
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-4">
              <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-zinc-300">
                <Sparkles className="h-3.5 w-3.5 text-violet-300" />
                试试这样描述
              </div>
              <div className="space-y-2">
                {examples.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setInputText(example)}
                    disabled={isLoading}
                    className="group w-full rounded-lg border border-white/[0.07] bg-black/15 p-3 text-left text-xs leading-5 text-zinc-400 transition-colors hover:border-violet-400/30 hover:bg-violet-400/[0.06] hover:text-zinc-200 disabled:opacity-40"
                  >
                    <span>{example}</span>
                    <ArrowUpRight className="mt-1 h-3.5 w-3.5 text-zinc-700 transition-colors group-hover:text-violet-300" />
                  </button>
                ))}
              </div>
            </div>

            <div className="rounded-xl border border-emerald-400/15 bg-emerald-400/[0.04] p-4">
              <div className="flex items-start gap-3">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />
                <div>
                  <div className="text-xs font-semibold text-emerald-200">安全流程内置</div>
                  <p className="mt-1.5 text-[11px] leading-5 text-zinc-500">所有高风险工具调用都会在执行前进入审批和审计流程。</p>
                </div>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
