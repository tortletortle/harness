/**
 * 主应用组件
 */

import { useEffect } from 'react';
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

function App() {
  const { currentView, addLog, sidebarCollapsed, logPanelCollapsed, aiPanelCollapsed } = useAppStore();

  useEffect(() => {
    apiClient.connectLogs((log) => {
      addLog(log);
    });
    // plan_update 推送：只打事件计数，权威状态由 usePlanPolling 全量拉取
    apiClient.onPlanUpdate((ev) => {
      useAppStore.getState().notifyPlanEvent(ev.plan_id);
    });
    return () => {
      apiClient.disconnectLogs();
    };
  }, [addLog]);

  const renderView = () => {
    switch (currentView) {
      case 'input':
        return <InputView />;
      case 'design':
        return <DesignView />;
      case 'plan':
        return <PlanView />;
      case 'execution':
        return <ExecutionView />;
      case 'tools':
        return <ToolsView />;
      case 'history':
        return <HistoryView />;
      case 'audit':
        return <AuditView />;
      case 'config':
        return <ConfigView />;
      default:
        return <InputView />;
    }
  };

  return (
    <div className="h-screen flex flex-col bg-black text-white">
      <PanelGroup direction="vertical">
        {/* 上部区域: 侧边栏 + 内容 + AI面板 */}
        <Panel defaultSize={85} minSize={30}>
          <PanelGroup direction="horizontal">
            {/* 左侧边栏 */}
            {!sidebarCollapsed && (
              <>
                <Panel 
                  defaultSize={15} 
                  minSize={12}
                  maxSize={30}
                  collapsible={true}
                  collapsedSize={4}
                >
                  <Sidebar />
                </Panel>
                <PanelResizeHandle className="w-1 bg-zinc-800 hover:bg-blue-600 transition-colors" />
              </>
            )}

            {/* 主内容区 */}
            <Panel defaultSize={70} minSize={30}>
              <div className="h-full overflow-auto">
                {renderView()}
              </div>
            </Panel>

            {/* 右侧 AI 助手面板 */}
            {!aiPanelCollapsed && (
              <>
                <PanelResizeHandle className="w-1 bg-zinc-800 hover:bg-blue-600 transition-colors" />
                <Panel 
                  defaultSize={20}
                  minSize={15}
                  maxSize={40}
                  collapsible={true}
                  collapsedSize={4}
                >
                  <AiPanel />
                </Panel>
              </>
            )}
          </PanelGroup>
        </Panel>

        {/* 底部日志面板 */}
        {!logPanelCollapsed && (
          <>
            <PanelResizeHandle className="h-1 bg-zinc-800 hover:bg-blue-600 transition-colors" />
            <Panel 
              defaultSize={15}
              minSize={10}
              maxSize={50}
              collapsible={true}
              collapsedSize={3}
            >
              <LogPanel />
            </Panel>
          </>
        )}
      </PanelGroup>

      {/* 全局 Toast 通知（覆盖在面板之上） */}
      <ToastHub />
    </div>
  );
}

export default App;
