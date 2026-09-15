import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from './client';

const payload = {
  revision: 'test-revision',
  base_urls: { alpha: 'https://alpha.invalid/v1' },
  models: {
    designer: { primary: { provider: 'alpha', model: 'custom/model' } },
  },
};
afterEach(() => vi.unstubAllGlobals());
describe('模型配置 API 契约', () => {
  it('提交模型分配与版本，并返回持久化配置', async () => {
    const config = { ...payload, schema_version: 2, requires_restart: true };
    const fetch = vi
      .fn()
      .mockResolvedValue({
        ok: true,
        json: async () => ({ success: true, config }),
      });
    vi.stubGlobal('fetch', fetch);
    expect(await apiClient.updateModelConfig(payload)).toEqual(config);
    expect(fetch.mock.calls[0][0]).toContain('/api/config');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(payload);
    expect(fetch).toHaveBeenCalledTimes(1); // No implicit /api/restart or LLM call.
  });
  it('旧响应不能被当成模型保存成功', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue({ ok: true, json: async () => ({ success: true }) }),
    );
    await expect(apiClient.updateModelConfig(payload)).rejects.toThrow(
      '尚不支持',
    );
  });
  it('版本冲突向页面返回可识别原因', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue({
          ok: false,
          status: 409,
          json: async () => ({ detail: '配置已被其他操作更新' }),
        }),
    );
    await expect(apiClient.updateModelConfig(payload)).rejects.toThrow(
      '配置已被其他操作更新',
    );
  });
});

it('检测请求传递 AbortSignal，不发送任意地址或密钥', async () => {
  const result = { ok: false, code: 'authentication_failed' };
  const fetch = vi
    .fn()
    .mockResolvedValue({
      ok: true,
      json: async () => ({ success: true, result }),
    });
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  const request = {
    provider: 'alpha',
    revision: 'r1',
    mode: 'connectivity' as const,
  };
  expect(await apiClient.probeModelConfig(request, signal)).toEqual(result);
  expect(fetch.mock.calls[0][0]).toContain('/api/config/probe');
  expect(fetch.mock.calls[0][1].signal).toBe(signal);
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(request);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('检测时的配置冲突保留服务端可操作提示', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: '配置版本已变化，请重新加载后再检测。' }),
      }),
  );
  await expect(
    apiClient.probeModelConfig({
      provider: 'alpha',
      revision: 'r1',
      mode: 'connectivity',
    }),
  ).rejects.toThrow('配置版本已变化');
});
