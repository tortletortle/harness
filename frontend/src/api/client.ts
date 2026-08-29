/**
 * API 客户端
 * 与后端 FastAPI 服务通信
 */

import type { Design, ExecutionPlan, Tool, LogMessage, PlanUpdateEvent, AuditLogEntry } from '../types';

const API_BASE = import.meta.env.VITE_API_BASE || '';
const API_TOKEN = import.meta.env.VITE_API_TOKEN || '';

/**
 * 包装 fetch，为所有请求注入 API token。
 *
 * 后端所有 /api/* 端点都要求 Bearer token。token 从 VITE_API_TOKEN 读取，
 * 值由后端首次启动时生成并打印（同时写入 config/api_token）。
 */
async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  if (API_TOKEN) {
    headers.set('Authorization', `Bearer ${API_TOKEN}`);
  }

  const response = await fetch(url, { ...init, headers });

  if (response.status === 401) {
    throw new Error(
      'API 认证失败：请在 frontend/.env.local 中设置 VITE_API_TOKEN，' +
      '值见后端启动日志或 config/api_token'
    );
  }

  return response;
}

/**
 * 从非 2xx 响应中提取错误信息并抛出。
 *
 * FastAPI 的 HTTPException 返回 {"detail": "..."}（如 409/404），
 * 业务错误返回 {"error": "..."}。两者都兼容，都没有时使用 fallback。
 */
async function throwResponseError(response: Response, fallback: string): Promise<never> {
  let message = fallback;
  try {
    const data = await response.json();
    message = data.detail || data.error || fallback;
  } catch {
    // 响应体不是合法 JSON，保留 fallback 文案
  }
  throw new Error(message);
}

export class ApiClient {
  private ws: WebSocket | null = null;
  private logCallbacks: ((log: LogMessage) => void)[] = [];
  private planUpdateCallbacks: ((ev: PlanUpdateEvent) => void)[] = [];
  // 区分"程序性关闭"（替换连接/主动断开）与"意外断开"：
  // 前者绝不能触发自动重连，否则重连本身会再次关闭连接，形成每 5 秒的
  // 无限重连循环（StrictMode 双挂载加剧此问题，见 E2E 观察到的连接风暴）。
  private intentionalClose = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  /**
   * 生成设计书
   */
  async createDesign(userRequest: string): Promise<Design> {
    const response = await authFetch(`${API_BASE}/api/design`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_request: userRequest }),
    });

    if (!response.ok) {
      await throwResponseError(response, '生成设计书失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '生成设计书失败');
    }

    return data.design;
  }

  /**
   * 获取设计书列表
   */
  async getDesigns(limit = 10): Promise<Design[]> {
    const response = await authFetch(`${API_BASE}/api/designs?limit=${limit}`);
    if (!response.ok) {
      await throwResponseError(response, '获取设计书列表失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取设计书列表失败');
    }

    return data.designs;
  }

  /**
   * 获取单个设计书
   */
  async getDesign(designId: string): Promise<Design> {
    const response = await authFetch(`${API_BASE}/api/design/${designId}`);
    if (!response.ok) {
      await throwResponseError(response, '获取设计书失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取设计书失败');
    }

    return data.design;
  }

  /**
   * 审批设计书
   */
  async approveDesign(
    designId: string,
    approved: boolean,
    feedback?: string
  ): Promise<{ plan?: ExecutionPlan; revised?: boolean; design?: Design; rejected?: boolean; message?: string }> {
    const response = await authFetch(`${API_BASE}/api/design/${designId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ design_id: designId, approved, feedback }),
    });

    if (!response.ok) {
      await throwResponseError(response, '审批失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '审批失败');
    }

    return data;
  }

  /**
   * 获取执行计划列表
   */
  async getPlans(limit = 10): Promise<ExecutionPlan[]> {
    const response = await authFetch(`${API_BASE}/api/plans?limit=${limit}`);
    if (!response.ok) {
      await throwResponseError(response, '获取计划列表失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取计划列表失败');
    }

    return data.plans;
  }

  /**
   * 获取单个执行计划
   */
  async getPlan(planId: string): Promise<ExecutionPlan> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}`);
    if (!response.ok) {
      await throwResponseError(response, '获取计划失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取计划失败');
    }

    return data.plan;
  }

  /**
   * 执行计划
   */
  async executePlan(planId: string): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}/execute`, {
      method: 'POST',
    });

    if (!response.ok) {
      await throwResponseError(response, '执行失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '执行失败');
    }
  }

  /**
   * 暂停计划执行
   */
  async pausePlan(planId: string): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}/pause`, {
      method: 'POST',
    });

    if (!response.ok) {
      await throwResponseError(response, '暂停计划失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '暂停计划失败');
    }
  }

  /**
   * 恢复计划执行
   */
  async resumePlan(planId: string): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}/resume`, {
      method: 'POST',
    });

    if (!response.ok) {
      await throwResponseError(response, '恢复计划失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '恢复计划失败');
    }
  }

  /**
   * 跳过指定步骤
   */
  async skipStep(planId: string, stepId: string): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}/skip-step/${stepId}`, {
      method: 'POST',
    });

    if (!response.ok) {
      await throwResponseError(response, '跳过步骤失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '跳过步骤失败');
    }
  }

  /**
   * 回滚到指定步骤（创建新分支）
   */
  async rollbackPlan(
    planId: string,
    stepId: string,
    strategy = 'retry'
  ): Promise<{ new_branch: number; resume_from: string; strategy: string; marked_obsolete: number }> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ step_id: stepId, strategy }),
    });

    if (!response.ok) {
      await throwResponseError(response, '回滚失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '回滚失败');
    }

    return {
      new_branch: data.new_branch,
      resume_from: data.resume_from,
      strategy: data.strategy,
      marked_obsolete: data.marked_obsolete,
    };
  }

  /**
   * 获取审计日志（工具调用记录，含被安全拦截的调用）
   */
  async getAuditLogs(limit = 100, planId?: string): Promise<AuditLogEntry[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (planId) query.set('plan_id', planId);
    const response = await authFetch(`${API_BASE}/api/audit-logs?${query.toString()}`);
    if (!response.ok) {
      await throwResponseError(response, '获取审计日志失败');
    }
    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取审计日志失败');
    }
    return data.logs as AuditLogEntry[];
  }

  /**
   * 获取服务状态
   *
   * 注意：/api/status 响应没有 success 字段，直接返回数据本身。
   */
  async getServiceStatus(): Promise<{
    status: string;
    llm_providers: string[];
    tools_count: number;
    current_execution: string | null;
  }> {
    const response = await authFetch(`${API_BASE}/api/status`);
    if (!response.ok) {
      await throwResponseError(response, '获取服务状态失败');
    }

    return await response.json();
  }

  /**
   * 获取工具列表
   */
  async getTools(): Promise<Tool[]> {
    const response = await authFetch(`${API_BASE}/api/tools`);
    if (!response.ok) {
      await throwResponseError(response, '获取工具列表失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取工具列表失败');
    }

    return data.tools;
  }

  /**
   * 获取配置
   */
  async getConfig(): Promise<any> {
    const response = await authFetch(`${API_BASE}/api/config`);
    if (!response.ok) {
      await throwResponseError(response, '获取配置失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '获取配置失败');
    }

    return data.config;
  }

  /**
   * 更新配置
   */
  async updateConfig(apiKeys: Record<string, string>, baseUrls: Record<string, string>): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_keys: apiKeys, base_urls: baseUrls }),
    });

    if (!response.ok) {
      await throwResponseError(response, '更新配置失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '更新配置失败');
    }
  }

  /**
   * 重启后端服务
   */
  async restartService(): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/restart`, {
      method: 'POST',
    });

    if (!response.ok) {
      await throwResponseError(response, '重启失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '重启失败');
    }
  }

  /**
   * AI 助手对话
   */
  async chat(message: string, history: Array<{role: string, content: string}>): Promise<string> {
    const response = await authFetch(`${API_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history }),
    });

    if (!response.ok) {
      await throwResponseError(response, '对话失败');
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.detail || data.error || '对话失败');
    }

    return data.response;
  }

  /**
   * 连接 WebSocket 实时日志
   */
  connectLogs(onLog: (log: LogMessage) => void): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      // 替换现有连接：标记有意关闭，避免旧 socket 的 onclose 触发重连
      this.intentionalClose = true;
      this.ws.close();
      this.ws = null;
    }

    this.logCallbacks = [onLog];

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsHost = import.meta.env.VITE_API_BASE
      ? new URL(import.meta.env.VITE_API_BASE).host
      : window.location.host;
    // 浏览器 WebSocket API 不允许自定义请求头，因此 token 走查询参数
    const wsQuery = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : '';
    this.ws = new WebSocket(`${wsProtocol}//${wsHost}/ws/logs${wsQuery}`);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data?.type === 'plan_update') {
        const ev = data as PlanUpdateEvent;
        this.planUpdateCallbacks.forEach(cb => cb(ev));
        return;
      }
      const log = data as LogMessage;
      this.logCallbacks.forEach(cb => cb(log));
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket 错误:', error);
    };

    this.ws.onclose = (event) => {
      console.log('WebSocket 连接关闭');
      // 程序性关闭（替换/主动断开）不重连
      if (this.intentionalClose) {
        this.intentionalClose = false;
        return;
      }
      // 1008 = 服务端拒绝（token 无效），重连只会无限失败
      if (event.code === 1008) {
        console.error('WebSocket 认证失败，请检查 VITE_API_TOKEN，不再重试');
        return;
      }
      // 5 秒后尝试重连（持有定时器句柄，connectLogs/disconnectLogs 会先清掉）
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        if (this.logCallbacks.length > 0) {
          this.connectLogs(this.logCallbacks[0]);
        }
      }, 5000);
    };
  }

  /**
   * 注册计划状态变更推送回调（type=plan_update）。
   * 与日志回调不同：注册列表跨重连保留，直到 disconnectLogs。
   */
  onPlanUpdate(cb: (ev: PlanUpdateEvent) => void): void {
    this.planUpdateCallbacks.push(cb);
  }

  /**
   * 断开 WebSocket
   */
  disconnectLogs(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.intentionalClose = true;
      this.ws.close();
      this.ws = null;
    }
    this.logCallbacks = [];
    this.planUpdateCallbacks = [];
  }
}

export const apiClient = new ApiClient();
