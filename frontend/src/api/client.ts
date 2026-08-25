/**
 * API 客户端
 * 与后端 FastAPI 服务通信
 */

import type { Design, ExecutionPlan, Tool, LogMessage } from '../types';

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

export class ApiClient {
  private ws: WebSocket | null = null;
  private logCallbacks: ((log: LogMessage) => void)[] = [];

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
      throw new Error(`API 错误: ${response.statusText}`);
    }

    const data = await response.json();
    if (!data.success) {
      throw new Error(data.error || '生成设计书失败');
    }

    return data.design;
  }

  /**
   * 获取设计书列表
   */
  async getDesigns(limit = 10): Promise<Design[]> {
    const response = await authFetch(`${API_BASE}/api/designs?limit=${limit}`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '获取设计书列表失败');
    }

    return data.designs;
  }

  /**
   * 获取单个设计书
   */
  async getDesign(designId: string): Promise<Design> {
    const response = await authFetch(`${API_BASE}/api/design/${designId}`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '获取设计书失败');
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

    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '审批失败');
    }

    return data;
  }

  /**
   * 获取执行计划列表
   */
  async getPlans(limit = 10): Promise<ExecutionPlan[]> {
    const response = await authFetch(`${API_BASE}/api/plans?limit=${limit}`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '获取计划列表失败');
    }

    return data.plans;
  }

  /**
   * 获取单个执行计划
   */
  async getPlan(planId: string): Promise<ExecutionPlan> {
    const response = await authFetch(`${API_BASE}/api/plan/${planId}`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '获取计划失败');
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

    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '执行失败');
    }
  }

  /**
   * 获取工具列表
   */
  async getTools(): Promise<Tool[]> {
    const response = await authFetch(`${API_BASE}/api/tools`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '获取工具列表失败');
    }

    return data.tools;
  }

  /**
   * 获取配置
   */
  async getConfig(): Promise<any> {
    const response = await authFetch(`${API_BASE}/api/config`);
    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '获取配置失败');
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

    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '更新配置失败');
    }
  }

  /**
   * 重启后端服务
   */
  async restartService(): Promise<void> {
    const response = await authFetch(`${API_BASE}/api/restart`, {
      method: 'POST',
    });

    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '重启失败');
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

    const data = await response.json();
    
    if (!data.success) {
      throw new Error(data.error || '对话失败');
    }

    return data.response;
  }

  /**
   * 连接 WebSocket 实时日志
   */
  connectLogs(onLog: (log: LogMessage) => void): void {
    if (this.ws) {
      this.ws.close();
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
      const log = JSON.parse(event.data) as LogMessage;
      this.logCallbacks.forEach(cb => cb(log));
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket 错误:', error);
    };

    this.ws.onclose = (event) => {
      console.log('WebSocket 连接关闭');
      // 1008 = 服务端拒绝（token 无效），重连只会无限失败
      if (event.code === 1008) {
        console.error('WebSocket 认证失败，请检查 VITE_API_TOKEN，不再重试');
        return;
      }
      // 5 秒后尝试重连
      setTimeout(() => {
        if (this.logCallbacks.length > 0) {
          this.connectLogs(this.logCallbacks[0]);
        }
      }, 5000);
    };
  }

  /**
   * 断开 WebSocket
   */
  disconnectLogs(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.logCallbacks = [];
  }
}

export const apiClient = new ApiClient();
