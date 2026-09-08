/**
 * 工具管理页面
 */

import { useEffect, useState, useMemo } from 'react';
import { Wrench, Loader2, Search } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { Tool } from '../types';

export function ToolsView() {
  const { tools, setTools, addLog } = useAppStore();
  const [isLoading, setIsLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');

  useEffect(() => {
    loadTools();
  }, []);

  const loadTools = async () => {
    setIsLoading(true);
    try {
      const toolsList = await apiClient.getTools();
      setTools(toolsList);
    } catch (error) {
      addLog({ type: 'log', level: 'error', message: `加载工具列表失败: ${error}` });
    } finally {
      setIsLoading(false);
    }
  };

  const filteredTools = useMemo(() => {
    let filtered = tools;
    
    // Category filter
    if (categoryFilter !== 'all') {
      filtered = filtered.filter(t => t.category === categoryFilter);
    }
    
    // Search filter
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      filtered = filtered.filter(t =>
        t.name.toLowerCase().includes(q) ||
        t.tool_id.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q)
      );
    }
    
    return filtered;
  }, [tools, searchQuery, categoryFilter]);

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-zinc-600 animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-6xl">
        {/* Header */}
        <div className="mb-6">
          <h2 className="text-2xl font-semibold tracking-tight text-white">工具库</h2>
          <p className="mt-1 text-sm text-zinc-500">系统可用的工具和执行边界 · 共 {tools.length} 个工具</p>
        </div>

        {/* Category filter */}
        <div className="mb-4">
          <div className="flex gap-2">
            {['all', 'generation', 'search', 'template', 'other'].map(cat => (
              <button
                key={cat}
                onClick={() => setCategoryFilter(cat)}
              className={`rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors ${
                  categoryFilter === cat
                    ? 'border-violet-400/25 bg-violet-500/15 text-violet-100'
                    : 'border-white/[0.07] bg-white/[0.025] text-zinc-500 hover:bg-white/[0.06] hover:text-white'
                }`}
              >
                {cat === 'all' ? '全部' : cat === 'generation' ? '生成' : cat === 'search' ? '搜索' : cat === 'template' ? '模板' : '其他'}
              </button>
            ))}
          </div>
        </div>

        {/* Search */}
        <div className="mb-6">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索工具名称、ID 或描述..."
              className="w-full rounded-xl border border-white/[0.1] bg-[#111419]/80 py-2.5 pl-10 pr-4 text-sm text-white placeholder:text-zinc-600 transition-colors focus:border-violet-400/50 focus:outline-none"
            />
          </div>
        </div>

        {/* Tools Grid */}
        {filteredTools.length === 0 ? (
          <div className="text-center text-zinc-500 py-12">
            <Wrench className="w-12 h-12 mx-auto mb-3 opacity-50" />
            <p>{searchQuery ? '无匹配工具' : '暂无可用工具'}</p>
          </div>
        ) : (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredTools.map((tool) => (
              <ToolCard key={tool.tool_id} tool={tool} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ToolCard({ tool }: { tool: Tool }) {
  const [expanded, setExpanded] = useState(false);
  
  const categoryColors = {
    generation: 'bg-green-600/20 text-green-400 border-green-600/30',
    search: 'bg-blue-600/20 text-blue-400 border-blue-600/30',
    template: 'bg-purple-600/20 text-purple-400 border-purple-600/30',
    other: 'bg-zinc-600/20 text-zinc-400 border-zinc-600/30',
  };
  
  const categoryLabel = {
    generation: '生成',
    search: '搜索',
    template: '模板',
    other: '其他',
  };

  return (
    <div className="rounded-xl border border-white/[0.08] bg-[#111419]/90 p-4 transition-colors hover:border-violet-400/25">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <Wrench className="w-4 h-4 text-blue-400" />
          <h3 className="font-medium text-white text-sm">{tool.name}</h3>
        </div>
        {tool.category && (
          <span className={`text-xs px-2 py-0.5 rounded border ${categoryColors[tool.category] || categoryColors.other}`}>
            {categoryLabel[tool.category] || '其他'}
          </span>
        )}
      </div>

      <p className="text-xs text-zinc-400 mb-3">{tool.description}</p>

      <div className="space-y-2">
        {/* Capabilities */}
        {tool.capabilities && tool.capabilities.length > 0 && (
          <div>
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-xs text-zinc-500 hover:text-zinc-400 mb-1"
            >
              能力 ({tool.capabilities.length}) {expanded ? '▼' : '▶'}
            </button>
            {expanded && (
              <div className="bg-zinc-950 rounded p-2 space-y-1">
                {tool.capabilities.map((cap, i) => (
                  <div key={i} className="text-xs text-zinc-400">• {cap}</div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Requirements */}
        {tool.requirements && tool.requirements.length > 0 && (
          <div>
            <div className="text-xs text-zinc-500 mb-1">需求:</div>
            <div className="flex flex-wrap gap-1">
              {tool.requirements.map((req, i) => (
                <span 
                  key={i}
                  className="text-xs px-2 py-0.5 bg-zinc-800 text-zinc-400 rounded"
                >
                  {req}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="mt-3 pt-3 border-t border-zinc-800">
        <code className="text-xs text-zinc-600 font-mono">{tool.tool_id}</code>
      </div>
    </div>
  );
}
