/**
 * Workspace navigation for 星舵 Helm.
 */

import {
  Activity, Bot, Boxes, ChevronRight, ClipboardList, HelpCircle, FileText, History,
  LayoutDashboard, PanelLeftClose, PanelLeftOpen, PenSquare, ScrollText,
  Settings, Terminal, Wrench, Gauge,
} from 'lucide-react';
import { useAppStore } from '../stores/appStore';

const primaryItems = [
  { id: 'input' as const, label: '新建任务', icon: PenSquare },
  { id: 'design' as const, label: '设计书', icon: FileText },
  { id: 'plan' as const, label: '执行计划', icon: LayoutDashboard },
  { id: 'execution' as const, label: '实时执行', icon: Activity },
];
const libraryItems = [
  { id: 'artifacts' as const, label: '产物库', icon: Boxes },
  { id: 'agents' as const, label: 'Agent 记录', icon: ClipboardList },
  { id: 'tools' as const, label: '工具库', icon: Wrench },
  { id: 'history' as const, label: '历史记录', icon: History },
  { id: 'audit' as const, label: '审计日志', icon: ScrollText },
  { id: 'system' as const, label: '系统概览', icon: Gauge },
];

export function Sidebar() {
  const { currentView, setCurrentView, sidebarCollapsed, toggleSidebar, toggleLogPanel, logPanelCollapsed, toggleAiPanel, aiPanelCollapsed, currentPlan, isExecuting } = useAppStore();
  const renderItem = (item: typeof primaryItems[number] | typeof libraryItems[number]) => {
    const Icon = item.icon;
    const active = currentView === item.id;
    return <button key={item.id} onClick={() => setCurrentView(item.id)} title={sidebarCollapsed ? item.label : undefined} aria-current={active ? 'page' : undefined} className={`group flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left text-sm transition-all ${active ? 'bg-violet-500/15 text-white shadow-[inset_2px_0_0_#8b7cff]' : 'text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-100'}`}>
      <Icon className={`h-[17px] w-[17px] shrink-0 ${active ? 'text-violet-300' : 'text-zinc-500 group-hover:text-zinc-300'}`} />
      {!sidebarCollapsed && <span className="truncate">{item.label}</span>}
      {!sidebarCollapsed && active && <ChevronRight className="ml-auto h-3.5 w-3.5 text-violet-300" />}
    </button>;
  };
  return <aside className={`flex h-full min-h-0 flex-col border-r border-white/[0.08] bg-[#0d0f13]/95 ${sidebarCollapsed ? 'w-[68px]' : 'w-full'}`}>
    <div className={`flex h-[72px] shrink-0 items-center border-b border-white/[0.08] ${sidebarCollapsed ? 'justify-center px-2' : 'gap-3 px-4'}`}>
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-400 to-indigo-600 text-sm font-bold text-white shadow-lg shadow-violet-900/30">舵</div>
      {!sidebarCollapsed && <div className="min-w-0"><div className="truncate text-[13px] font-semibold tracking-wide text-white">星舵 Helm</div><div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-500"><span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />本地工作区</div></div>}
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto px-2 py-4">
      {!sidebarCollapsed && <div className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">工作流</div>}
      <nav className="space-y-1">{primaryItems.map(renderItem)}</nav>
      {!sidebarCollapsed && <div className="mb-2 mt-7 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">资源与治理</div>}
      <nav className="space-y-1">{libraryItems.map(renderItem)}</nav>
      {!sidebarCollapsed && currentPlan && <div className="mt-7 rounded-xl border border-white/[0.07] bg-white/[0.025] p-3"><div className="mb-2 flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600">当前任务{isExecuting && <span className="animate-pulse-soft text-violet-300">运行中</span>}</div><div className="truncate font-mono text-[11px] text-zinc-300">{currentPlan.plan_id}</div></div>}
    </div>
    <div className="shrink-0 space-y-1 border-t border-white/[0.08] p-2">
      <button onClick={toggleLogPanel} title="切换活动流" className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${!logPanelCollapsed ? 'bg-emerald-400/10 text-emerald-300' : 'text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200'}`}><Terminal className="h-[17px] w-[17px] shrink-0" />{!sidebarCollapsed && <span>活动流</span>}</button>
      <button onClick={toggleAiPanel} title="切换 AI Copilot" className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${!aiPanelCollapsed ? 'bg-violet-400/10 text-violet-300' : 'text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200'}`}><Bot className="h-[17px] w-[17px] shrink-0" />{!sidebarCollapsed && <span>AI Copilot</span>}</button>
      {!sidebarCollapsed && <button onClick={() => setCurrentView('config')} className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${currentView === 'config' ? 'bg-white/[0.06] text-zinc-100' : 'text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200'}`}><Settings className="h-[17px] w-[17px]" /><span>设置</span></button>}
      <button onClick={toggleSidebar} title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'} className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-zinc-600 transition-colors hover:bg-white/[0.05] hover:text-zinc-300 ${sidebarCollapsed ? 'justify-center' : ''}`}>{sidebarCollapsed ? <PanelLeftOpen className="h-[17px] w-[17px]" /> : <PanelLeftClose className="h-[17px] w-[17px]" />}{!sidebarCollapsed && <span>收起侧栏</span>}</button>
      {!sidebarCollapsed && <div className="flex items-center gap-2 px-3 pt-2 text-[10px] text-zinc-700"><HelpCircle className="h-3 w-3" />EAS 2.0 · 本地运行</div>}
    </div>
  </aside>;
}
