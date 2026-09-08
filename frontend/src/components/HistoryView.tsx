/**
 * 历史记录页面
 */

import { useEffect, useState, useMemo } from 'react';
import { Clock, Loader2, FileText, Search } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { Design, ExecutionPlan } from '../types';

export function HistoryView() {
  const [designs, setDesigns] = useState<Design[]>([]);
  const [plans, setPlans] = useState<ExecutionPlan[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'designs' | 'plans'>('designs');
  const [searchQuery, setSearchQuery] = useState('');
  const { setCurrentDesign, setCurrentPlan, setCurrentView, addLog } = useAppStore();

  useEffect(() => {
    loadHistory();
  }, []);

  const loadHistory = async () => {
    setIsLoading(true);
    try {
      const [designsList, plansList] = await Promise.all([
        apiClient.getDesigns(20),
        apiClient.getPlans(20),
      ]);
      setDesigns(designsList);
      setPlans(plansList);
    } catch (error) {
      addLog({ type: 'log', level: 'error', message: `加载历史记录失败: ${error}` });
    } finally {
      setIsLoading(false);
    }
  };

  const filteredDesigns = useMemo(() => {
    if (!searchQuery.trim()) return designs;
    const q = searchQuery.toLowerCase();
    return designs.filter(d => 
      d.user_request.toLowerCase().includes(q) ||
      d.design_id.toLowerCase().includes(q)
    );
  }, [designs, searchQuery]);

  const filteredPlans = useMemo(() => {
    if (!searchQuery.trim()) return plans;
    const q = searchQuery.toLowerCase();
    return plans.filter(p => 
      p.plan_id.toLowerCase().includes(q) ||
      p.design_id.toLowerCase().includes(q)
    );
  }, [plans, searchQuery]);

  const handleSelectDesign = (design: Design) => {
    setCurrentDesign(design);
    setCurrentView('design');
  };

  const handleSelectPlan = (plan: ExecutionPlan) => {
    setCurrentPlan(plan);
    setCurrentView('plan');
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-zinc-600 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl">
        {/* Header */}
        <div className="mb-6">
          <h2 className="text-2xl font-semibold tracking-tight text-white">历史记录</h2>
          <p className="mt-1 text-sm text-zinc-500">查看过往的设计书和执行计划，快速回到工作现场</p>
        </div>

        {/* Search */}
        <div className="mb-4">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索设计书或执行计划..."
              className="w-full rounded-xl border border-white/[0.1] bg-[#111419]/80 py-2.5 pl-10 pr-4 text-sm text-white placeholder:text-zinc-600 transition-colors focus:border-violet-400/50 focus:outline-none"
            />
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6 border-b border-zinc-800">
          <button
            onClick={() => setActiveTab('designs')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'designs'
                ? 'border-blue-600 text-white'
                : 'border-transparent text-zinc-400 hover:text-white'
            }`}
          >
            设计书 ({filteredDesigns.length})
          </button>
          <button
            onClick={() => setActiveTab('plans')}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              activeTab === 'plans'
                ? 'border-blue-600 text-white'
                : 'border-transparent text-zinc-400 hover:text-white'
            }`}
          >
            执行计划 ({filteredPlans.length})
          </button>
        </div>

        {/* Content */}
        {activeTab === 'designs' && (
          <div className="space-y-3">
            {filteredDesigns.length === 0 ? (
              <div className="text-center text-zinc-500 py-12">
                <FileText className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>{searchQuery ? '无匹配结果' : '暂无历史设计书'}</p>
              </div>
            ) : (
              filteredDesigns.map((design) => (
                <button
                  key={design.design_id}
                  onClick={() => handleSelectDesign(design)}
                  className="w-full rounded-xl border border-white/[0.08] bg-[#111419]/90 p-4 text-left transition-colors hover:border-violet-400/25 hover:bg-white/[0.04]"
                >
                  <div className="flex items-start justify-between mb-2">
                    <h3 className="text-sm font-medium text-white line-clamp-1">
                      {design.user_request}
                    </h3>
                    {design.created_at && (
                      <span className="text-xs text-zinc-500 ml-2 flex-shrink-0">
                        {new Date(design.created_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="text-xs text-zinc-600 font-mono">{design.design_id}</code>
                    <span className="text-xs px-2 py-0.5 bg-zinc-800 text-zinc-400 rounded">
                      {design.part1.difficulty.level}
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>
        )}

        {activeTab === 'plans' && (
          <div className="space-y-3">
            {filteredPlans.length === 0 ? (
              <div className="text-center text-zinc-500 py-12">
                <Clock className="w-12 h-12 mx-auto mb-3 opacity-50" />
                <p>{searchQuery ? '无匹配结果' : '暂无历史执行计划'}</p>
              </div>
            ) : (
              filteredPlans.map((plan) => (
                <button
                  key={plan.plan_id}
                  onClick={() => handleSelectPlan(plan)}
                  className="w-full rounded-xl border border-white/[0.08] bg-[#111419]/90 p-4 text-left transition-colors hover:border-violet-400/25 hover:bg-white/[0.04]"
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-white">执行计划</span>
                      <span className="text-xs text-zinc-500">
                        {plan.segments.length} 阶段
                      </span>
                    </div>
                    {plan.created_at && (
                      <span className="text-xs text-zinc-500 flex-shrink-0">
                        {new Date(plan.created_at).toLocaleDateString()}
                      </span>
                    )}
                  </div>
                  <code className="text-xs text-zinc-600 font-mono">{plan.plan_id}</code>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
