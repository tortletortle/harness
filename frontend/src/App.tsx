/**
 * Application shell for 星舵 Helm.
 */

import { useCallback, useEffect, useState } from 'react';
import { Activity, Circle, Moon, PanelRight, Sun, Wifi, WifiOff, Loader2 } from 'lucide-react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { apiClient } from './api/client';
import { useAppStore } from './stores/appStore';
import type { ServiceConnectionState } from './types';
import { Sidebar } from './components/Sidebar';
import { LogPanel } from './components/LogPanel';
import { AiPanel } from './components/AiPanel';
import { InputView } from './components/InputView';
import { DesignView } from './components/DesignView';
import { PlanView } from './components/PlanView';
import { ExecutionView } from './components/ExecutionView';
import { ArtifactsView } from './components/ArtifactsView';
import { AgentSessionsView } from './components/AgentSessionsView';
import { ToolsView } from './components/ToolsView';
import { HistoryView } from './components/HistoryView';
import { AuditView } from './components/AuditView';
import { ConfigView } from './components/ConfigView';
import { SystemView } from './components/SystemView';
import { ToastHub } from './components/ToastHub';
import { ApprovalBanner } from './components/ApprovalBanner';
import { SplashScreen, shouldShowSplash } from './components/SplashScreen';
import { PipelineOverview } from './components/PipelineOverview';

let lastPlanEventVersionByPlan = new Map<string, number>();

const connectionMeta: Record<ServiceConnectionState, { label: string; className: string }> = {
  connecting: { label: '连接中', className: 'text-amber-300' },
  online: { label: '服务在线', className: 'text-emerald-300' },
  reconnecting: { label: '正在重连', className: 'text-amber-300' },
  offline: { label: '已离线', className: 'text-zinc-500' },
  auth_error: { label: '认证失败', className: 'text-red-300' },
};

const viewMeta = {
  input: { label: '新建任务', hint: '把目标交给星舵，先从清晰的需求开始' },
  design: { label: '设计书审阅', hint: '检查架构、工具和风险，再批准执行' },
  plan: { label: '执行计划', hint: '管理步骤、依赖、验收和恢复点' },
  execution: { label: '实时执行', hint: '观察当前任务的运行状态' },
  artifacts: { label: '产物库', hint: '浏览步骤与 Agent 产生的文件和交付报告' },
  agents: { label: 'Agent 记录', hint: '查看 Agent 会话与逐轮模型调用' },
  tools: { label: '工具库', hint: '查看可用能力和执行边界' },
  history: { label: '历史记录', hint: '浏览过往设计书和执行计划' },
  audit: { label: '审计日志', hint: '追踪工具调用和安全决定' },
  config: { label: '系统设置', hint: '管理服务地址、密钥和模型分配' },
  system: { label: '系统概览', hint: '模型用量、技能与能力、密钥和通知通道' },
} as const;

function App() {
  const { currentView, addLog, sidebarCollapsed, logPanelCollapsed, aiPanelCollapsed, currentPlan, isExecuting, connectionState, setConnectionState } = useAppStore();
  const [showSplash, setShowSplash] = useState(shouldShowSplash);
  const [compact, setCompact] = useState(() => typeof window !== 'undefined' && window.matchMedia?.('(max-width: 760px)').matches || false);
  const hideSplash = useCallback(() => setShowSplash(false), []);
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('eas-theme') as 'dark' | 'light' | null) || 'dark';
  });

  useEffect(() => {
    if (!window.matchMedia) return;
    const query = window.matchMedia('(max-width: 760px)');
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (compact && !useAppStore.getState().aiPanelCollapsed) useAppStore.getState().toggleAiPanel();
  }, [compact]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('eas-theme', theme);
  }, [theme]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'n' || event.altKey) return;
      event.preventDefault();
      useAppStore.getState().setCurrentView('input');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    apiClient.onConnectionState(setConnectionState);
    apiClient.connectLogs((log) => addLog(log));
    apiClient.onPlanUpdate((ev) => {
      if (typeof ev.version === 'number') {
        const previous = lastPlanEventVersionByPlan.get(ev.plan_id) ?? 0;
        if (ev.version <= previous) return;
        lastPlanEventVersionByPlan.set(ev.plan_id, ev.version);
      }
      useAppStore.getState().notifyPlanEvent(ev.plan_id);
    });
    apiClient.onApprovalRequest((ev) => useAppStore.getState().setPendingApproval(ev));
    apiClient.onApprovalResolved((ev) => {
      const current = useAppStore.getState().pendingApproval;
      if (current && current.approval_id === ev.approval_id) useAppStore.getState().setPendingApproval(null);
    });
    void apiClient.getServiceStatus()
      .then(() => setConnectionState('online'))
      .catch(() => setConnectionState('offline'));
    return () => apiClient.disconnectLogs();
  }, [addLog, setConnectionState]);

  const renderView = () => {
    switch (currentView) {
      case 'input': return <InputView />;
      case 'design': return <DesignView />;
      case 'plan': return <PlanView />;
      case 'execution': return <ExecutionView />;
      case 'artifacts': return <ArtifactsView />;
      case 'agents': return <AgentSessionsView />;
      case 'tools': return <ToolsView />;
      case 'history': return <HistoryView />;
      case 'audit': return <AuditView />;
      case 'config': return <ConfigView />;
      case 'system': return <SystemView />;
      default: return <InputView />;
    }
  };

  const meta = viewMeta[currentView];
  const compactSidebarSize = compact ? Math.min(16, Math.max(8, 60 / window.innerWidth * 100)) : 6;

  return (
    <div className="helm-app surface-grid flex h-screen min-h-0 flex-col text-white">
      <ApprovalBanner />
      <PanelGroup key={`${sidebarCollapsed}-${aiPanelCollapsed}-${compact}`} direction="horizontal" className="min-h-0 flex-1">
        <Panel defaultSize={compact ? compactSidebarSize : sidebarCollapsed ? 6 : 17} minSize={compact ? compactSidebarSize : sidebarCollapsed ? 5 : 12} maxSize={27} className="min-w-0">
          <Sidebar forceCollapsed={compact} />
        </Panel>
        <PanelResizeHandle className="helm-resize-handle is-vertical" />

        <Panel defaultSize={compact ? 100 - compactSidebarSize : aiPanelCollapsed ? (sidebarCollapsed ? 94 : 83) : 63} minSize={35} className="min-w-0">
          <PanelGroup direction="vertical" className="min-h-0">
            <Panel defaultSize={logPanelCollapsed ? 100 : 82} minSize={35} className="min-h-0">
              <div className="helm-main flex h-full min-h-0 flex-col">
                <header className="helm-header">
                  <div className="min-w-0">
                    <div className="helm-breadcrumb"><span>工作区</span><span>/</span><span>星舵 Helm</span><span>/</span><span>{meta.label}</span></div>
                    <div className="helm-header-title"><h1>{meta.label}</h1>{currentPlan && <span className="helm-plan-id">PLAN · {currentPlan.plan_id.slice(0, 8)}</span>}</div>
                  </div>
                  <div className="helm-header-actions">
                    <div className={`helm-connection ${connectionState === 'online' ? 'is-online' : ''}`} title={connectionMeta[connectionState].label}>
                      {connectionState === 'connecting' || connectionState === 'reconnecting' ? <Loader2 size={13} className="animate-spin" /> : connectionState === 'online' ? <Wifi size={13} /> : <WifiOff size={13} />}
                      <span className="hidden sm:inline">{connectionMeta[connectionState].label}</span>
                      <Circle size={5} fill="currentColor" />
                    </div>
                    {currentPlan && <span className={`helm-run-status ${isExecuting ? 'is-live' : ''}`}><Activity size={14} />{isExecuting ? '执行中' : '已就绪'}</span>}
                    <button type="button" className="helm-icon-button" onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')} title={theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'} aria-label={theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}>{theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}</button>
                    {(aiPanelCollapsed || compact) && <button type="button" className="helm-icon-button" onClick={() => useAppStore.getState().toggleAiPanel()} title="打开 AI Copilot" aria-label="打开 AI Copilot"><PanelRight size={17} /></button>}
                    <div className="helm-avatar" title="本地工作区">我</div>
                  </div>
                </header>
                <PipelineOverview />
                <main className="helm-content min-h-0 flex-1 overflow-auto">{renderView()}</main>
              </div>
            </Panel>
            {!logPanelCollapsed && <><PanelResizeHandle className="helm-resize-handle is-horizontal" /><Panel defaultSize={18} minSize={10} maxSize={40} className="min-h-0"><LogPanel /></Panel></>}
          </PanelGroup>
        </Panel>

        {!aiPanelCollapsed && !compact && <><PanelResizeHandle className="helm-resize-handle is-vertical" /><Panel defaultSize={20} minSize={16} maxSize={36} className="min-w-0"><AiPanel /></Panel></>}
      </PanelGroup>
      {compact && !aiPanelCollapsed && <>
        <button type="button" className="helm-mobile-overlay" aria-label="关闭 AI 副驾" onClick={() => useAppStore.getState().toggleAiPanel()} />
        <div className="helm-mobile-ai"><AiPanel /></div>
      </>}
      <ToastHub />
      {showSplash && <SplashScreen onDone={hideSplash} />}
    </div>
  );
}

export default App;
