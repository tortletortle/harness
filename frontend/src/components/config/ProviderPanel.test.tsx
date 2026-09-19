import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfigView } from '../ConfigView';
import { ProviderPanel } from './ProviderPanel';
import { apiClient } from '../../api/client';
import { useAppStore } from '../../stores/appStore';
import type {
  ModelConfiguration,
  ModelProbeResult,
} from '../../types/modelConfig';

vi.mock('../../api/client', () => ({
  apiClient: {
    getConfig: vi.fn(),
    getModelCatalog: vi.fn(),
    deleteModelCatalog: vi.fn(),
    probeModelConfig: vi.fn(),
    updateModelConfig: vi.fn(),
    setConfigKey: vi.fn(),
    restartService: vi.fn(),
  },
}));
const api = vi.mocked(apiClient);
const config = (): ModelConfiguration => ({
  schema_version: 2,
  probe_version: 1,
  catalog_version: 1,
  revision: 'r1',
  base_urls: {
    alpha: 'https://example.invalid/v1',
    beta: 'https://beta.invalid/v1',
  },
  api_keys_configured: { alpha: true, beta: false },
  api_keys_persisted: {},
  environment_overrides: [],
  models: { designer: { primary: { provider: 'alpha', model: 'saved-m' } } },
});
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
const CHECK = '检测连通性 / 获取模型列表';
const SAVE_CHECK = '保存并检测 / 获取模型列表';
const ready = async () => {
  render(<ConfigView />);
  await screen.findByLabelText('alpha Base URL');
};
const panelProps = (overrides: Partial<Parameters<typeof ProviderPanel>[0]> = {}) => {
  const cfg = config();
  return {
    name: 'alpha',
    baseline: cfg,
    url: cfg.base_urls.alpha,
    onUrlChange: vi.fn(),
    isNew: false,
    inUse: true,
    onWithdraw: vi.fn(),
    keyDraft: '',
    onKeyDraftChange: vi.fn(),
    onSaveKey: vi.fn(),
    onCatalogFetched: vi.fn(),
    onCatalogForget: vi.fn(),
    roles: Object.entries(cfg.models!),
    onAssign: vi.fn(),
    dirty: false,
    blocked: false,
    readOnly: false,
    prepare: vi.fn(async () => cfg),
    ...overrides,
  };
};
beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    currentView: 'config',
    configDirty: false,
    config: null,
    addLog: vi.fn(),
  });
  api.getConfig.mockResolvedValue(config());
  api.getModelCatalog.mockResolvedValue({});
  api.probeModelConfig.mockResolvedValue(result);
  api.setConfigKey.mockResolvedValue({ configured: true });
});

describe('提供商面板 · 上游检测', () => {
  it('从不自动检测，连通性请求不发送模型或密钥', async () => {
    const user = userEvent.setup();
    await ready();
    expect(api.probeModelConfig).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: CHECK }));
    await screen.findByText(/已获取 1 个模型/);
    expect(api.probeModelConfig.mock.calls[0][0]).toEqual({
      provider: 'alpha',
      revision: 'r1',
      mode: 'connectivity',
    });
    expect(screen.getByRole('status')).toHaveTextContent('HTTP 200');
    // Fetched IDs become routing candidates for this provider immediately.
    await user.click(screen.getByLabelText('designer 主模型 ID'));
    expect(
      screen.getByRole('option', { name: /upstream-m/ }),
    ).toBeInTheDocument();
  });
  it('模型推理必须明确确认，取消不发送请求', async () => {
    const user = userEvent.setup();
    await ready();
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
      models: [],
    });
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole('button', { name: CHECK }));
    await screen.findByText('端点可达，但密钥缺失或无效。');
    expect(screen.getByRole('status')).toHaveTextContent('未通过');
    expect(screen.queryByText(/已获取 \d+ 个模型/)).toBeNull();
  });
  it('有未保存草稿时改为“保存并检测”：先保存配置、再保存密钥、最后检测已保存端点', async () => {
    api.updateModelConfig.mockImplementation(async (payload) => ({
      ...config(),
      ...payload,
      revision: 'r2',
    }));
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    await user.type(screen.getByLabelText('alpha API Key'), 'sk-new');
    expect(screen.getByRole('button', { name: '测试模型' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: SAVE_CHECK }));
    await screen.findByText(/已获取 1 个模型/);
    expect(api.updateModelConfig).toHaveBeenCalledTimes(1);
    expect(api.setConfigKey).toHaveBeenCalledWith('alpha', 'sk-new');
    expect(api.probeModelConfig.mock.calls[0][0]).toEqual({
      provider: 'alpha',
      revision: 'r2',
      mode: 'connectivity',
    });
    const order = [
      api.updateModelConfig.mock.invocationCallOrder[0],
      api.setConfigKey.mock.invocationCallOrder[0],
      api.probeModelConfig.mock.invocationCallOrder[0],
    ];
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(screen.getByRole('button', { name: CHECK })).toBeEnabled();
    expect(screen.getByLabelText('alpha API Key')).toHaveValue('');
  });
  it('保存失败或草稿无效时不发出检测', async () => {
    api.updateModelConfig.mockRejectedValue(new Error('配置已被其他操作更新'));
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    await user.click(screen.getByRole('button', { name: SAVE_CHECK }));
    await screen.findByRole('alert');
    expect(api.probeModelConfig).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText('designer 主模型 ID'));
    expect(screen.getByRole('button', { name: SAVE_CHECK })).toBeDisabled();
  });
  it('旧后台禁止发出请求', async () => {
    api.getConfig.mockResolvedValue({ ...config(), probe_version: undefined });
    await ready();
    expect(screen.getByRole('button', { name: CHECK })).toBeDisabled();
    expect(screen.getByText(/后台尚未提供检测接口/)).toBeInTheDocument();
  });
  it('选择目录模型不自动推理，修改模型后清除旧结果', async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole('button', { name: CHECK }));
    await screen.findByText(/已获取 1 个模型/);
    await user.click(screen.getByRole('button', { name: 'upstream-m' }));
    expect(screen.getByLabelText('待测模型 ID')).toHaveValue('upstream-m');
    expect(screen.queryByRole('status')).toBeNull();
    expect(api.probeModelConfig).toHaveBeenCalledTimes(1);
  });
  it('网络失败保留输入并显示可重试提示', async () => {
    api.probeModelConfig.mockRejectedValue(new Error('network'));
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('待测模型 ID'), 'custom');
    await user.click(screen.getByRole('button', { name: CHECK }));
    await screen.findByRole('alert');
    expect(screen.getByLabelText('待测模型 ID')).toHaveValue('custom');
  });
  it('切换提供商会丢弃上一个提供商的检测结果与测试输入', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('待测模型 ID'), 'custom');
    await user.click(screen.getByRole('button', { name: CHECK }));
    await screen.findByRole('status');
    await user.click(screen.getByRole('button', { name: /^beta/ }));
    expect(screen.getByLabelText('beta Base URL')).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
    expect(screen.getByLabelText('待测模型 ID')).toHaveValue('');
    expect(screen.getByText('未检测到密钥', { selector: '.mc-status' })).toBeInTheDocument();
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
    const props = panelProps();
    const { rerender } = render(<ProviderPanel {...props} />);
    await user.click(screen.getByRole('button', { name: CHECK }));
    const signal = api.probeModelConfig.mock.calls[0][1];
    rerender(
      <ProviderPanel {...props} baseline={{ ...props.baseline, revision: 'r2' }} />,
    );
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(result));
    expect(screen.queryByRole('status')).toBeNull();
    expect(props.onCatalogFetched).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: CHECK })).toBeEnabled();
  });
  it('“保存并检测”返回的新版本不会取消刚发起的检测', async () => {
    const user = userEvent.setup();
    const saved = { ...config(), revision: 'r2' };
    const props = panelProps({ dirty: true, prepare: vi.fn(async () => saved) });
    const { rerender } = render(<ProviderPanel {...props} />);
    await user.click(screen.getByRole('button', { name: SAVE_CHECK }));
    await waitFor(() => expect(api.probeModelConfig).toHaveBeenCalledTimes(1));
    rerender(<ProviderPanel {...props} dirty={false} baseline={saved} />);
    expect(api.probeModelConfig.mock.calls[0][1]?.aborted).toBe(false);
    expect(await screen.findByRole('status')).toHaveTextContent('HTTP 200');
    expect(props.onCatalogFetched).toHaveBeenCalledWith(['upstream-m']);
  });
});
