/**
 * Zustand 状态管理
 */

import { create } from 'zustand';
import type { Design, ExecutionPlan, Tool, LogMessage, Toast, ApprovalRequestEvent, ServiceConnectionState } from '../types';

const STORAGE_KEY = 'eas-ui-state';
const LOG_MAX = 500;

type PersistedUiState = {
  currentView?: AppState['currentView'];
  inputText?: string;
  currentPlan?: ExecutionPlan | null;
};

function readPersistedState(): PersistedUiState {
  if (typeof localStorage === 'undefined') return {};
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as PersistedUiState;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function persistUiState(state: Pick<AppState, 'currentView' | 'inputText' | 'currentPlan'>): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      currentView: state.currentView,
      inputText: state.inputText,
      currentPlan: state.currentPlan,
    }));
  } catch {
    // 本地 UI 状态持久化失败不应影响任务执行。
  }
}

const persisted = readPersistedState();

// Toast 自增 ID 计数器（模块级，保证唯一）
let toastIdCounter = 0;
// Toast 最多保留条数
const TOAST_MAX = 5;

interface AppState {
  // 当前视图
  currentView: 'input' | 'design' | 'plan' | 'execution' | 'tools' | 'history' | 'audit' | 'artifacts' | 'agents' | 'config';
  setCurrentView: (view: AppState['currentView']) => void;

  // 服务连接状态
  connectionState: ServiceConnectionState;
  setConnectionState: (state: ServiceConnectionState) => void;

  // plan_update 推送事件计数：usePlanPolling 订阅它以在事件到达时立即刷新。
  // 只带 planId 与自增序号——权威状态仍由轮询 hook 的全量拉取写入，
  // 避免本地合并造成的状态漂移。
  planEventTick: { planId: string | null; seq: number };
  notifyPlanEvent: (planId: string) => void;

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

  // P0-a 工具审批：当前待审批请求（同一时刻最多挂起一个——执行循环串行）
  pendingApproval: ApprovalRequestEvent | null;
  setPendingApproval: (approval: ApprovalRequestEvent | null) => void;

  // 配置
  configDirty: boolean;
  config: any;
  setConfig: (config: any) => void;
}

export const useAppStore = create<AppState>((set) => ({
  // 初始状态
  currentView: persisted.currentView ?? 'input',
  connectionState: 'connecting',
  planEventTick: { planId: null, seq: 0 },
  inputText: persisted.inputText ?? '',
  sidebarCollapsed: false,
  logPanelCollapsed: false,
  aiPanelCollapsed: true,
  designs: [],
  currentDesign: null,
  plans: [],
  currentPlan: persisted.currentPlan ?? null,
  tools: [],
  logs: [],
  toasts: [],
  isExecuting: false,
  pendingApproval: null,
  config: null,
  configDirty: false,

  // Actions
  setCurrentView: (view) => set((state) => {
    if (state.currentView === 'config' && view !== 'config' && state.configDirty &&
        !window.confirm('模型配置有未保存的修改，确定离开并放弃吗？')) return state;
    const next = { ...state, currentView: view };
    persistUiState(next);
    return { currentView: view };
  }),
  setConnectionState: (connectionState) => set({ connectionState }),

  notifyPlanEvent: (planId) => set((state) => ({
    planEventTick: { planId, seq: state.planEventTick.seq + 1 },
  })),

  setInputText: (text) => set((state) => {
    const next = { ...state, inputText: text };
    persistUiState(next);
    return { inputText: text };
  }),

  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  toggleLogPanel: () => set((state) => ({ logPanelCollapsed: !state.logPanelCollapsed })),
  toggleAiPanel: () => set((state) => ({ aiPanelCollapsed: !state.aiPanelCollapsed })),

  setDesigns: (designs) => set({ designs }),
  setCurrentDesign: (design) => set({ currentDesign: design }),

  setPlans: (plans) => set({ plans }),
  setCurrentPlan: (plan) => set((state) => {
    const next = { ...state, currentPlan: plan };
    persistUiState(next);
    return { currentPlan: plan };
  }),

  setTools: (tools) => set({ tools }),

  addLog: (log) => set((state) => {
    const incoming = { ...log, id: log.id ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`, timestamp: log.timestamp ?? new Date().toISOString() };
    const logs = [...state.logs, incoming].slice(-LOG_MAX);

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

  setPendingApproval: (approval) => set({ pendingApproval: approval }),

  setConfig: (config) => set({ config }),
}));

// 调试用：浏览器控制台/自动化可通过 window.__useAppStore 读取实时状态
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__useAppStore = useAppStore;
}
