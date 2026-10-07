import {
  Activity, Bot, Boxes, ChevronRight, ClipboardList, Command, FileText, Gauge,
  History, LayoutDashboard, PanelLeftClose, PanelLeftOpen, PenSquare, Plus,
  ScrollText, Settings, Terminal, Wrench,
} from 'lucide-react';
import { useMemo } from 'react';
import { useAppStore } from '../stores/appStore';

const workflow = [
  { id: 'input' as const, label: '新建任务', icon: PenSquare },
  { id: 'design' as const, label: '设计书', icon: FileText },
  { id: 'plan' as const, label: '执行计划', icon: LayoutDashboard },
  { id: 'execution' as const, label: '实时执行', icon: Activity },
  { id: 'artifacts' as const, label: '产物库', icon: Boxes },
  { id: 'agents' as const, label: 'Agent 记录', icon: Bot },
];
const resources = [
  { id: 'tools' as const, label: '工具库', icon: Wrench },
  { id: 'history' as const, label: '历史记录', icon: History },
  { id: 'audit' as const, label: '审计日志', icon: ScrollText },
  { id: 'system' as const, label: '系统概览', icon: Gauge },
  { id: 'config' as const, label: '系统设置', icon: Settings },
];

type NavItem = (typeof workflow)[number] | (typeof resources)[number];

export function Sidebar({ forceCollapsed = false }: { forceCollapsed?: boolean }) {
  const {
    currentView, setCurrentView, sidebarCollapsed, toggleSidebar,
    toggleLogPanel, logPanelCollapsed, toggleAiPanel, aiPanelCollapsed,
    currentPlan, isExecuting, connectionState,
  } = useAppStore();
  const collapsed = sidebarCollapsed || forceCollapsed;
  const newTaskShortcut = useMemo(
    () => (typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ N' : 'Ctrl N'),
    [],
  );
  const steps = currentPlan?.segments.flatMap((segment) => segment.steps) ?? [];
  const completed = steps.filter((step) => step.status === 'Success' || step.status === 'Skipped').length;
  const progress = steps.length ? Math.round((completed / steps.length) * 100) : 0;

  const renderItem = (item: NavItem) => {
    const Icon = item.icon;
    const active = currentView === item.id;
    return <button
      key={item.id}
      type="button"
      onClick={() => setCurrentView(item.id)}
      title={collapsed ? item.label : undefined}
      aria-current={active ? 'page' : undefined}
      aria-label={item.id === 'config' ? '设置' : undefined}
      className={`helm-nav-item ${active ? 'is-active' : ''}`}
    >
      <Icon size={17} strokeWidth={1.75} aria-hidden="true" />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {!collapsed && active && <span className="helm-nav-current" aria-hidden="true" />}
    </button>;
  };

  return <aside className={`helm-sidebar ${collapsed ? 'is-collapsed' : ''}`}>
    <div className="helm-sidebar-brand">
      <div className="helm-brand-mark"><Command size={19} strokeWidth={1.8} /></div>
      {!collapsed && <div className="min-w-0"><strong>星舵 Helm</strong><small>EAS 2.0 · 本地工作区</small></div>}
    </div>

    <div className="helm-sidebar-scroll">
      <button type="button" className="helm-new-task" onClick={() => setCurrentView('input')} title="创建新任务">
        <Plus size={17} /><span className="helm-sidebar-label">创建新任务</span><kbd className="helm-sidebar-label">{newTaskShortcut}</kbd>
      </button>
      <div className="helm-nav-section">
        {!collapsed && <p className="helm-nav-heading">工作流</p>}
        <nav aria-label="工作流">{workflow.map(renderItem)}</nav>
      </div>
      <div className="helm-nav-section">
        {!collapsed && <p className="helm-nav-heading">资源与治理</p>}
        <nav aria-label="资源与治理">{resources.map(renderItem)}</nav>
      </div>
    </div>

    <div className="helm-sidebar-footer">
      {!collapsed && currentPlan && <button type="button" className="helm-current-task" onClick={() => setCurrentView('execution')}>
        <span className="helm-current-task-label">当前任务 <ChevronRight size={13} /></span>
        <strong><span className={`helm-status-dot ${isExecuting ? 'is-live' : ''}`} />计划 {currentPlan.plan_id.slice(0, 8)}</strong>
        <span className="helm-progress-track"><span style={{ width: `${progress}%` }} /></span>
        <small>{completed} / {steps.length} 步骤 <span>{isExecuting ? '运行中' : currentPlan.status ?? '待执行'}</span></small>
      </button>}
      <div className="helm-sidebar-controls">
        <button type="button" className="helm-footer-button" onClick={toggleLogPanel} title="切换活动流" aria-label="活动流"><Terminal size={16} /><span className="helm-sidebar-label">活动流</span>{!collapsed && !logPanelCollapsed && <span className="helm-footer-on" />}</button>
        <button type="button" className="helm-footer-button" onClick={toggleAiPanel} title="切换 AI Copilot" aria-label="AI Copilot"><ClipboardList size={16} /><span className="helm-sidebar-label">AI Copilot</span>{!collapsed && !aiPanelCollapsed && <span className="helm-footer-on" />}</button>
        <button type="button" className="helm-footer-button" onClick={toggleSidebar} title={collapsed ? '展开侧栏' : '收起侧栏'} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}<span className="helm-sidebar-label">收起侧栏</span></button>
      </div>
      <div className="helm-workspace" aria-label="本地工作区状态"><span className="helm-workspace-mark">⌘</span>{!collapsed && <div><strong>本地工作区</strong><small><i className={connectionState === 'online' ? '' : 'is-offline'} />FastAPI · {connectionState === 'online' ? '已连接' : '未连接'}</small></div>}</div>
    </div>
  </aside>;
}
