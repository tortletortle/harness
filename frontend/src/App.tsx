/**
 * Application shell for the EAS control room.
 */

import { useEffect, useState } from 'react';
import { Activity, Circle, ChevronDown, Moon, PanelRight, Sun, Wifi } from 'lucide-react';
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels';
import { apiClient } from './api/client';
import { useAppStore } from './stores/appStore';
import { Sidebar } from './components/Sidebar';
import { LogPanel } from './components/LogPanel';
import { AiPanel } from './components/AiPanel';
import { InputView } from './components/InputView';
import { DesignView } from './components/DesignView';
import { PlanView } from './components/PlanView';
import { ExecutionView } from './components/ExecutionView';
import { ToolsView } from './components/ToolsView';
import { HistoryView } from './components/HistoryView';
import { AuditView } from './components/AuditView';
import { ConfigView } from './components/ConfigView';
import { ToastHub } from './components/ToastHub';
import { ApprovalBanner } from './components/ApprovalBanner';

let lastPlanEventVersion = 0;

const viewMeta = {
  input: { label: '新建任务', hint: '把目标交给 EAS，先从清晰的需求开始' },
  design: { label: '设计书审阅', hint: '检查架构、工具和风险，再批准执行' },
  plan: { label: '执行计划', hint: '管理步骤、验收和恢复点' },
  execution: { label: '实时执行', hint: '观察当前任务的运行状态' },
  tools: { label: '工具库', hint: '查看可用能力和执行边界' },
  history: { label: '历史记录', hint: '浏览过往设计书和执行计划' },
  audit: { label: '审计日志', hint: '追踪工具调用和安全决定' },
  config: { label: '系统设置', hint: '管理服务地址、密钥和模型分配' },
} as const;

function App() {
  const { currentView, addLog, sidebarCollapsed, logPanelCollapsed, aiPanelCollapsed, currentPlan, isExecuting } = useAppStore();
  const [theme, setTheme] = useState<'dark' | 'light'>(() => {
    return (localStorage.getItem('eas-theme') as 'dark' | 'light' | null) || 'dark';
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('eas-theme', theme);
  }, [theme]);

  useEffect(() => {
    apiClient.connectLogs((log) => addLog(log));
    apiClient.onPlanUpdate((ev) => {
      if (typeof ev.version === 'number') {
        if (ev.version <= lastPlanEventVersion) return;
        lastPlanEventVersion = ev.version;
      }
      useAppStore.getState().notifyPlanEvent(ev.plan_id);
    });
    apiClient.onApprovalRequest((ev) => useAppStore.getState().setPendingApproval(ev));
    apiClient.onApprovalResolved((ev) => {
      const current = useAppStore.getState().pendingApproval;
      if (current && current.approval_id === ev.approval_id) useAppStore.getState().setPendingApproval(null);
    });
    return () => apiClient.disconnectLogs();
  }, [addLog]);

  const renderView = () => {
    switch (currentView) {
      case 'input': return <InputView />;
      case 'design': return <DesignView />;
      case 'plan': return <PlanView />;
      case 'execution': return <ExecutionView />;
      case 'tools': return <ToolsView />;
      case 'history': return <HistoryView />;
      case 'audit': return <AuditView />;
      case 'config': return <ConfigView />;
      default: return <InputView />;
    }
  };

  const meta = viewMeta[currentView];

  return (
    <div className="surface-grid flex h-screen min-h-0 flex-col text-white">
      <ApprovalBanner />
      <PanelGroup direction="vertical" className="min-h-0 flex-1">
        <Panel defaultSize={85} minSize={35} className="min-h-0">
          <PanelGroup direction="horizontal" className="min-h-0">
            <Panel defaultSize={sidebarCollapsed ? 5 : 16} minSize={5} maxSize={28} className="min-w-0">
              <Sidebar />
            </Panel>
            <PanelResizeHandle className="w-px bg-white/[0.06] transition-colors hover:bg-violet-400" />

            <Panel defaultSize={aiPanelCollapsed ? 84 : 64} minSize={35} className="min-w-0">
              <div className="flex h-full min-h-0 flex-col">
                <header className="flex h-[72px] shrink-0 items-center justify-between border-b border-white/[0.08] bg-[#0d0f13]/80 px-6 backdrop-blur-xl">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-[11px] text-zinc-600"><span>工作区</span><ChevronDown className="h-3 w-3" /><span className="text-zinc-400">EAS Control Room</span></div>
                    <div className="mt-1 flex items-center gap-3"><h1 className="text-[17px] font-semibold tracking-tight text-white">{meta.label}</h1><span className="hidden text-xs text-zinc-600 sm:inline">{meta.hint}</span></div>
                  </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {currentPlan && <div className="hidden items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 md:flex"><Activity className={`h-3.5 w-3.5 ${isExecuting ? 'text-violet-300' : 'text-zinc-600'}`} /><span className="font-mono text-[10px] text-zinc-500">{currentPlan.plan_id.slice(0, 8)}</span></div>}
                      <div className="flex items-center gap-1.5 text-[11px] text-zinc-500"><Wifi className="h-3.5 w-3.5 text-emerald-300" /><span className="hidden sm:inline">服务在线</span><Circle className="h-1.5 w-1.5 fill-emerald-400 text-emerald-400" /></div>
                      <button
                        type="button"
                        onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
                        title={theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
                        aria-label={theme === 'dark' ? '切换到亮色主题' : '切换到暗色主题'}
                        className="rounded-lg border border-white/[0.08] bg-white/[0.03] p-2 text-zinc-500 transition-colors hover:bg-white/[0.08] hover:text-white"
                      >
                        {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
                      </button>
                      {!aiPanelCollapsed && <PanelRight className="h-4 w-4 text-violet-300" />}
                    </div>
                </header>
                <main className="min-h-0 flex-1 overflow-auto">{renderView()}</main>
              </div>
            </Panel>

            {!aiPanelCollapsed && <><PanelResizeHandle className="w-px bg-white/[0.06] transition-colors hover:bg-violet-400" /><Panel defaultSize={20} minSize={16} maxSize={36} className="min-w-0"><AiPanel /></Panel></>}
          </PanelGroup>
        </Panel>

        {!logPanelCollapsed && <><PanelResizeHandle className="h-px bg-white/[0.06] transition-colors hover:bg-emerald-400" /><Panel defaultSize={15} minSize={10} maxSize={40} className="min-h-0"><LogPanel /></Panel></>}
      </PanelGroup>
      <ToastHub />
    </div>
  );
}

export default App;
