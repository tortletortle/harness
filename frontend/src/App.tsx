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
import { ToolsView } from './components/ToolsView';
import { HistoryView } from './components/HistoryView';
import { ConfigView } from './components/ConfigView';

function App() {
  const { currentView, addLog, sidebarCollapsed, logPanelCollapsed, aiPanelCollapsed } = useAppStore();

  useEffect(() => {
    apiClient.connectLogs((log) => {
      addLog(log);
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
      case 'tools':
        return <ToolsView />;
      case 'history':
        return <HistoryView />;
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
    </div>
  );
}

export default App;
