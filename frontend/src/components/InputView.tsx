/**
 * 输入需求页面
 */

import { useState } from 'react';
import { ArrowUpRight, FileText, Loader2, Send, ShieldCheck, Sparkles } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

const examples = [
  '构建一个支持团队协作的知识库，包含搜索、权限和版本历史',
  '为现有 API 增加限流、审计日志和可观测性指标',
  '设计一个从数据采集到报表导出的自动化工作流',
];

export function InputView() {
  const [isLoading, setIsLoading] = useState(false);
  const { inputText, setInputText, setCurrentDesign, setCurrentView, addLog } = useAppStore();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!inputText.trim()) return;

    setIsLoading(true);
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
                <span className="text-xs text-zinc-600">⌘ Enter 也可以提交</span>
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
