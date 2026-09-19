import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfigView } from './ConfigView';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { ModelConfiguration } from '../types/modelConfig';

vi.mock('../api/client', () => ({
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
const fixture = (): ModelConfiguration => ({
  schema_version: 2,
  probe_version: 1,
  catalog_version: 1,
  revision: 'version-1',
  base_urls: {
    alpha: 'https://alpha.invalid/v1',
    beta: 'https://beta.invalid/v1',
  },
  api_keys_configured: { alpha: true, beta: false },
  api_keys_persisted: {},
  environment_overrides: [],
  requires_restart: false,
  models: {
    designer: {
      primary: {
        provider: 'alpha',
        model: 'arbitrary/custom',
        temperature: 0.3,
        max_tokens: 4096,
      },
      fallback: { provider: 'beta', model: 'backup/model' },
    },
    special_voice: { primary: { provider: 'alpha', model: 'voice/custom' } },
  },
});
const ready = async () => {
  render(<ConfigView />);
  await screen.findByLabelText('designer 主模型 ID');
};
beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    currentView: 'config',
    configDirty: false,
    config: null,
    addLog: vi.fn(),
  });
  api.getConfig.mockResolvedValue(fixture());
  api.getModelCatalog.mockResolvedValue({});
  api.deleteModelCatalog.mockResolvedValue(undefined);
});

describe('模型配置', () => {
  it('从真实角色显示自定义模型，初始不可保存', async () => {
    await ready();
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue(
      'arbitrary/custom',
    );
    expect(screen.getByLabelText('special_voice 主模型 ID')).toHaveValue(
      'voice/custom',
    );
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
    // The first provider is selected and its detail is shown at once.
    expect(screen.getByLabelText('alpha Base URL')).toHaveValue(
      'https://alpha.invalid/v1',
    );
  });
  it('主备用模型均提交，携带版本、不带密钥且不重启', async () => {
    const user = userEvent.setup();
    await ready();
    await user.clear(screen.getByLabelText('designer 主模型 ID'));
    await user.type(screen.getByLabelText('designer 主模型 ID'), 'new-model');
    await user.clear(screen.getByLabelText('designer 备用模型 ID'));
    await user.type(
      screen.getByLabelText('designer 备用模型 ID'),
      'new-backup',
    );
    api.updateModelConfig.mockImplementation(async (payload) => ({
      ...fixture(),
      ...payload,
      schema_version: 2,
      revision: 'version-2',
      requires_restart: true,
    }));
    await user.click(screen.getByRole('button', { name: '保存配置' }));
    await screen.findByText(/配置已保存到本地/);
    const payload = api.updateModelConfig.mock.calls[0][0];
    expect(payload.revision).toBe('version-1');
    expect(payload.models.designer.primary.model).toBe('new-model');
    expect(payload.models.designer.fallback?.model).toBe('new-backup');
    expect(payload).not.toHaveProperty('api_keys');
    expect(api.restartService).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
  });
  it('切换提供商不静默替换自定义模型', async () => {
    const user = userEvent.setup();
    await ready();
    await user.selectOptions(
      screen.getByLabelText('designer 主模型提供商'),
      'beta',
    );
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue(
      'arbitrary/custom',
    );
  });
  it('密钥只在加密输入框提交，不回显已存值', async () => {
    const user = userEvent.setup();
    await ready();
    expect(screen.getByText('EAS_LLM_API_KEY_ALPHA')).toBeInTheDocument();
    expect(
      screen.getByText('密钥已注入', { selector: '.mc-status' }),
    ).toBeInTheDocument();
    const field = screen.getByLabelText('alpha API Key');
    expect(field).toHaveAttribute('type', 'password');
    // A saved key is never rendered back into the field.
    expect(field).toHaveValue('');
    api.setConfigKey.mockResolvedValue({ configured: true });
    await user.type(field, 'sk-typed-secret');
    await user.click(screen.getByRole('button', { name: '显示密钥' }));
    expect(field).toHaveAttribute('type', 'text');
    await user.click(screen.getByRole('button', { name: '隐藏密钥' }));
    expect(field).toHaveAttribute('type', 'password');
    await user.click(screen.getByRole('button', { name: 'alpha 保存密钥' }));
    await waitFor(() =>
      expect(api.setConfigKey).toHaveBeenCalledWith('alpha', 'sk-typed-secret'),
    );
    expect(field).toHaveValue('');
    await user.click(screen.getByRole('button', { name: 'alpha 保存密钥' }));
    await waitFor(() => expect(api.setConfigKey).toHaveBeenCalledWith('alpha', ''));
  });
  it('保存密钥不会丢弃未保存的模型草稿', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    api.setConfigKey.mockResolvedValue({ configured: true });
    await user.type(screen.getByLabelText('alpha API Key'), 'sk-abc');
    await user.click(screen.getByRole('button', { name: 'alpha 保存密钥' }));
    await waitFor(() => expect(api.setConfigKey).toHaveBeenCalled());
    expect(api.getConfig).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue(
      'arbitrary/custom-edited',
    );
  });
  it('无效 URL 与空模型禁止保存', async () => {
    const user = userEvent.setup();
    await ready();
    await user.clear(screen.getByLabelText('designer 主模型 ID'));
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
    await user.type(screen.getByLabelText('designer 主模型 ID'), 'fixed');
    await user.clear(screen.getByLabelText('alpha Base URL'));
    await user.type(
      screen.getByLabelText('alpha Base URL'),
      'https://user:secret@example.com',
    );
    expect(screen.getByLabelText('alpha Base URL')).toHaveAttribute(
      'aria-invalid',
      'true',
    );
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
  });
  it('预设一键填入标识与地址，新提供商在保存前不能单独保存密钥', async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole('button', { name: '添加提供商' }));
    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /DeepSeek/ }));
    expect(screen.getByLabelText('新提供商标识')).toHaveValue('deepseek');
    expect(screen.getByLabelText('新提供商 Base URL')).toHaveValue(
      'https://api.deepseek.com/v1',
    );
    await user.click(screen.getByRole('button', { name: '添加' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('deepseek Base URL')).toHaveValue(
      'https://api.deepseek.com/v1',
    );
    expect(screen.getByText(/新增，尚未保存/)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'deepseek 保存密钥' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled();
    expect(screen.getByLabelText('designer 主模型提供商')).toContainHTML(
      'deepseek',
    );
  });
  it('重复提供商标识不区分大小写，新提供商可以撤回', async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole('button', { name: '添加提供商' }));
    const name = screen.getByLabelText('新提供商标识');
    await user.type(name, 'ALPHA');
    await user.click(screen.getByRole('button', { name: '添加' }));
    expect(screen.getByRole('alert')).toHaveTextContent('已存在');
    await user.clear(name);
    await user.type(name, 'local_llm');
    await user.click(screen.getByRole('button', { name: '添加' }));
    expect(screen.getByLabelText('local_llm Base URL')).toBeInTheDocument();
    await user.click(
      screen.getByRole('button', { name: '撤回新增 local_llm' }),
    );
    expect(
      screen.queryByLabelText('local_llm Base URL'),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText('alpha Base URL')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
  });
  it('搜索同时过滤提供商列表与角色路由，无匹配显示空状态', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('搜索配置'), 'no-such-role');
    expect(screen.getByText('没有匹配的模型角色')).toBeInTheDocument();
    expect(screen.getByText('没有匹配的提供商')).toBeInTheDocument();
    // The selected provider stays editable even when filtered out.
    expect(screen.getByLabelText('alpha Base URL')).toBeInTheDocument();
  });
  it('旧后台只读，不发送会被忽略的配置', async () => {
    api.getConfig.mockResolvedValue({
      ...fixture(),
      schema_version: undefined,
    });
    await ready();
    expect(screen.getByRole('alert')).toHaveTextContent('当前为只读');
    expect(screen.getByLabelText('designer 主模型 ID')).toBeDisabled();
    expect(screen.getByLabelText('alpha Base URL')).toBeDisabled();
    expect(screen.getByRole('button', { name: '添加提供商' })).toBeDisabled();
  });
  it('冲突失败保留草稿', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    api.updateModelConfig.mockRejectedValue(new Error('配置已被其他操作更新'));
    await user.click(screen.getByRole('button', { name: '保存配置' }));
    await screen.findByRole('alert');
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue(
      'arbitrary/custom-edited',
    );
    expect(screen.getByRole('alert')).toHaveTextContent('草稿已保留');
  });
  it('重新加载需要确认，取消后保留编辑', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    await user.click(screen.getByRole('button', { name: '重新加载' }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue(
      'arbitrary/custom-edited',
    );
    expect(api.getConfig).toHaveBeenCalledTimes(1);
  });
  it('重新加载失败也不丢失草稿', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    await user.click(screen.getByRole('button', { name: '放弃修改' }));
    api.getConfig.mockRejectedValue(new Error('offline'));
    await user.click(screen.getByRole('button', { name: '放弃并重新加载' }));
    await screen.findByRole('alert');
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue(
      'arbitrary/custom-edited',
    );
  });
  it('离开配置页面会确认未保存修改（含未保存密钥）', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('alpha API Key'), 'sk-pending');
    await waitFor(() => expect(useAppStore.getState().configDirty).toBe(true));
    await user.clear(screen.getByLabelText('alpha API Key'));
    await waitFor(() => expect(useAppStore.getState().configDirty).toBe(false));
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    await waitFor(() => expect(useAppStore.getState().configDirty).toBe(true));
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    act(() => useAppStore.getState().setCurrentView('input'));
    expect(useAppStore.getState().currentView).toBe('config');
    confirm.mockReturnValue(true);
    act(() => useAppStore.getState().setCurrentView('input'));
    expect(useAppStore.getState().currentView).toBe('input');
    confirm.mockRestore();
  });
  it('已保存中文提供商可选且提交其他修改时不会被重命名或丢弃', async () => {
    const config = fixture();
    config.base_urls['国模'] = 'https://unicode.invalid/v1';
    config.models!.designer.primary.provider = '国模';
    api.getConfig.mockResolvedValue(config);
    api.updateModelConfig.mockImplementation(async (payload) => ({
      ...config,
      ...payload,
      schema_version: 2,
      revision: 'version-2',
    }));
    const user = userEvent.setup();
    await ready();
    expect(screen.getByLabelText('designer 主模型提供商')).toHaveValue('国模');
    await user.clear(screen.getByLabelText('designer 主模型 ID'));
    await user.type(screen.getByLabelText('designer 主模型 ID'), 'changed');
    await user.click(screen.getByRole('button', { name: '保存配置' }));
    await waitFor(() => expect(api.updateModelConfig).toHaveBeenCalledTimes(1));
    const payload = api.updateModelConfig.mock.calls[0][0];
    expect(payload.base_urls['国模']).toBe('https://unicode.invalid/v1');
    expect(payload.models.designer.primary).toEqual({
      provider: '国模',
      model: 'changed',
    });
  });
  it('已缓存的模型目录进入角色路由候选，并显示在提供商列表', async () => {
    api.getModelCatalog.mockResolvedValue({
      alpha: {
        models: ['cat-a', 'cat-b'],
        fetched_at: '2026-09-19T00:00:00+00:00',
        base_url: 'https://alpha.invalid/v1',
        stale: false,
      },
    });
    const user = userEvent.setup();
    await ready();
    expect(await screen.findByText(/已获取 2 个模型/)).toBeInTheDocument();
    expect(screen.getByRole('button', { pressed: true })).toHaveTextContent(
      '2 模型',
    );
    await user.click(screen.getByLabelText('designer 主模型 ID'));
    const listbox = screen.getByRole('listbox', { name: 'designer 主模型 ID 候选' });
    expect(within(listbox).getByText('cat-a')).toBeInTheDocument();
    expect(within(listbox).getByText('arbitrary/custom')).toBeInTheDocument();
    await user.click(within(listbox).getByText('cat-b'));
    expect(screen.getByLabelText('designer 主模型 ID')).toHaveValue('cat-b');
    expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled();
    // Fallback points at beta, which has no catalog: only configured IDs.
    await user.click(screen.getByLabelText('designer 备用模型 ID'));
    expect(
      within(
        screen.getByRole('listbox', { name: 'designer 备用模型 ID 候选' }),
      ).queryByText('cat-a'),
    ).toBeNull();
  });
  it('修改端点后目录标记为可能过期，清除目录调用后台', async () => {
    api.getModelCatalog.mockResolvedValue({
      alpha: {
        models: ['cat-a'],
        fetched_at: null,
        base_url: 'https://alpha.invalid/v1',
        stale: false,
      },
    });
    const user = userEvent.setup();
    await ready();
    await screen.findByText(/已获取 1 个模型/);
    expect(screen.queryByText(/列表可能过期/)).toBeNull();
    await user.type(screen.getByLabelText('alpha Base URL'), '/x');
    expect(screen.getByText(/列表可能过期/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '清除目录' }));
    expect(api.deleteModelCatalog).toHaveBeenCalledWith('alpha');
    expect(screen.queryByText(/已获取 1 个模型/)).toBeNull();
  });
  it('从模型列表直接分配到角色只改草稿', async () => {
    api.getModelCatalog.mockResolvedValue({
      alpha: {
        models: ['cat-a'],
        fetched_at: null,
        base_url: 'https://alpha.invalid/v1',
        stale: false,
      },
    });
    const user = userEvent.setup();
    await ready();
    await screen.findByText(/已获取 1 个模型/);
    await user.click(screen.getByRole('button', { name: 'cat-a 分配到角色' }));
    const menu = screen.getByRole('menu', { name: 'cat-a 分配到角色' });
    const designerGroup = within(menu).getByText('designer').closest(
      '.mc-menu-group',
    ) as HTMLElement;
    await user.click(within(designerGroup).getByRole('menuitem', { name: '备用模型' }));
    expect(screen.getByLabelText('designer 备用模型提供商')).toHaveValue('alpha');
    expect(screen.getByLabelText('designer 备用模型 ID')).toHaveValue('cat-a');
    expect(screen.getByText('designer 备用模型', { selector: '.mc-tag' })).toBeInTheDocument();
    expect(api.updateModelConfig).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '保存配置' })).toBeEnabled();
  });
});
