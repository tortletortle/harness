/**
 * 执行计划查看页面
 */

import { useState, useMemo, useEffect } from 'react';
import { Play, Loader2, ChevronDown, ChevronRight, Search, CheckCircle, XCircle, Clock } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

export function PlanView() {
  const { currentPlan, isExecuting, setIsExecuting, addLog, logs } = useAppStore();
  const [expandedSegments, setExpandedSegments] = useState<Set<string>>(new Set());
  const [logSearch, setLogSearch] = useState('');
  const [stepStatus, setStepStatus] = useState<Record<string, 'pending' | 'running' | 'success' | 'error'>>({});

  // 解析日志更新步骤状态
  useEffect(() => {
    if (!currentPlan || logs.length === 0) return;

    const latestLog = logs[logs.length - 1];
    const message = latestLog.message;

    // 匹配步骤开始执行
    const startMatch = message.match(/执行步骤 ([^:]+):/);
    if (startMatch) {
      const stepId = startMatch[1];
      setStepStatus(prev => ({ ...prev, [stepId]: 'running' }));
      return;
    }

    // 匹配步骤成功 - 支持两种格式: "✓ 步骤" 和 "[OK] 步骤"
    const successMatch = message.match(/(?:✓|\[OK\]) 步骤 ([^ ]+) 完成/);
    if (successMatch) {
      const stepId = successMatch[1];
      setStepStatus(prev => ({ ...prev, [stepId]: 'success' }));
      return;
    }

    // 匹配步骤失败 - 支持两种格式: "✗ 步骤" 和 "[ERROR] 步骤"
    const errorMatch = message.match(/(?:✗|\[ERROR\]) 步骤 ([^ ]+) 失败/);
    if (errorMatch) {
      const stepId = errorMatch[1];
      setStepStatus(prev => ({ ...prev, [stepId]: 'error' }));
      return;
    }
  }, [logs, currentPlan]);

  // 执行开始时清空状态
  useEffect(() => {
    if (isExecuting && currentPlan) {
      // 重置所有步骤为 pending
      const initialStatus: Record<string, 'pending'> = {};
      currentPlan.segments.forEach(seg => {
        seg.steps.forEach(step => {
          initialStatus[step.step_id] = 'pending';
        });
      });
      setStepStatus(initialStatus);
    }
  }, [isExecuting, currentPlan]);

  const filteredLogs = useMemo(() => {
    if (!logSearch.trim()) return logs;
    const q = logSearch.toLowerCase();
    return logs.filter(l => l.message.toLowerCase().includes(q));
  }, [logs, logSearch]);

  if (!currentPlan) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500">
        <div className="text-center">
          <p className="mb-2">暂无执行计划</p>
          <button
            onClick={() => useAppStore.getState().setCurrentView('design')}
            className="text-blue-500 hover:text-blue-400 text-sm"
          >
            去审批设计书
          </button>
        </div>
      </div>
    );
  }

  const getStepStatusIcon = (stepId: string) => {
    const status = stepStatus[stepId] || 'pending';
    switch (status) {
      case 'running':
        return <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />;
      case 'success':
        return <CheckCircle className="w-4 h-4 text-green-400" />;
      case 'error':
        return <XCircle className="w-4 h-4 text-red-400" />;
      default:
        return <Clock className="w-4 h-4 text-zinc-600" />;
    }
  };

  const getStepStatusColor = (stepId: string) => {
    const status = stepStatus[stepId] || 'pending';
    switch (status) {
      case 'running':
        return 'border-blue-500';
      case 'success':
        return 'border-green-500';
      case 'error':
        return 'border-red-500';
      default:
        return 'border-zinc-700';
    }
  };

  const getSegmentStatus = (segmentId: string) => {
    const segment = currentPlan?.segments.find(s => s.segment_id === segmentId);
    if (!segment) return 'pending';
    
    const stepStatuses = segment.steps.map(s => stepStatus[s.step_id] || 'pending');
    
    if (stepStatuses.some(s => s === 'error')) return 'error';
    if (stepStatuses.some(s => s === 'running')) return 'running';
    if (stepStatuses.every(s => s === 'success')) return 'success';
    if (stepStatuses.some(s => s === 'success')) return 'partial';
    return 'pending';
  };

  const getSegmentIcon = (segmentId: string) => {
    const status = getSegmentStatus(segmentId);
    switch (status) {
      case 'running':
        return <Loader2 className="w-5 h-5 text-blue-400 animate-spin" />;
      case 'success':
        return <CheckCircle className="w-5 h-5 text-green-400" />;
      case 'error':
        return <XCircle className="w-5 h-5 text-red-400" />;
      case 'partial':
        return (
          <div className="w-5 h-5 rounded-full border-2 border-blue-400 flex items-center justify-center">
            <div className="w-2 h-2 rounded-full bg-blue-400" />
          </div>
        );
      default:
        return (
          <span className="flex items-center justify-center w-5 h-5 rounded-full bg-zinc-700 text-white text-xs font-bold">
            {currentPlan?.segments.findIndex(s => s.segment_id === segmentId)! + 1}
          </span>
        );
    }
  };

  const toggleSegment = (segmentId: string) => {
    const newExpanded = new Set(expandedSegments);
    if (newExpanded.has(segmentId)) {
      newExpanded.delete(segmentId);
    } else {
      newExpanded.add(segmentId);
    }
    setExpandedSegments(newExpanded);
  };

  const handleExecute = async () => {
    setIsExecuting(true);
    addLog({ 
      type: 'log', 
      level: 'info', 
      message: `开始执行计划: ${currentPlan.plan_id}` 
    });

    try {
      await apiClient.executePlan(currentPlan.plan_id);
    } catch (error) {
      addLog({ 
        type: 'log', 
        level: 'error', 
        message: `执行失败: ${error}` 
      });
      setIsExecuting(false);
    }
  };

  const totalSteps = currentPlan.segments.reduce((sum, seg) => sum + seg.steps.length, 0);
  
  // 计算执行进度
  const completedSteps = Object.values(stepStatus).filter(s => s === 'success').length;
  const failedSteps = Object.values(stepStatus).filter(s => s === 'error').length;
  const progressPercent = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Plan content */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex-1">
              <h2 className="text-2xl font-bold text-white mb-2">执行计划</h2>
              <p className="text-sm text-zinc-400">
                ID: {currentPlan.plan_id} · {currentPlan.segments.length} 个阶段 · {totalSteps} 个步骤
              </p>
              
              {/* 进度条 */}
              {isExecuting && (
                <div className="mt-3">
                  <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
                    <span>执行进度</span>
                    <span>{completedSteps}/{totalSteps} 步骤 ({progressPercent}%)</span>
                  </div>
                  <div className="w-full h-2 bg-zinc-800 rounded-full overflow-hidden">
                    <div 
                      className="h-full bg-gradient-to-r from-blue-600 to-blue-400 transition-all duration-300"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                  {failedSteps > 0 && (
                    <div className="text-xs text-red-400 mt-1">
                      {failedSteps} 个步骤失败
                    </div>
                  )}
                </div>
              )}
            </div>

            <button
              onClick={handleExecute}
              disabled={isExecuting}
              className="flex items-center gap-2 bg-green-600 hover:bg-green-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium px-6 py-3 rounded-lg transition-colors"
            >
              {isExecuting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>执行中...</span>
                </>
              ) : (
                <>
                  <Play className="w-4 h-4" />
                  <span>开始执行</span>
                </>
              )}
            </button>
          </div>

          {/* Segments */}
          <div className="space-y-4">
            {currentPlan.segments.map((segment) => (
              <div key={segment.segment_id} className="bg-zinc-900 border border-zinc-800 rounded-lg">
                {/* Segment Header */}
                <button
                  onClick={() => toggleSegment(segment.segment_id)}
                  className="w-full flex items-center justify-between p-4 hover:bg-zinc-800/50 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    {getSegmentIcon(segment.segment_id)}
                    <div className="text-left">
                      <h3 className="text-sm font-medium text-white">{segment.description}</h3>
                      <p className="text-xs text-zinc-500">{segment.steps.length} 个步骤</p>
                    </div>
                  </div>
                  {expandedSegments.has(segment.segment_id) ? (
                    <ChevronDown className="w-4 h-4 text-zinc-400" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-zinc-400" />
                  )}
                </button>

                {/* Steps */}
                {expandedSegments.has(segment.segment_id) && (
                  <div className="px-4 pb-4 space-y-2">
                    {segment.steps.map((step) => (
                      <div key={step.step_id} className={`bg-zinc-950 rounded p-3 border-l-2 ${getStepStatusColor(step.step_id)}`}>
                        <div className="flex items-start gap-3">
                          {getStepStatusIcon(step.step_id)}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-2">
                              <h4 className="text-sm font-medium text-white">{step.description}</h4>
                              <span className="text-xs px-2 py-0.5 bg-blue-600/20 text-blue-400 rounded">
                                {step.tool}
                              </span>
                            </div>
                            
                            {/* Inputs */}
                            {Object.keys(step.inputs).length > 0 && (
                              <div className="mb-2">
                                <div className="text-xs text-zinc-500 mb-1">输入参数:</div>
                                <div className="bg-zinc-900 rounded p-2 font-mono text-xs text-zinc-300">
                                  {JSON.stringify(step.inputs, null, 2)}
                                </div>
                              </div>
                            )}
                            
                            {/* Expected Outputs */}
                            {step.expected_outputs.length > 0 && (
                              <div>
                                <div className="text-xs text-zinc-500 mb-1">预期输出:</div>
                                <div className="space-y-0.5">
                                  {step.expected_outputs.map((output, i) => (
                                    <div key={i} className="text-xs text-zinc-400">• {output}</div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Log search panel */}
      <div className="h-48 border-t border-zinc-800 bg-zinc-950 flex flex-col">
        <div className="flex items-center gap-2 px-4 py-2 border-b border-zinc-800">
          <Search className="w-4 h-4 text-zinc-500" />
          <input
            type="text"
            value={logSearch}
            onChange={(e) => setLogSearch(e.target.value)}
            placeholder="搜索执行日志..."
            className="flex-1 bg-transparent text-sm text-white placeholder-zinc-600 focus:outline-none"
          />
          <span className="text-xs text-zinc-500">{filteredLogs.length} 条</span>
        </div>
        <div className="flex-1 overflow-y-auto p-2 font-mono text-xs">
          {filteredLogs.length === 0 ? (
            <div className="text-zinc-600 text-center py-4">暂无日志</div>
          ) : (
            filteredLogs.map((log, index) => (
              <div key={index} className="mb-1">
                <span className="text-zinc-600">
                  {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : ''}
                </span>
                {' '}
                <span className={
                  log.level === 'success' ? 'text-green-400' :
                  log.level === 'error' ? 'text-red-400' :
                  log.level === 'warning' ? 'text-yellow-400' :
                  'text-zinc-400'
                }>
                  [{log.level.toUpperCase()}]
                </span>
                {' '}
                <span className="text-zinc-300">{log.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
