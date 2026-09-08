/**
 * 配置页面
 */

import { useEffect, useState } from 'react';
import { Settings, Save, Loader2, Plus, Trash2, RefreshCw, Edit, Check, X, ChevronDown, ChevronRight } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

interface ProviderConfig {
  name: string;
  apiKey: string;
  baseUrl: string;
}

interface EditState {
  [key: string]: {
    apiKey?: boolean;
    baseUrl?: boolean;
  };
}

// Model lists for each provider
const MODEL_LISTS: Record<string, string[]> = {
  newapi: ['claude-opus-5', 'claude-sonnet-5', 'claude-haiku-4-5-20251001', 'gpt-4o', 'gpt-4o-mini'],
  bbapi: ['claude-opus-5', 'claude-sonnet-5', 'claude-haiku-4-5-20251001'],
  hejuapi: ['gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-4.5-turbo'],
  stepfun: ['step-3.7-flash', 'step-3.5-flash', 'step-2x-large', 'step-gui', 'step-1o-turbo-vision', 'dr-search-api'],
};

const PREDEFINED_PROVIDERS = Object.keys(MODEL_LISTS);

export function ConfigView() {
  const { config, setConfig, addLog } = useAppStore();
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [modelAssignments, setModelAssignments] = useState<Record<string, { provider: string; model: string }>>({});
  const [newProviderName, setNewProviderName] = useState('');
  const [editState, setEditState] = useState<EditState>({});
  const [tempValues, setTempValues] = useState<Record<string, { apiKey?: string; baseUrl?: string }>>({});
  const [providersExpanded, setProvidersExpanded] = useState(true);
  const [modelsExpanded, setModelsExpanded] = useState(true);

  useEffect(() => {
    loadConfig();
  }, []);

  const loadConfig = async () => {
    setIsLoading(true);
    try {
      const cfg = await apiClient.getConfig();
      setConfig(cfg);
      
      // Build provider list from config
      const baseUrls = cfg.base_urls || {};
      const apiKeysConfigured = cfg.api_keys_configured || {};
      const providerList: ProviderConfig[] = Object.keys(baseUrls).map(name => ({
        name,
        apiKey: '',
        baseUrl: baseUrls[name] || '',
      }));
      // Add any providers in api_keys_configured but not in base_urls
      Object.keys(apiKeysConfigured).forEach(name => {
        if (!providerList.find(p => p.name === name)) {
          providerList.push({ name, apiKey: '', baseUrl: '' });
        }
      });
      setProviders(providerList);

      // Load model assignments from agents config
      const agents = cfg.agents || {};
      const assignments: Record<string, { provider: string; model: string }> = {};
      for (const [agentName, agentConfig] of Object.entries(agents as Record<string, any>)) {
        if (agentConfig?.primary) {
          assignments[agentName] = {
            provider: agentConfig.primary.provider || '',
            model: agentConfig.primary.model || '',
          };
        }
      }
      setModelAssignments(assignments);
    } catch (error) {
      addLog({ type: 'log', level: 'error', message: `加载配置失败: ${error}` });
    } finally {
      setIsLoading(false);
    }
  };

  const handleAddProvider = () => {
    const name = newProviderName.trim();
    if (!name) return;
    if (providers.find(p => p.name === name)) {
      addLog({ type: 'log', level: 'warning', message: `Provider "${name}" 已存在` });
      return;
    }
    setProviders([...providers, { name, apiKey: '', baseUrl: '' }]);
    setNewProviderName('');
  };

  const handleRemoveProvider = (name: string) => {
    setProviders(providers.filter(p => p.name !== name));
  };

  const handleProviderChange = (index: number, field: 'apiKey' | 'baseUrl', value: string) => {
    const updated = [...providers];
    updated[index] = { ...updated[index], [field]: value };
    setProviders(updated);
  };

  const maskApiKey = (key: string): string => {
    if (!key || key.length < 8) return '••••••••';
    return `${key.substring(0, 4)}••••${key.substring(key.length - 4)}`;
  };

  const handleEditToggle = (providerName: string, field: 'apiKey' | 'baseUrl') => {
    const provider = providers.find(p => p.name === providerName);
    if (!provider) return;

    setEditState(prev => ({
      ...prev,
      [providerName]: {
        ...prev[providerName],
        [field]: !prev[providerName]?.[field],
      },
    }));

    // Store the current value in temp
    if (!editState[providerName]?.[field]) {
      setTempValues(prev => ({
        ...prev,
        [providerName]: {
          ...prev[providerName],
          [field]: provider[field],
        },
      }));
    }
  };

  const handleSaveField = (providerName: string, field: 'apiKey' | 'baseUrl') => {
    setEditState(prev => ({
      ...prev,
      [providerName]: {
        ...prev[providerName],
        [field]: false,
      },
    }));
  };

  const handleCancelField = (providerName: string, field: 'apiKey' | 'baseUrl') => {
    const index = providers.findIndex(p => p.name === providerName);
    if (index === -1) return;

    // Restore the original value
    const originalValue = tempValues[providerName]?.[field] || '';
    handleProviderChange(index, field, originalValue);

    setEditState(prev => ({
      ...prev,
      [providerName]: {
        ...prev[providerName],
        [field]: false,
      },
    }));
  };

  const handleModelAssignmentChange = (agent: string, field: 'provider' | 'model', value: string) => {
    setModelAssignments(prev => ({
      ...prev,
      [agent]: { ...prev[agent], [field]: value },
    }));
  };

  const handleSave = async () => {
    setIsSaving(true);
    addLog({ type: 'log', level: 'info', message: '保存配置并重启服务...' });

    try {
      // Build api_keys and base_urls from providers list
      const apiKeys: Record<string, string> = {};
      const baseUrls: Record<string, string> = {};
      for (const p of providers) {
        if (p.apiKey) apiKeys[p.name] = p.apiKey;
        if (p.baseUrl) baseUrls[p.name] = p.baseUrl;
      }

      await apiClient.updateConfig(apiKeys, baseUrls);
      
      // Trigger restart
      try {
        await apiClient.restartService();
        addLog({ type: 'log', level: 'success', message: '配置已保存，服务正在重启...' });
      } catch {
        addLog({ type: 'log', level: 'warning', message: '配置已保存，但自动重启失败。请手动重启服务。' });
      }
    } catch (error) {
      addLog({ type: 'log', level: 'error', message: `保存失败: ${error}` });
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-zinc-600 animate-spin" />
      </div>
    );
  }

  const agents = ['designer', 'executor', 'toolmanager', 'knowledge', 'vision', 'codegen'];

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-4xl space-y-5">
        {/* Header */}
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-white">系统配置</h2>
          <p className="mt-1 text-sm text-zinc-500">管理 Provider、模型分配和服务运行参数</p>
        </div>

        {/* API Providers */}
        <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-[#111419]/90">
          <button
            onClick={() => setProvidersExpanded(!providersExpanded)}
            className="w-full flex items-center justify-between p-4 text-left transition-colors hover:bg-white/[0.025]"
          >
            <h3 className="text-sm font-medium text-white flex items-center gap-2">
              <Settings className="w-4 h-4" />
              API 提供商
            </h3>
            {providersExpanded ? <ChevronDown className="w-4 h-4 text-zinc-400" /> : <ChevronRight className="w-4 h-4 text-zinc-400" />}
          </button>
          {providersExpanded && (
            <div className="p-6 pt-0">
              <div className="space-y-4">
            {providers.map((provider, index) => (
              <div key={provider.name} className="space-y-2 pb-4 border-b border-zinc-800 last:border-0 last:pb-0">
                <div className="flex items-center justify-between">
                  <label className="text-sm text-zinc-300 font-medium">
                    {provider.name}
                  </label>
                  <div className="flex items-center gap-2">
                    {config?.api_keys_configured?.[provider.name] && (
                      <span className="text-xs px-2 py-0.5 bg-green-600/20 text-green-400 rounded">
                        已配置
                      </span>
                    )}
                    <button
                      onClick={() => handleRemoveProvider(provider.name)}
                      className="text-zinc-500 hover:text-red-400 transition-colors"
                      title="删除"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-xs text-zinc-500 mb-1 block">API Key</label>
                    <div className="flex gap-2">
                      {editState[provider.name]?.apiKey ? (
                        <>
                          <input
                            type="password"
                            value={provider.apiKey}
                            onChange={(e) => handleProviderChange(index, 'apiKey', e.target.value)}
                            placeholder="输入新的 API Key"
                            className="bg-black/20 border border-white/[0.08] rounded px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-violet-400/50"
                          />
                          <button
                            onClick={() => handleSaveField(provider.name, 'apiKey')}
                            className="p-2 bg-green-600 hover:bg-green-700 text-white rounded transition-colors"
                            title="保存"
                          >
                            <Check className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => handleCancelField(provider.name, 'apiKey')}
                            className="p-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded transition-colors"
                            title="取消"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        </>
                      ) : (
                        <>
                          <div className="flex-1 bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-zinc-400">
                            {config?.api_keys_configured?.[provider.name] ? maskApiKey(provider.apiKey) : '未配置'}
                          </div>
                          <button
                            onClick={() => handleEditToggle(provider.name, 'apiKey')}
                            className="p-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded transition-colors"
                            title="编辑"
                          >
                            <Edit className="w-4 h-4" />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-zinc-500 mb-1 block">Base URL</label>
                    <div className="flex gap-2">
                      {editState[provider.name]?.baseUrl ? (
                        <>
                          <input
                            type="text"
                            value={provider.baseUrl}
                            onChange={(e) => handleProviderChange(index, 'baseUrl', e.target.value)}
                            placeholder="https://api.example.com/v1"
                            className="bg-black/20 border border-white/[0.08] rounded px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-violet-400/50"
                          />
                          <button
                            onClick={() => handleSaveField(provider.name, 'baseUrl')}
                            className="p-2 bg-green-600 hover:bg-green-700 text-white rounded transition-colors"
                            title="保存"
                          >
                            <Check className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => handleCancelField(provider.name, 'baseUrl')}
                            className="p-2 bg-zinc-700 hover:bg-zinc-600 text-white rounded transition-colors"
                            title="取消"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        </>
                      ) : (
                        <>
                          <div className="flex-1 bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-zinc-400 truncate">
                            {provider.baseUrl || '未配置'}
                          </div>
                          <button
                            onClick={() => handleEditToggle(provider.name, 'baseUrl')}
                            className="p-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded transition-colors"
                            title="编辑"
                          >
                            <Edit className="w-4 h-4" />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ))}
              </div>

              {/* Add new provider */}
              <div className="mt-4 pt-4 border-t border-zinc-800">
            <div className="flex gap-2">
              <input
                type="text"
                value={newProviderName}
                onChange={(e) => setNewProviderName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleAddProvider()}
                placeholder="新 Provider 名称"
                className="bg-black/20 border border-white/[0.08] rounded px-3 py-2 text-sm text-white placeholder:text-zinc-600 focus:outline-none focus:border-violet-400/50"
              />
              <button
                onClick={handleAddProvider}
                disabled={!newProviderName.trim()}
                className="flex items-center gap-1 px-4 py-2 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50 text-white text-sm rounded transition-colors"
              >
                <Plus className="w-4 h-4" />
                添加
              </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Model Assignments */}
        <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-[#111419]/90">
          <button
            onClick={() => setModelsExpanded(!modelsExpanded)}
            className="w-full flex items-center justify-between p-4 text-left transition-colors hover:bg-white/[0.025]"
          >
            <div>
              <h3 className="text-sm font-medium text-white flex items-center gap-2">
                <RefreshCw className="w-4 h-4" />
                模型分配
              </h3>
              <p className="text-xs text-zinc-500 mt-1">为每个 Agent 指定使用的 Provider 和模型</p>
            </div>
            {modelsExpanded ? <ChevronDown className="w-4 h-4 text-zinc-400" /> : <ChevronRight className="w-4 h-4 text-zinc-400" />}
          </button>
          {modelsExpanded && (
            <div className="px-6 pb-6">
              <div className="space-y-3">
                {agents.map((agent) => {
                  const selectedProvider = modelAssignments[agent]?.provider || '';
                  const isCustomProvider = selectedProvider && !PREDEFINED_PROVIDERS.includes(selectedProvider);
                  const modelList = selectedProvider ? MODEL_LISTS[selectedProvider] : [];
                  
                  return (
                    <div key={agent} className="grid grid-cols-3 gap-3 items-center">
                      <label className="text-sm text-zinc-300 font-medium capitalize">{agent}</label>
                      <select
                    value={selectedProvider}
                    onChange={(e) => handleModelAssignmentChange(agent, 'provider', e.target.value)}
                    className="bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-600"
                  >
                    <option value="">选择 Provider</option>
                    {providers.map(p => (
                      <option key={p.name} value={p.name}>{p.name}</option>
                    ))}
                      </select>
                      {isCustomProvider || !selectedProvider ? (
                        <input
                          type="text"
                          value={modelAssignments[agent]?.model || ''}
                          onChange={(e) => handleModelAssignmentChange(agent, 'model', e.target.value)}
                          placeholder="模型名称"
                          className="bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-white placeholder-zinc-600 focus:outline-none focus:border-blue-600"
                        />
                      ) : (
                        <select
                          value={modelAssignments[agent]?.model || ''}
                          onChange={(e) => handleModelAssignmentChange(agent, 'model', e.target.value)}
                          className="bg-zinc-950 border border-zinc-800 rounded px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-600"
                        >
                          <option value="">选择模型</option>
                          {modelList.map(model => (
                            <option key={model} value={model}>{model}</option>
                          ))}
                        </select>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Save Button */}
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:bg-zinc-800 disabled:text-zinc-600 text-white font-medium py-3 rounded-lg transition-colors"
        >
          {isSaving ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>保存并重启中...</span>
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              <span>保存配置并重启服务</span>
            </>
          )}
        </button>

        {/* Info */}
        <div className="bg-blue-600/10 border border-blue-600/20 rounded-lg p-4">
          <p className="text-xs text-blue-400">
            💡 保存配置后系统将自动重启后端服务以应用更改
          </p>
        </div>
      </div>
    </div>
  );
}
