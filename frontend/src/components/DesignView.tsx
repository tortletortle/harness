/**
 * 设计书查看和审批页面
 */

import { useState } from 'react';
import { CheckCircle, XCircle, Loader2, ChevronDown, ChevronRight, MessageSquare } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

function openSecretsIfNeeded(
  secrets: { prompt_shown?: boolean; entry_url?: string } | undefined,
  addLog: (entry: { type: 'log'; level: 'info' | 'success' | 'error'; message: string }) => void,
) {
  if (secrets?.prompt_shown && secrets.entry_url) {
    window.open(secrets.entry_url, '_blank', 'noopener,noreferrer');
    addLog({ type: 'log', level: 'info', message: '该设计声明了项目密钥，已打开本地录入页面' });
  }
}

export function DesignView() {
  const { currentDesign, setCurrentDesign, setCurrentPlan, setCurrentView, addLog } = useAppStore();
  const [isApproving, setIsApproving] = useState(false);
  const [isGeneratingPlan, setIsGeneratingPlan] = useState(false);
  const [isFulfilling, setIsFulfilling] = useState(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['architecture', 'tools']));
  const [showFeedbackDialog, setShowFeedbackDialog] = useState(false);
  const [feedback, setFeedback] = useState('');

  if (!currentDesign) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500">
        <div className="text-center">
          <p className="mb-2">暂无设计书</p>
          <button
            onClick={() => useAppStore.getState().setCurrentView('input')}
            className="helm-link text-sm"
          >
            去创建一个
          </button>
        </div>
      </div>
    );
  }

  const toggleSection = (section: string) => {
    const newExpanded = new Set(expandedSections);
    if (newExpanded.has(section)) {
      newExpanded.delete(section);
    } else {
      newExpanded.add(section);
    }
    setExpandedSections(newExpanded);
  };

  const missingTools = currentDesign.part2?.missing_tools ?? [];
  const toolsIncomplete = missingTools.length > 0;
  const approved = Boolean(currentDesign.user_approval);
  const busy = isApproving || isGeneratingPlan || isFulfilling;

  const handleFulfillTools = async () => {
    setIsFulfilling(true);
    addLog({ type: 'log', level: 'info', message: '正在按缺口补齐/映射工具…' });
    try {
      const result = await apiClient.fulfillDesignTools(currentDesign.design_id);
      setCurrentDesign(result.design);
      addLog({
        type: 'log',
        level: result.ready ? 'success' : 'error',
        message: result.ready
          ? '工具已齐，可以批准'
          : `仍有缺口：${result.missing_tools.join(', ') || '未知'}`,
      });
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `对齐工具失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setIsFulfilling(false);
    }
  };

  const handleGeneratePlan = async () => {
    setIsGeneratingPlan(true);
    addLog({ type: 'log', level: 'info', message: '正在生成执行计划…' });
    try {
      const response = await apiClient.generatePlanFromDesign(currentDesign.design_id);
      if (response.toolsIncomplete) {
        if (response.design) setCurrentDesign(response.design);
        addLog({
          type: 'log',
          level: 'error',
          message: response.message || `工具未齐：${(response.missing_tools || []).join(', ')}`,
        });
        return;
      }
      if (response.design) setCurrentDesign(response.design);
      if (response.plan) {
        setCurrentPlan(response.plan);
        setCurrentView('plan');
        addLog({
          type: 'log',
          level: 'success',
          message: `执行计划已生成: ${response.plan.plan_id}`,
        });
      }
      openSecretsIfNeeded(response.secrets_prompt, addLog);
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `生成计划失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setIsGeneratingPlan(false);
    }
  };

  const handleApprove = async (doApprove: boolean) => {
    if (!doApprove && !showFeedbackDialog) {
      setShowFeedbackDialog(true);
      return;
    }

    setIsApproving(true);
    addLog({
      type: 'log',
      level: 'info',
      message: doApprove ? '批准设计书...' : '拒绝设计书...',
    });

    try {
      const response = await apiClient.approveDesign(
        currentDesign.design_id,
        doApprove,
        !doApprove ? feedback : undefined,
      );

      if (doApprove && response.toolsIncomplete) {
        if (response.design) setCurrentDesign(response.design);
        addLog({
          type: 'log',
          level: 'error',
          message: response.message || `工具未齐：${(response.missing_tools || []).join(', ')}`,
        });
        return;
      }

      if (doApprove) {
        if (response.design) setCurrentDesign(response.design);
        else setCurrentDesign({ ...currentDesign, user_approval: true });
        addLog({ type: 'log', level: 'success', message: '设计书已批准，可生成执行计划' });
        openSecretsIfNeeded(response.secrets_prompt, addLog);
      } else if (response.revised && response.design) {
        setCurrentDesign(response.design);
        setShowFeedbackDialog(false);
        setFeedback('');
        addLog({
          type: 'log',
          level: 'success',
          message: 'AI已根据您的反馈修订设计书，请重新审阅',
        });
      } else {
        setCurrentDesign(null);
        setCurrentView('input');
        setShowFeedbackDialog(false);
        setFeedback('');
        addLog({ type: 'log', level: 'info', message: '设计书已拒绝' });
      }
    } catch (error) {
      addLog({
        type: 'log',
        level: 'error',
        message: `审批失败: ${error instanceof Error ? error.message : String(error)}`,
      });
    } finally {
      setIsApproving(false);
    }
  };

  const { part1, part2 } = currentDesign;

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl space-y-5">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-white">设计书审阅</h2>
          <p className="mt-1 text-xs text-zinc-500">设计书 ID：<code className="font-mono text-zinc-400">{currentDesign.design_id}</code> · 审阅架构、工具和执行边界</p>
        </div>

        {/* 用户需求 */}
        <div className="panel-surface p-4">
          <h3 className="text-sm font-medium text-white mb-2">用户需求</h3>
          <p className="text-sm text-zinc-300">{currentDesign.user_request}</p>
        </div>

        {/* 架构设计 */}
        <div className="panel-surface">
          <button
            onClick={() => toggleSection('architecture')}
            className="w-full flex items-center justify-between p-4 hover:bg-zinc-800/50 transition-colors"
          >
            <h3 className="text-sm font-medium text-white">架构设计</h3>
            {expandedSections.has('architecture') ? (
              <ChevronDown className="w-4 h-4 text-zinc-400" />
            ) : (
              <ChevronRight className="w-4 h-4 text-zinc-400" />
            )}
          </button>
          
          {expandedSections.has('architecture') && (
            <div className="px-4 pb-4 space-y-4">
              {/* 难度评估 */}
              <div>
                <h4 className="text-xs font-medium text-zinc-400 mb-2">难度评估</h4>
                <div className="bg-zinc-950 rounded p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-medium text-white">{part1.difficulty.level}</span>
                  </div>
                  <p className="text-xs text-zinc-400">{part1.difficulty.reasoning}</p>
                </div>
              </div>

              {/* 组件 */}
              <div>
                <h4 className="text-xs font-medium text-zinc-400 mb-2">系统组件</h4>
                <div className="bg-zinc-950 rounded p-3 space-y-1">
                  {part1.architecture.components.map((comp, i) => (
                    <div key={i} className="text-xs text-zinc-300">• {comp}</div>
                  ))}
                </div>
              </div>

              {/* 技术栈 */}
              <div>
                <h4 className="text-xs font-medium text-zinc-400 mb-2">技术栈</h4>
                <div className="flex flex-wrap gap-2">
                  {part1.architecture.tech_stack.map((tech, i) => (
                    <span 
                      key={i}
                      className="helm-chip px-2 py-1 text-xs rounded"
                    >
                      {tech}
                    </span>
                  ))}
                </div>
              </div>

              {/* 数据流 */}
              <div>
                <h4 className="text-xs font-medium text-zinc-400 mb-2">数据流</h4>
                <div className="bg-zinc-950 rounded p-3 space-y-1">
                  {part1.architecture.data_flow.map((flow, i) => (
                    <div key={i} className="text-xs text-zinc-300">
                      {i + 1}. {flow}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* 工具评估 */}
        <div className="panel-surface">
          <button
            onClick={() => toggleSection('tools')}
            className="w-full flex items-center justify-between p-4 hover:bg-zinc-800/50 transition-colors"
          >
            <h3 className="text-sm font-medium text-white">工具评估</h3>
            {expandedSections.has('tools') ? (
              <ChevronDown className="w-4 h-4 text-zinc-400" />
            ) : (
              <ChevronRight className="w-4 h-4 text-zinc-400" />
            )}
          </button>
          
          {expandedSections.has('tools') && (
            <div className="px-4 pb-4 space-y-4">
              {/* 可用工具 */}
              <div>
                <h4 className="text-xs font-medium text-zinc-400 mb-2">可用工具</h4>
                <div className="space-y-2">
                  {part2.available_tools.map((tool, i) => (
                    <div key={i} className="bg-zinc-950 rounded p-3">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium text-green-400">{tool.tool_id}</span>
                        {tool.quality && (
                          <span className="text-xs text-zinc-500">({tool.quality})</span>
                        )}
                      </div>
                      <p className="text-xs text-zinc-400">{tool.purpose}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* 缺失工具 */}
              {part2.missing_tools.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-zinc-400 mb-2">缺失工具</h4>
                  <div className="space-y-2">
                    {part2.missing_tools.map((tool, i) => (
                      <div key={i} className="bg-zinc-950 rounded p-3 border-l-2 border-red-500">
                        <div className="text-sm font-medium text-red-400 mb-1">{tool.tool_id}</div>
                        <p className="text-xs text-zinc-400">{tool.purpose}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 建议 */}
              {part2.recommendations.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-zinc-400 mb-2">建议</h4>
                  <div className="bg-zinc-950 rounded p-3 space-y-1">
                    {part2.recommendations.map((rec, i) => (
                      <div key={i} className="text-xs text-zinc-300">• {rec}</div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* 反馈对话框 */}
        {showFeedbackDialog && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="panel-surface p-6 max-w-md w-full mx-4">
              <div className="flex items-center gap-2 mb-4">
                <MessageSquare className="w-5 h-5 helm-accent" />
                <h3 className="text-lg font-medium text-white">请输入反馈意见</h3>
              </div>
              <p className="text-sm text-zinc-400 mb-4">
                请告诉AI您对设计书的不满意之处，AI将根据您的反馈重新修订设计。
              </p>
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="例如：组件划分不够细致，需要进一步拆分..."
                className="helm-field w-full h-32 bg-zinc-950 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none resize-none"
                autoFocus
              />
              <div className="flex gap-3 mt-4">
                <button
                  onClick={() => {
                    setShowFeedbackDialog(false);
                    setFeedback('');
                  }}
                  disabled={busy}
                  className="flex-1 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white rounded transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={() => handleApprove(false)}
                  disabled={busy || !feedback.trim()}
                  className="helm-btn-primary flex-1 px-4 py-2 rounded flex items-center justify-center gap-2"
                >
                  {isApproving ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    '提交反馈'
                  )}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 审批按钮 */}
        {toolsIncomplete && (
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2">
            <p className="flex-1 text-xs text-amber-200">
              还有 {missingTools.length} 个工具未就绪（{missingTools.map((t) => t.tool_id).join('、')}）。可先「重试补齐」，或直接批准让后端再对齐一次。
            </p>
            <button
              type="button"
              onClick={() => void handleFulfillTools()}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-md border border-amber-400/30 px-2.5 py-1 text-xs text-amber-100 hover:bg-amber-400/10 disabled:opacity-40"
            >
              {isFulfilling ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
              重试补齐工具
            </button>
          </div>
        )}
        <div className="flex flex-wrap gap-3">
          {!approved ? (
            <button
              onClick={() => handleApprove(true)}
              disabled={busy}
              className="helm-btn-primary flex flex-1 items-center justify-center gap-2 rounded-lg py-3 font-medium disabled:opacity-40"
            >
              {isApproving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <CheckCircle className="h-4 w-4" />
                  <span>批准</span>
                </>
              )}
            </button>
          ) : (
            <button
              onClick={() => void handleGeneratePlan()}
              disabled={busy}
              className="helm-btn-primary flex flex-1 items-center justify-center gap-2 rounded-lg py-3 font-medium disabled:opacity-40"
            >
              {isGeneratingPlan ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <CheckCircle className="h-4 w-4" />
                  <span>生成执行计划</span>
                </>
              )}
            </button>
          )}

          <button
            onClick={() => handleApprove(false)}
            disabled={busy || approved}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-red-600 py-3 font-medium text-white transition-colors hover:bg-red-700 disabled:bg-zinc-800 disabled:text-zinc-600"
          >
            <XCircle className="h-4 w-4" />
            <span>拒绝</span>
          </button>
        </div>
        {approved && (
          <p className="text-center text-xs text-zinc-500">设计书已批准 · 过程条「计划」一步等待生成</p>
        )}
      </div>
    </div>
  );
}
