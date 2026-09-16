import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfigView } from './ConfigView';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { ModelConfiguration } from '../types/modelConfig';

vi.mock('../api/client', () => ({
  apiClient: {
    getConfig: vi.fn(),
    updateModelConfig: vi.fn(),
    setConfigKey: vi.fn(),
    restartService: vi.fn(),
  },
}));
const api = vi.mocked(apiClient);
const fixture = (): ModelConfiguration => ({
  schema_version: 2,
  revision: 'version-1',
  base_urls: {
    alpha: 'https://alpha.invalid/v1',
    beta: 'https://beta.invalid/v1',
  },
  api_keys_configured: { alpha: true, beta: false },
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
    await user.click(screen.getByRole('tab', { name: /服务提供商/ }));
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
    await user.click(screen.getByRole('button', { name: 'alpha 保存密钥' }));
    await waitFor(() =>
      expect(api.setConfigKey).toHaveBeenCalledWith('alpha', 'sk-typed-secret'),
    );
    await user.clear(field);
    await user.click(screen.getByRole('button', { name: 'alpha 保存密钥' }));
    await waitFor(() => expect(api.setConfigKey).toHaveBeenCalledWith('alpha', ''));
  });
  it('保存密钥不会丢弃未保存的模型草稿', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('designer 主模型 ID'), '-edited');
    await user.click(screen.getByRole('tab', { name: /服务提供商/ }));
    api.setConfigKey.mockResolvedValue({ configured: true });
    await user.type(screen.getByLabelText('alpha API Key'), 'sk-abc');
    await user.click(screen.getByRole('button', { name: 'alpha 保存密钥' }));
    await waitFor(() => expect(api.setConfigKey).toHaveBeenCalled());
    expect(api.getConfig).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('tab', { name: /模型分配/ }));
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
    await user.click(screen.getByRole('tab', { name: /服务提供商/ }));
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
  it('重复提供商标识不区分大小写，新提供商可以撤回', async () => {
    const user = userEvent.setup();
    await ready();
    await user.click(screen.getByRole('tab', { name: /服务提供商/ }));
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
  });
  it('搜索无匹配显示空状态', async () => {
    const user = userEvent.setup();
    await ready();
    await user.type(screen.getByLabelText('搜索配置'), 'no-such-role');
    expect(screen.getByText('没有匹配的模型角色')).toBeInTheDocument();
  });
  it('旧后台只读，不发送会被忽略的配置', async () => {
    api.getConfig.mockResolvedValue({
      ...fixture(),
      schema_version: undefined,
    });
    await ready();
    expect(screen.getByRole('alert')).toHaveTextContent('当前为只读');
    expect(screen.getByLabelText('designer 主模型 ID')).toBeDisabled();
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
  it('离开配置页面会确认未保存修改', async () => {
    const user = userEvent.setup();
    await ready();
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
});


it('已保存中文提供商可选且提交其他修改时不会被重命名或丢弃',async()=>{
 const config=fixture();config.base_urls['国模']='https://unicode.invalid/v1';
 config.models.designer.primary.provider='国模';
 api.getConfig.mockResolvedValue(config);
 api.updateModelConfig.mockImplementation(async payload=>({...config,...payload,schema_version:2,revision:'version-2'}));
 const user=userEvent.setup();await ready();
 expect(screen.getByLabelText('designer 主模型提供商')).toHaveValue('国模');
 await user.clear(screen.getByLabelText('designer 主模型 ID'));
 await user.type(screen.getByLabelText('designer 主模型 ID'),'changed');
 await user.click(screen.getByRole('button',{name:'保存配置'}));
 await waitFor(()=>expect(api.updateModelConfig).toHaveBeenCalledTimes(1));
 const payload=api.updateModelConfig.mock.calls[0][0];
 expect(payload.base_urls['国模']).toBe('https://unicode.invalid/v1');
 expect(payload.models.designer.primary.provider).toBe('国模');
 expect(api.restartService).not.toHaveBeenCalled();
});
