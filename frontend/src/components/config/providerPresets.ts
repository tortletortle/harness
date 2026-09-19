/**
 * Built-in OpenAI-compatible provider presets.
 *
 * Presets only prefill an ID, a Base URL and a "where to get a key" link for
 * new providers. They never carry credentials and are never applied silently
 * to saved providers; the backend keeps validating every URL on save.
 */
export interface ProviderPreset {
  id: string;
  name: string;
  baseUrl: string;
  /** Console page where the user creates an API key. */
  keyUrl?: string;
  /** Local runtimes usually need no key. */
  local?: boolean;
  note?: string;
  /** Hostname hints used to recognise saved providers. */
  hosts: string[];
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: 'openai',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    keyUrl: 'https://platform.openai.com/api-keys',
    hosts: ['api.openai.com'],
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com/v1',
    keyUrl: 'https://platform.deepseek.com/api_keys',
    hosts: ['api.deepseek.com'],
  },
  {
    id: 'moonshot',
    name: 'Moonshot / Kimi',
    baseUrl: 'https://api.moonshot.cn/v1',
    keyUrl: 'https://platform.moonshot.cn/console/api-keys',
    hosts: ['api.moonshot.cn', 'api.moonshot.ai'],
  },
  {
    id: 'zhipu',
    name: '智谱 GLM',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys',
    hosts: ['open.bigmodel.cn'],
  },
  {
    id: 'dashscope',
    name: '阿里云百炼 / Qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    keyUrl: 'https://bailian.console.aliyun.com/?apiKey=1',
    hosts: ['dashscope.aliyuncs.com'],
  },
  {
    id: 'siliconflow',
    name: '硅基流动',
    baseUrl: 'https://api.siliconflow.cn/v1',
    keyUrl: 'https://cloud.siliconflow.cn/account/ak',
    hosts: ['api.siliconflow.cn', 'api.siliconflow.com'],
  },
  {
    id: 'stepfun',
    name: '阶跃星辰 StepFun',
    baseUrl: 'https://api.stepfun.com/v1',
    keyUrl: 'https://platform.stepfun.com/interface-key',
    hosts: ['api.stepfun.com'],
  },
  {
    id: 'volcengine',
    name: '火山方舟 / 豆包',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    keyUrl: 'https://console.volcengine.com/ark',
    hosts: ['ark.cn-beijing.volces.com'],
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    baseUrl: 'https://api.minimaxi.com/v1',
    keyUrl: 'https://platform.minimaxi.com/user-center/basic-information/interface-key',
    hosts: ['api.minimaxi.com', 'api.minimax.chat'],
  },
  {
    id: 'qianfan',
    name: '百度千帆',
    baseUrl: 'https://qianfan.baidubce.com/v2',
    keyUrl: 'https://console.bce.baidu.com/iam/#/iam/apikey/list',
    hosts: ['qianfan.baidubce.com'],
  },
  {
    id: 'hunyuan',
    name: '腾讯混元',
    baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
    keyUrl: 'https://console.cloud.tencent.com/hunyuan/api-key',
    hosts: ['api.hunyuan.cloud.tencent.com'],
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    baseUrl: 'https://openrouter.ai/api/v1',
    keyUrl: 'https://openrouter.ai/keys',
    hosts: ['openrouter.ai'],
  },
  {
    id: 'groq',
    name: 'Groq',
    baseUrl: 'https://api.groq.com/openai/v1',
    keyUrl: 'https://console.groq.com/keys',
    hosts: ['api.groq.com'],
  },
  {
    id: 'gemini',
    name: 'Google Gemini（OpenAI 兼容）',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    keyUrl: 'https://aistudio.google.com/apikey',
    hosts: ['generativelanguage.googleapis.com'],
  },
  {
    id: 'anthropic',
    name: 'Anthropic（OpenAI 兼容层）',
    baseUrl: 'https://api.anthropic.com/v1',
    keyUrl: 'https://console.anthropic.com/settings/keys',
    note: '仅覆盖 Chat Completions 子集，模型 ID 需手动填写。',
    hosts: ['api.anthropic.com'],
  },
  {
    id: 'xai',
    name: 'xAI Grok',
    baseUrl: 'https://api.x.ai/v1',
    keyUrl: 'https://console.x.ai',
    hosts: ['api.x.ai'],
  },
  {
    id: 'mistral',
    name: 'Mistral',
    baseUrl: 'https://api.mistral.ai/v1',
    keyUrl: 'https://console.mistral.ai/api-keys',
    hosts: ['api.mistral.ai'],
  },
  {
    id: 'ollama',
    name: 'Ollama（本地）',
    baseUrl: 'http://localhost:11434/v1',
    local: true,
    note: '默认端口 11434；无需密钥，模型需先 ollama pull。',
    hosts: ['localhost', '127.0.0.1'],
  },
  {
    id: 'lmstudio',
    name: 'LM Studio（本地）',
    baseUrl: 'http://localhost:1234/v1',
    local: true,
    note: '需在 LM Studio 中启动本地服务器并加载模型。',
    hosts: [],
  },
  {
    id: 'vllm',
    name: 'vLLM / 自建（本地）',
    baseUrl: 'http://localhost:8000/v1',
    local: true,
    note: '任何 OpenAI 兼容自建服务均可，按实际端口修改。',
    hosts: [],
  },
];

export const CUSTOM_PRESET: ProviderPreset = {
  id: 'custom',
  name: '自定义（OpenAI 兼容）',
  baseUrl: '',
  note: '填写供应商文档中的 Base URL，通常以 /v1 结尾。',
  hosts: [],
};

export const presetById = (id: string) =>
  PROVIDER_PRESETS.find((preset) => preset.id === id.toLowerCase());

/** Recognise a saved provider by its endpoint host (never by name alone). */
export const matchPreset = (url: string): ProviderPreset | undefined => {
  try {
    const host = new URL(url.trim()).hostname.toLowerCase();
    if (!host) return undefined;
    return PROVIDER_PRESETS.find(
      (preset) =>
        !preset.local &&
        preset.hosts.some((h) => host === h || host.endsWith('.' + h)),
    );
  } catch {
    return undefined;
  }
};

/** deepseek, deepseek2, deepseek3 … until it no longer collides. */
export const uniqueProviderId = (base: string, existing: string[]) => {
  const taken = new Set(existing.map((name) => name.toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  for (let index = 2; index < 100; index++) {
    const candidate = `${base}${index}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
  return `${base}_${Date.now().toString(36)}`;
};
