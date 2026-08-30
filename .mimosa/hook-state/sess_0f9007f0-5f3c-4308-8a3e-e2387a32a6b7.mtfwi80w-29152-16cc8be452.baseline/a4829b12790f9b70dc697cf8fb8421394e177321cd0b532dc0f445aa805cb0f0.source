/**
 * ApiClient 单元测试
 *
 * 通过 stub global.fetch 验证请求 URL/方法/头部/请求体与错误处理。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 客户端在模块加载时读取 VITE_API_TOKEN，因此先 stub 再动态导入
vi.stubEnv('VITE_API_TOKEN', 'test-token');

const { apiClient } = await import('./client');

type FetchStub = ReturnType<typeof vi.fn>;

/** 构造一个仅包含客户端所用字段（ok/status/json）的 Response 桩 */
function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

function getFetchMock(): FetchStub {
  return vi.mocked(globalThis.fetch) as unknown as FetchStub;
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('ApiClient 控制端点', () => {
  it('pausePlan 发送 POST 到 /api/plan/{id}/pause 并携带 Bearer token', async () => {
    getFetchMock().mockResolvedValue(jsonResponse(200, { success: true, message: '暂停请求已发出' }));

    await apiClient.pausePlan('plan-1');

    const [url, init] = getFetchMock().mock.calls[0];
    expect(url).toBe('/api/plan/plan-1/pause');
    expect(init.method).toBe('POST');
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
  });

  it('resumePlan 发送 POST 到 /api/plan/{id}/resume', async () => {
    getFetchMock().mockResolvedValue(jsonResponse(200, { success: true, message: '恢复请求已发出' }));

    await apiClient.resumePlan('plan-1');

    const [url, init] = getFetchMock().mock.calls[0];
    expect(url).toBe('/api/plan/plan-1/resume');
    expect(init.method).toBe('POST');
    expect((init.headers as Headers).get('Authorization')).toBe('Bearer test-token');
  });

  it('skipStep 发送 POST 到 /api/plan/{id}/skip-step/{stepId}', async () => {
    getFetchMock().mockResolvedValue(jsonResponse(200, { success: true, message: '步骤已标记为跳过' }));

    await apiClient.skipStep('plan-1', 'step-2');

    const [url, init] = getFetchMock().mock.calls[0];
    expect(url).toBe('/api/plan/plan-1/skip-step/step-2');
    expect(init.method).toBe('POST');
  });

  it('409 响应的 {"detail": ...} 被转换为中文 Error', async () => {
    getFetchMock().mockResolvedValue(
      jsonResponse(409, { detail: '只能暂停执行中的计划' })
    );

    await expect(apiClient.pausePlan('plan-1')).rejects.toThrow('只能暂停执行中的计划');
  });

  it('404 响应的 {"detail": ...} 被转换为中文 Error', async () => {
    getFetchMock().mockResolvedValue(jsonResponse(404, { detail: '计划不存在' }));

    await expect(apiClient.skipStep('plan-1', 'step-2')).rejects.toThrow('计划不存在');
  });

  it('rollbackPlan 发送 {"step_id","strategy"} 并解析响应', async () => {
    getFetchMock().mockResolvedValue(
      jsonResponse(200, {
        success: true,
        new_branch: 2,
        resume_from: 'step-1',
        strategy: 'retry',
        marked_obsolete: 3,
      })
    );

    const result = await apiClient.rollbackPlan('plan-1', 'step-1');

    const [url, init] = getFetchMock().mock.calls[0];
    expect(url).toBe('/api/plan/plan-1/rollback');
    expect(init.method).toBe('POST');
    expect((init.headers as Headers).get('Content-Type')).toBe('application/json');
    expect(JSON.parse(init.body)).toEqual({ step_id: 'step-1', strategy: 'retry' });

    expect(result).toEqual({
      new_branch: 2,
      resume_from: 'step-1',
      strategy: 'retry',
      marked_obsolete: 3,
    });
  });

  it('rollbackPlan 支持自定义 strategy', async () => {
    getFetchMock().mockResolvedValue(
      jsonResponse(200, {
        success: true,
        new_branch: 3,
        resume_from: 'step-2',
        strategy: 'regenerate',
        marked_obsolete: 1,
      })
    );

    await apiClient.rollbackPlan('plan-1', 'step-2', 'regenerate');

    const [, init] = getFetchMock().mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ step_id: 'step-2', strategy: 'regenerate' });
  });

  it('rollbackPlan 400 时抛出 detail 错误', async () => {
    getFetchMock().mockResolvedValue(jsonResponse(400, { detail: '缺少 step_id' }));

    await expect(apiClient.rollbackPlan('plan-1', 'step-1')).rejects.toThrow('缺少 step_id');
  });
});

describe('ApiClient getServiceStatus', () => {
  it('解析 current_execution 为 null 的状态', async () => {
    getFetchMock().mockResolvedValue(
      jsonResponse(200, {
        status: 'idle',
        llm_providers: ['openai'],
        tools_count: 5,
        current_execution: null,
      })
    );

    const status = await apiClient.getServiceStatus();

    const [url] = getFetchMock().mock.calls[0];
    expect(url).toBe('/api/status');
    expect(status).toEqual({
      status: 'idle',
      llm_providers: ['openai'],
      tools_count: 5,
      current_execution: null,
    });
  });

  it('解析 current_execution 为计划 ID 的状态', async () => {
    getFetchMock().mockResolvedValue(
      jsonResponse(200, {
        status: 'running',
        llm_providers: [],
        tools_count: 0,
        current_execution: 'plan-9',
      })
    );

    const status = await apiClient.getServiceStatus();

    expect(status.current_execution).toBe('plan-9');
    expect(status.status).toBe('running');
  });
});
