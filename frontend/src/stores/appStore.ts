/**
 * Zustand 状态管理
 */

import { create } from 'zustand';
import type { Design, ExecutionPlan, Tool, LogMessage } from '../types';

interface AppState {
  // 当前视图
  currentView: 'input' | 'design' | 'plan' | 'execution' | 'tools' | 'history' | 'config';
  setCurrentView: (view: AppState['currentView']) => void;

  // 输入文本（跨 tab 持久化）
  inputText: string;
  setInputText: (text: string) => void;

  // 面板折叠状态
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  logPanelCollapsed: boolean;
  toggleLogPanel: () => void;
  aiPanelCollapsed: boolean;
  toggleAiPanel: () => void;

  // 设计书相关
  designs: Design[];
  currentDesign: Design | null;
  setDesigns: (designs: Design[]) => void;
  setCurrentDesign: (design: Design | null) => void;

  // 执行计划相关
  plans: ExecutionPlan[];
  currentPlan: ExecutionPlan | null;
  setPlans: (plans: ExecutionPlan[]) => void;
  setCurrentPlan: (plan: ExecutionPlan | null) => void;

  // 工具相关
  tools: Tool[];
  setTools: (tools: Tool[]) => void;

  // 日志
  logs: LogMessage[];
  addLog: (log: LogMessage) => void;
  clearLogs: () => void;

  // 执行状态
  isExecuting: boolean;
  setIsExecuting: (executing: boolean) => void;

  // 配置
  config: any;
  setConfig: (config: any) => void;
}

export const useAppStore = create<AppState>((set) => ({
  // 初始状态
  currentView: 'input',
  inputText: '',
  sidebarCollapsed: false,
  logPanelCollapsed: false,
  aiPanelCollapsed: true,
  designs: [],
  currentDesign: null,
  plans: [],
  currentPlan: null,
  tools: [],
  logs: [],
  isExecuting: false,
  config: null,

  // Actions
  setCurrentView: (view) => set({ currentView: view }),

  setInputText: (text) => set({ inputText: text }),

  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  toggleLogPanel: () => set((state) => ({ logPanelCollapsed: !state.logPanelCollapsed })),
  toggleAiPanel: () => set((state) => ({ aiPanelCollapsed: !state.aiPanelCollapsed })),

  setDesigns: (designs) => set({ designs }),
  setCurrentDesign: (design) => set({ currentDesign: design }),

  setPlans: (plans) => set({ plans }),
  setCurrentPlan: (plan) => set({ currentPlan: plan }),

  setTools: (tools) => set({ tools }),

  addLog: (log) => set((state) => ({
    logs: [...state.logs, { ...log, timestamp: new Date().toISOString() }]
  })),
  clearLogs: () => set({ logs: [] }),

  setIsExecuting: (executing) => set({ isExecuting: executing }),

  setConfig: (config) => set({ config }),
}));

// 调试用：浏览器控制台/自动化可通过 window.__useAppStore 读取实时状态
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__useAppStore = useAppStore;
}
