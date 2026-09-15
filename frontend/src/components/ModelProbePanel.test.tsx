import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { ModelProbePanel } from './ModelProbePanel';
import { apiClient } from '../api/client';
import type {
  ModelConfiguration,
  ModelProbeResult,
} from '../types/modelConfig';
vi.mock('../api/client', () => ({ apiClient: { probeModelConfig: vi.fn() } }));
const api = vi.mocked(apiClient);
const config: ModelConfiguration = {
  schema_version: 2,
  probe_version: 1,
  revision: 'r1',
  base_urls: { alpha: 'https://example.invalid/v1' },
  api_keys_configured: { alpha: true },
  models: { designer: { primary: { provider: 'alpha', model: 'saved-m' } } },
};
const result: ModelProbeResult = {
  ok: true,
  code: 'ok',
  message: '列表接口可用',
  provider: 'alpha',
  mode: 'connectivity',
  model: null,
  reachable: true,
  http_status: 200,
  latency_ms: 25,
  models: ['upstream-m'],
};
beforeEach(() => {
  vi.clearAllMocks();
  api.probeModelConfig.mockResolvedValue(result);
});
it('从不自动检测，连通性请求不发送模型或密钥', async () => {
  const user = userEvent.setup();
  render(<ModelProbePanel config={config} dirty={false} />);
  expect(api.probeModelConfig).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  );
  await screen.findByText(/已获取 1 个模型/);
  expect(api.probeModelConfig.mock.calls[0][0]).toEqual({
    provider: 'alpha',
    revision: 'r1',
    mode: 'connectivity',
  });
  expect(screen.getByRole('status')).toHaveTextContent('HTTP 200');
});
it('模型推理必须明确确认，取消不发送请求', async () => {
  const user = userEvent.setup();
  render(<ModelProbePanel config={config} dirty={false} />);
  await user.type(screen.getByLabelText('待测模型 ID'), 'custom');
  await user.click(screen.getByRole('button', { name: '测试模型' }));
  expect(screen.getByRole('alertdialog')).toHaveTextContent('可能产生费用');
  expect(api.probeModelConfig).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '取消测试' }));
  expect(api.probeModelConfig).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: '测试模型' }));
  await user.click(screen.getByRole('button', { name: '确认发送一次' }));
  await waitFor(() => expect(api.probeModelConfig).toHaveBeenCalledTimes(1));
  expect(api.probeModelConfig.mock.calls[0][0]).toMatchObject({
    mode: 'model',
    model: 'custom',
    acknowledge_cost: true,
  });
});
it('HTTP 可达但鉴权失败不能显示通过', async () => {
  api.probeModelConfig.mockResolvedValue({
    ...result,
    ok: false,
    code: 'authentication_failed',
    http_status: 401,
    message: '端点可达，但密钥缺失或无效。',
  });
  const user = userEvent.setup();
  render(<ModelProbePanel config={config} dirty={false} />);
  await user.click(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  );
  await screen.findByText('端点可达，但密钥缺失或无效。');
  expect(screen.getByRole('status')).toHaveTextContent('未通过');
});
it('未保存端点或旧后台禁止发出请求', () => {
  const { rerender } = render(<ModelProbePanel config={config} dirty />);
  expect(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  ).toBeDisabled();
  rerender(
    <ModelProbePanel
      config={{ ...config, probe_version: undefined }}
      dirty={false}
    />,
  );
  expect(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  ).toBeDisabled();
});
it('选择目录模型不自动推理，修改模型后清除旧结果', async () => {
  const user = userEvent.setup();
  render(<ModelProbePanel config={config} dirty={false} />);
  await user.click(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  );
  await user.click(await screen.findByText(/已获取 1 个模型/));
  await user.click(screen.getByRole('button', { name: 'upstream-m' }));
  expect(screen.getByLabelText('待测模型 ID')).toHaveValue('upstream-m');
  expect(screen.queryByRole('status')).toBeNull();
  expect(api.probeModelConfig).toHaveBeenCalledTimes(1);
});
it('网络失败保留输入并显示可重试提示', async () => {
  api.probeModelConfig.mockRejectedValue(new Error('network'));
  const user = userEvent.setup();
  render(<ModelProbePanel config={config} dirty={false} />);
  await user.type(screen.getByLabelText('待测模型 ID'), 'custom');
  await user.click(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  );
  await screen.findByRole('alert');
  expect(screen.getByLabelText('待测模型 ID')).toHaveValue('custom');
});

it('配置刷新会取消等待，迟到的结果不能污染新配置', async () => {
  let finish!: (r: ModelProbeResult) => void;
  api.probeModelConfig.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const user = userEvent.setup();
  const { rerender } = render(
    <ModelProbePanel config={config} dirty={false} />,
  );
  await user.click(
    screen.getByRole('button', { name: '检测连通性 / 获取模型' }),
  );
  const signal = api.probeModelConfig.mock.calls[0][1];
  rerender(
    <ModelProbePanel config={{ ...config, revision: 'r2' }} dirty={false} />,
  );
  expect(signal?.aborted).toBe(true);
  await act(async () => finish(result));
  expect(screen.queryByRole('status')).toBeNull();
});
