/**
 * Zustand 状态管理
 */

import { create } from 'zustand';
import type { Design, ExecutionPlan, Tool, LogMessage, Toast } from '../types';

// Toast 自增 ID 计数器（模块级，保证唯一）
let toastIdCounter = 0;
// Toast 最多保留条数
const TOAST_MAX = 5;

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

  // Toast 通知（error/warning 日志自动浮出）
  toasts: Toast[];
  dismissToast: (id: number) => void;

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
  toasts: [],
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

  addLog: (log) => set((state) => {
    const logs = [...state.logs, { ...log, timestamp: new Date().toISOString() }];

    // error/warning 日志同时浮出 Toast
    let toasts = state.toasts;
    if (log.level === 'error' || log.level === 'warning') {
      const newest = toasts[toasts.length - 1];
      if (newest && newest.message === log.message) {
        // 短时间重复的相同消息：刷新最新一条，避免连发刷屏
        const timestamp = new Date().toISOString();
        toasts = toasts.map((t, i) => (i === toasts.length - 1 ? { ...t, timestamp } : t));
      } else {
        toasts = [
          ...toasts,
          { id: ++toastIdCounter, level: log.level, message: log.message, timestamp: new Date().toISOString() },
        ].slice(-TOAST_MAX); // 只保留最近 5 条
      }
    }

    return { logs, toasts };
  }),
  clearLogs: () => set({ logs: [] }),

  dismissToast: (id) => set((state) => ({
    toasts: state.toasts.filter((t) => t.id !== id),
  })),

  setIsExecuting: (executing) => set({ isExecuting: executing }),

  setConfig: (config) => set({ config }),
}));

// 调试用：浏览器控制台/自动化可通过 window.__useAppStore 读取实时状态
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__useAppStore = useAppStore;
}
