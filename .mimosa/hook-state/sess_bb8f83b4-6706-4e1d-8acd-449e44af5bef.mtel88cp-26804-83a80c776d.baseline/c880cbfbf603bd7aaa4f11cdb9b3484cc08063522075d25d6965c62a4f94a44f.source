/**
 * 设计书查看和审批页面
 */

import { useState } from 'react';
import { CheckCircle, XCircle, Loader2, ChevronDown, ChevronRight, MessageSquare } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

export function DesignView() {
  const { currentDesign, setCurrentDesign, setCurrentPlan, setCurrentView, addLog } = useAppStore();
  const [isApproving, setIsApproving] = useState(false);
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
            className="text-blue-500 hover:text-blue-400 text-sm"
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

  const handleApprove = async (approved: boolean) => {
    if (!approved && !showFeedbackDialog) {
      // 拒绝时，先显示反馈对话框
      setShowFeedbackDialog(true);
      return;
    }
    
    setIsApproving(true);
    addLog({ 
      type: 'log', 
      level: 'info', 
      message: approved ? '批准设计书...' : '拒绝设计书...' 
    });

    try {
      const response = await apiClient.approveDesign(
        currentDesign.design_id, 
        approved,
        !approved ? feedback : undefined
      );
      
      if (approved && response.plan) {
        setCurrentPlan(response.plan);
        setCurrentView('plan');
        addLog({ 
          type: 'log', 
          level: 'success', 
          message: `执行计划已生成: ${response.plan.plan_id}` 
        });
      } else if (!approved && response.revised && response.design) {
        // AI已根据反馈修订设计书
        setCurrentDesign(response.design);
        setShowFeedbackDialog(false);
        setFeedback('');
        addLog({ 
          type: 'log', 
          level: 'success', 
          message: 'AI已根据您的反馈修订设计书，请重新审阅' 
        });
      } else {
        // 拒绝且无反馈或修订失败
        setCurrentDesign(null);
        setCurrentView('input');
        setShowFeedbackDialog(false);
        setFeedback('');
        addLog({ 
          type: 'log', 
          level: 'info', 
          message: '设计书已拒绝' 
        });
      }
    } catch (error) {
      addLog({ 
        type: 'log', 
        level: 'error', 
        message: `审批失败: ${error}` 
      });
    } finally {
      setIsApproving(false);
    }
  };

  const { part1, part2 } = currentDesign;

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-bold text-white mb-2">设计书审阅</h2>
          <p className="text-sm text-zinc-400">
            ID: {currentDesign.design_id}
          </p>
        </div>

        {/* 用户需求 */}
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
          <h3 className="text-sm font-medium text-white mb-2">用户需求</h3>
          <p className="text-sm text-zinc-300">{currentDesign.user_request}</p>
        </div>

        {/* 架构设计 */}
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg">
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
                      className="px-2 py-1 bg-blue-600/20 text-blue-400 text-xs rounded"
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
        <div className="bg-zinc-900 border border-zinc-800 rounded-lg">
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
            <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-6 max-w-md w-full mx-4">
              <div className="flex items-center gap-2 mb-4">
                <MessageSquare className="w-5 h-5 text-blue-400" />
                <h3 className="text-lg font-medium text-white">请输入反馈意见</h3>
              </div>
              <p className="text-sm text-zinc-400 mb-4">
                请告诉AI您对设计书的不满意之处，AI将根据您的反馈重新修订设计。
              </p>
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="例如：组件划分不够细致，需要进一步拆分..."
                className="w-full h-32 bg-zinc-950 border border-zinc-700 rounded px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-blue-500 resize-none"
                autoFocus
              />
              <div className="flex gap-3 mt-4">
                <button
                  onClick={() => {
                    setShowFeedbackDialog(false);
                    setFeedback('');
                  }}
                  disabled={isApproving}
                  className="flex-1 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white rounded transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={() => handleApprove(false)}
                  disabled={isApproving || !feedback.trim()}
                  className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white rounded transition-colors flex items-center justify-center gap-2"
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
        <div className="flex gap-4">
          <button
            onClick={() => handleApprove(true)}
            disabled={isApproving}
            className="flex-1 flex items-center justify-center gap-2 bg-green-600 hover:bg-green-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium py-3 rounded-lg transition-colors"
          >
            {isApproving ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <>
                <CheckCircle className="w-4 h-4" />
                <span>批准并生成计划</span>
              </>
            )}
          </button>

          <button
            onClick={() => handleApprove(false)}
            disabled={isApproving}
            className="flex-1 flex items-center justify-center gap-2 bg-red-600 hover:bg-red-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium py-3 rounded-lg transition-colors"
          >
            <XCircle className="w-4 h-4" />
            <span>拒绝</span>
          </button>
        </div>
      </div>
    </div>
  );
}
