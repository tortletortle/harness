/**
 * 侧边栏导航
 */

import { 
  FileText, 
  Play, 
  Wrench, 
  Clock, 
  Settings,
  PenSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Terminal,
  Bot,
} from 'lucide-react';
import { useAppStore } from '../stores/appStore';

export function Sidebar() {
  const { 
    currentView, setCurrentView,
    sidebarCollapsed, toggleSidebar,
    toggleLogPanel, logPanelCollapsed,
    toggleAiPanel, aiPanelCollapsed,
  } = useAppStore();

  const navItems = [
    { id: 'input' as const, label: '新建任务', icon: PenSquare },
    { id: 'design' as const, label: '设计书', icon: FileText },
    { id: 'plan' as const, label: '执行计划', icon: Play },
    { id: 'tools' as const, label: '工具', icon: Wrench },
    { id: 'history' as const, label: '历史记录', icon: Clock },
    { id: 'config' as const, label: '配置', icon: Settings },
  ];

  return (
    <div className="w-full h-full bg-zinc-900 border-r border-zinc-800 flex flex-col">
      {/* Logo */}
      <div className="p-4 border-b border-zinc-800 overflow-hidden">
        <h1 className="text-xl font-bold text-white truncate">EAS 2.0</h1>
        <p className="text-xs text-zinc-400 mt-1 truncate">AI 任务编排系统</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = currentView === item.id;
          
          return (
            <button
              key={item.id}
              onClick={() => setCurrentView(item.id)}
              title={item.label}
              className={`
                w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium
                transition-colors mb-1 overflow-hidden
                ${isActive 
                  ? 'bg-blue-600 text-white' 
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800'
                }
              `}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              <span className="truncate">{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Panel toggles */}
      <div className="p-2 border-t border-zinc-800 space-y-1">
        <button
          onClick={toggleLogPanel}
          title="切换日志面板"
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors overflow-hidden ${
            !logPanelCollapsed ? 'text-green-400 bg-zinc-800' : 'text-zinc-500 hover:text-white hover:bg-zinc-800'
          }`}
        >
          <Terminal className="w-4 h-4 flex-shrink-0" />
          <span className="truncate">日志</span>
        </button>
        <button
          onClick={toggleAiPanel}
          title="切换 AI 助手"
          className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors overflow-hidden ${
            !aiPanelCollapsed ? 'text-blue-400 bg-zinc-800' : 'text-zinc-500 hover:text-white hover:bg-zinc-800'
          }`}
        >
          <Bot className="w-4 h-4 flex-shrink-0" />
          <span className="truncate">AI 助手</span>
        </button>
      </div>

      {/* Collapse toggle + Footer */}
      <div className="p-2 border-t border-zinc-800">
        <button
          onClick={toggleSidebar}
          title="收起/展开侧栏"
          className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-zinc-500 hover:text-white hover:bg-zinc-800 transition-colors overflow-hidden"
        >
          <PanelLeftClose className="w-4 h-4 flex-shrink-0" />
          <span className="truncate text-sm">收起</span>
        </button>
      </div>
    </div>
  );
}
