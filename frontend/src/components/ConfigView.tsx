import { ModelProbePanel } from './ModelProbePanel';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  Check,
  Cpu,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Search,
  Server,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type {
  ModelConfiguration,
  ModelRole,
  ModelConfigUpdate,
} from '../types/modelConfig';
import './ConfigView.css';

const ROLE_NAMES: Record<string, [string, string]> = {
  designer: ['设计规划', '拆解需求，生成设计与执行计划'],
  executor: ['任务执行', '执行步骤，调用工具并校验结果'],
  toolmanager: ['工具管理', '选择工具与构建调用参数'],
  knowledge: ['知识检索', '理解上下文，检索与整理知识'],
  vision: ['视觉理解', '识别图片与界面中的信息'],
  codegen: ['代码生成', '编写、修改与分析代码'],
};
type Draft = {
  base_urls: Record<string, string>;
  models: Record<string, ModelRole>;
};
const toDraft = (cfg: ModelConfiguration): Draft => ({
  base_urls: { ...cfg.base_urls },
  models: structuredClone(cfg.models || cfg.agents || {}),
});
const same = (a: Draft | null, b: Draft | null) =>
  JSON.stringify(a) === JSON.stringify(b);
const validURL = (value: string) => {
  try {
    const url = new URL(value.trim());
    return (
      ['http:', 'https:'].includes(url.protocol) &&
      !!url.hostname &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      !/\s/.test(value.trim())
    );
  } catch {
    return false;
  }
};

export function ConfigView() {
  const [baseline, setBaseline] = useState<ModelConfiguration | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [tab, setTab] = useState<'models' | 'providers'>('models');
  const [search, setSearch] = useState('');
  const [newName, setNewName] = useState('');
  const [nameError, setNameError] = useState('');
  const [confirmReload, setConfirmReload] = useState(false);
  const sequence = useRef(0);
  const discardTrigger = useRef<HTMLElement | null>(null);
  const setConfig = useAppStore((s) => s.setConfig);
  const addLog = useAppStore((s) => s.addLog);
  const baselineDraft = useMemo(
    () => (baseline ? toDraft(baseline) : null),
    [baseline],
  );
  const dirty = !!draft && !same(draft, baselineDraft);
  const supported = baseline?.schema_version === 2 && !!baseline.revision;
  const busy = loading || saving;

  const load = useCallback(async () => {
    const id = ++sequence.current;
    setLoading(true);
    setError('');
    try {
      const cfg = await apiClient.getConfig();
      if (id !== sequence.current) return;
      setBaseline(cfg);
      setDraft(toDraft(cfg));
      setConfig(cfg);
      setNotice('');
    } catch {
      if (id === sequence.current)
        setError('加载失败，请检查后台连接后重试。当前编辑内容未被清除。');
    } finally {
      if (id === sequence.current) setLoading(false);
    }
  }, [setConfig]);

  useEffect(() => {
    void load();
    return () => {
      sequence.current++;
    };
  }, [load]);
  useEffect(() => {
    useAppStore.setState({ configDirty: dirty });
    const leave = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', leave);
    return () => {
      window.removeEventListener('beforeunload', leave);
      useAppStore.setState({ configDirty: false });
    };
  }, [dirty]);

  const validation = useMemo(() => {
    const errors: Record<string, string> = {};
    if (!draft) return errors;
    Object.entries(draft.base_urls).forEach(([name, url]) => {
      if (!validURL(url))
        errors[`url:${name}`] =
          '请输入完整 HTTP(S) 地址，不要包含密钥、查询参数或片段。';
    });
    Object.entries(draft.models).forEach(([role, settings]) => {
      (['primary', 'fallback'] as const).forEach((slot) => {
        const choice = settings[slot];
        if (
          choice &&
          (!(choice.provider in draft.base_urls) ||
            !choice.model.trim() ||
            choice.model.trim().length > 256 ||
            /[\x00-\x1f]/.test(choice.model))
        ) {
          errors[`${role}:${slot}`] =
            '请选择已配置的提供商，并填写有效的模型 ID。';
        }
      });
    });
    return errors;
  }, [draft]);

  const providers = Object.keys(draft?.base_urls || {});
  const roles = Object.entries(draft?.models || {}).filter(
    ([, value]) => !!value.primary,
  );
  const changedCount =
    providers.filter((p) => draft?.base_urls[p] !== baselineDraft?.base_urls[p])
      .length +
    roles.filter(
      ([name, value]) =>
        JSON.stringify(value) !== JSON.stringify(baselineDraft?.models[name]),
    ).length;
  const query = search.trim().toLowerCase();
  const shownRoles = roles.filter(([name, role]) =>
    `${name} ${ROLE_NAMES[name]?.join(' ') || ''} ${role.primary.provider} ${role.primary.model} ${role.fallback?.model || ''}`
      .toLowerCase()
      .includes(query),
  );
  const shownProviders = providers.filter((p) =>
    `${p} ${draft?.base_urls[p]}`.toLowerCase().includes(query),
  );
  const configured = providers.filter(
    (p) => baseline?.api_keys_configured[p],
  ).length;
  const suggestions = (provider: string) => [
    ...new Set(
      roles
        .flatMap(([, role]) => [role.primary, role.fallback])
        .filter((c) => c?.provider === provider)
        .map((c) => c!.model)
        .filter(Boolean),
    ),
  ];

  const changeChoice = (
    role: string,
    slot: 'primary' | 'fallback',
    field: 'provider' | 'model',
    value: string,
  ) => {
    setDraft(
      (current) =>
        current && {
          ...current,
          models: {
            ...current.models,
            [role]: {
              ...current.models[role],
              [slot]: { ...current.models[role][slot], [field]: value },
            },
          },
        },
    );
    setNotice('');
  };
  const addProvider = () => {
    const name = newName.trim();
    if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(name)) {
      setNameError(
        '使用字母开头的标识，可含数字、下划线或短横线（最多 64 位）。',
      );
      return;
    }
    if (providers.some((p) => p.toLowerCase() === name.toLowerCase())) {
      setNameError('这个提供商已存在，请使用不同的标识。');
      return;
    }
    setDraft(
      (current) =>
        current && {
          ...current,
          base_urls: { ...current.base_urls, [name]: '' },
        },
    );
    setNewName('');
    setNameError('');
    setSearch('');
    setNotice('');
  };
  const save = async () => {
    if (
      !draft ||
      !supported ||
      !dirty ||
      Object.keys(validation).length ||
      busy
    )
      return;
    setSaving(true);
    setError('');
    setNotice('');
    const payload: ModelConfigUpdate = {
      revision: baseline!.revision!,
      base_urls: Object.fromEntries(
        Object.entries(draft.base_urls).map(([p, url]) => [p, url.trim()]),
      ),
      models: {},
    };
    roles.forEach(([name, role]) => {
      payload.models[name] = {
        primary: {
          provider: role.primary.provider,
          model: role.primary.model.trim(),
        },
      };
      if (role.fallback)
        payload.models[name].fallback = {
          provider: role.fallback.provider,
          model: role.fallback.model.trim(),
        };
    });
    try {
      const cfg = await apiClient.updateModelConfig(payload);
      setBaseline(cfg);
      setDraft(toDraft(cfg));
      setConfig(cfg);
      setNotice(
        cfg.requires_restart
          ? '配置已保存到本地。运行中的配置尚未切换，请在任务结束后手动重启后台。'
          : '配置已保存，与当前运行配置一致。',
      );
      addLog({
        type: 'log',
        level: 'success',
        message: '模型配置已保存；未自动重启服务。',
      });
    } catch (failure) {
      // Never dump form values or keys into logs.
      setError(
        failure instanceof Error &&
          failure.message.includes('配置已被其他操作更新')
          ? '配置已被其他操作更新。你的草稿已保留；请先核对，再重新加载配置。'
          : '保存失败，草稿已保留。请检查后台版本、配置权限与网络连接后重试。',
      );
    } finally {
      setSaving(false);
    }
  };
  const openDiscard = () => {
    discardTrigger.current = document.activeElement as HTMLElement | null;
    setConfirmReload(true);
  };
  const closeDiscard = () => {
    setConfirmReload(false);
    discardTrigger.current?.focus();
  };
  const refresh = () => {
    if (dirty) openDiscard();
    else void load();
  };

  return (
    <div className="model-config">
      <div className="mc-header">
        <div>
          <div className="mc-eyebrow">
            <span /> WORKSPACE SETTINGS
          </div>
          <h2>
            模型配置<span className="mc-version">MODEL ROUTING</span>
          </h2>
          <p>让合适的模型，处理合适的任务。</p>
        </div>
        <button
          className="mc-button mc-secondary"
          onClick={refresh}
          disabled={busy}
        >
          <RefreshCw size={15} className={loading ? 'mc-spin' : ''} />
          重新加载
        </button>
      </div>

      <div className="mc-overview">
        <div className="mc-overview-copy">
          <div className="mc-orbit">
            <Cpu size={26} />
          </div>
          <div>
            <h3>你的模型工作台</h3>
            <p>统一管理服务端点，为每个角色分配主模型和备用模型。</p>
          </div>
        </div>
        <div className="mc-metrics">
          <div>
            <strong>{providers.length.toString().padStart(2, '0')}</strong>
            <span>提供商</span>
          </div>
          <div>
            <strong>{roles.length.toString().padStart(2, '0')}</strong>
            <span>模型角色</span>
          </div>
          <div>
            <strong>
              {configured}
              <small> / {providers.length}</small>
            </strong>
            <span>密钥已注入</span>
          </div>
        </div>
      </div>

      {error && (
        <div className="mc-message mc-danger" role="alert">
          <AlertCircle size={17} />
          <span>{error}</span>
          <button onClick={refresh} disabled={busy}>
            重新加载
          </button>
        </div>
      )}
      {notice && (
        <div className="mc-message mc-success" role="status">
          <Check size={17} />
          <span>{notice}</span>
        </div>
      )}
      {baseline && !supported && (
        <div className="mc-message mc-warning" role="alert">
          <AlertCircle size={17} />
          <span>
            后台版本尚不支持新版配置保存，当前为只读。请先加载更新后的后台，再重新加载此页面。
          </span>
        </div>
      )}
      {baseline?.requires_restart && !notice && (
        <div className="mc-message mc-warning">
          <AlertCircle size={17} />
          <span>
            已保存配置与运行中配置不同。请在任务结束后手动重启后台；此页面不会自动中断任务。
          </span>
        </div>
      )}

      {baseline && <ModelProbePanel config={baseline} dirty={dirty} />}

      <div className="mc-toolbar">
        <div
          className="mc-tabs"
          role="tablist"
          aria-label="配置分类"
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
              return;
            event.preventDefault();
            const next =
              event.key === 'Home'
                ? 'models'
                : event.key === 'End'
                  ? 'providers'
                  : tab === 'models'
                    ? 'providers'
                    : 'models';
            setTab(next);
            setSearch('');
            document.getElementById(`mc-tab-${next}`)?.focus();
          }}
        >
          <button
            role="tab"
            tabIndex={tab === 'models' ? 0 : -1}
            aria-selected={tab === 'models'}
            aria-controls="mc-panel"
            id="mc-tab-models"
            onClick={() => {
              setTab('models');
              setSearch('');
            }}
          >
            <Cpu size={16} />
            模型分配<span>{roles.length}</span>
          </button>
          <button
            role="tab"
            tabIndex={tab === 'providers' ? 0 : -1}
            aria-selected={tab === 'providers'}
            aria-controls="mc-panel"
            id="mc-tab-providers"
            onClick={() => {
              setTab('providers');
              setSearch('');
            }}
          >
            <Server size={16} />
            服务提供商<span>{providers.length}</span>
          </button>
        </div>
        <label className="mc-search">
          <Search size={16} />
          <input
            aria-label="搜索配置"
            placeholder={
              tab === 'models' ? '搜索角色、模型或提供商' : '搜索提供商或端点'
            }
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>

      {loading && !draft ? (
        <div className="mc-empty" role="status">
          <Loader2 className="mc-spin" size={24} />
          <h3>正在读取模型配置</h3>
          <p>只读取配置状态，不获取或展示密钥。</p>
        </div>
      ) : !draft ? (
        <div className="mc-empty">
          <Server size={28} />
          <h3>暂时无法读取配置</h3>
          <p>请确认后台已启动，再点击重新加载。</p>
        </div>
      ) : (
        <fieldset disabled={busy || !supported} className="mc-fieldset">
          <div role="tabpanel" id="mc-panel" aria-labelledby={`mc-tab-${tab}`}>
            {tab === 'models' ? (
              <>
                <div className="mc-section-heading">
                  <div>
                    <h3>按角色分配模型</h3>
                    <p>
                      模型 ID 可自由输入；候选仅来自现有配置，不代表已验证可用。
                    </p>
                  </div>
                  <span className="mc-hint">{shownRoles.length} 个角色</span>
                </div>
                <div className="mc-role-grid">
                  {shownRoles.map(([name, role]) => (
                    <article className="mc-card" key={name}>
                      <header>
                        <div className="mc-role-icon">
                          <Cpu size={19} />
                        </div>
                        <div>
                          <h4>{ROLE_NAMES[name]?.[0] || name}</h4>
                          <span className="mc-mono">{name}</span>
                        </div>
                        {JSON.stringify(role) !==
                          JSON.stringify(baselineDraft?.models[name]) && (
                          <span className="mc-draft-dot" title="未保存修改" />
                        )}
                      </header>
                      <p className="mc-description">
                        {ROLE_NAMES[name]?.[1] ||
                          '专项能力模型 · 沿用配置文件中的角色定义'}
                      </p>
                      {(['primary', 'fallback'] as const).map((slot) => {
                        const choice = role[slot];
                        if (!choice) return null;
                        const label = `${name} ${slot === 'primary' ? '主模型' : '备用模型'}`;
                        return (
                          <div className="mc-choice" key={slot}>
                            <div className="mc-choice-label">
                              <span
                                className={
                                  slot === 'primary'
                                    ? 'mc-primary-badge'
                                    : 'mc-fallback-badge'
                                }
                              >
                                {slot === 'primary' ? '主模型' : '备用'}
                              </span>
                              {slot === 'fallback' && (
                                <span>按运行时降级策略使用</span>
                              )}
                            </div>
                            <div className="mc-choice-fields">
                              <label>
                                <span>提供商</span>
                                <select
                                  aria-label={`${label}提供商`}
                                  value={choice.provider}
                                  onChange={(e) =>
                                    changeChoice(
                                      name,
                                      slot,
                                      'provider',
                                      e.target.value,
                                    )
                                  }
                                >
                                  <option value="">选择提供商</option>
                                  {!providers.includes(choice.provider) &&
                                    choice.provider && (
                                      <option value={choice.provider}>
                                        {choice.provider}（端点未配置）
                                      </option>
                                    )}
                                  {providers.map((p) => (
                                    <option key={p} value={p}>
                                      {p}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <ArrowRight size={14} className="mc-arrow" />
                              <label>
                                <span>模型 ID</span>
                                <input
                                  aria-label={`${label} ID`}
                                  aria-invalid={!!validation[`${name}:${slot}`]}
                                  aria-describedby={
                                    validation[`${name}:${slot}`]
                                      ? `error-${name}-${slot}`
                                      : undefined
                                  }
                                  list={`models-${name}-${slot}`}
                                  value={choice.model}
                                  onChange={(e) =>
                                    changeChoice(
                                      name,
                                      slot,
                                      'model',
                                      e.target.value,
                                    )
                                  }
                                  placeholder="输入模型 ID"
                                  autoComplete="off"
                                  spellCheck={false}
                                />
                                <datalist id={`models-${name}-${slot}`}>
                                  {suggestions(choice.provider).map((model) => (
                                    <option key={model} value={model} />
                                  ))}
                                </datalist>
                              </label>
                            </div>
                            {validation[`${name}:${slot}`] && (
                              <p
                                className="mc-field-error"
                                id={`error-${name}-${slot}`}
                              >
                                {validation[`${name}:${slot}`]}
                              </p>
                            )}
                          </div>
                        );
                      })}
                      <footer className="mc-card-footer">
                        <ShieldCheck size={13} />
                        <span>
                          保留高级参数
                          {role.primary.temperature != null
                            ? ` · 温度 ${role.primary.temperature}`
                            : ''}
                          {role.primary.max_tokens != null
                            ? ` · ${role.primary.max_tokens.toLocaleString()} tokens`
                            : ''}
                        </span>
                      </footer>
                    </article>
                  ))}
                </div>
                {shownRoles.length === 0 && (
                  <div className="mc-empty">
                    <Search size={25} />
                    <h3>
                      {roles.length ? '没有匹配的模型角色' : '尚未定义模型角色'}
                    </h3>
                    <p>
                      {roles.length
                        ? '试试角色名称、提供商或模型 ID。'
                        : '在 models.yaml 中定义角色后重新加载，不会自动生成虚假的默认角色。'}
                    </p>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="mc-section-heading">
                  <div>
                    <h3>服务提供商</h3>
                    <p>
                      配置 API
                      端点。密钥由环境变量管理，“已注入”不代表连接测试通过。
                    </p>
                  </div>
                  <span className="mc-hint">HTTP / HTTPS</span>
                </div>
                <div className="mc-provider-grid">
                  {shownProviders.map((name) => {
                    const existing = name in (baseline?.base_urls || {});
                    const inUse = roles.some(
                      ([, r]) =>
                        r.primary.provider === name ||
                        r.fallback?.provider === name,
                    );
                    return (
                      <article className="mc-card mc-provider-card" key={name}>
                        <header>
                          <div className="mc-role-icon">
                            <Server size={19} />
                          </div>
                          <h4>{name}</h4>
                          <span
                            className={`mc-status ${baseline?.api_keys_configured[name] ? 'mc-configured' : ''}`}
                          >
                            {baseline?.api_keys_configured[name]
                              ? '密钥已注入'
                              : '未检测到密钥'}
                          </span>
                          {!existing && (
                            <button
                              type="button"
                              className="mc-icon-button"
                              disabled={inUse}
                              aria-label={`撤回新增 ${name}`}
                              title={
                                inUse
                                  ? '请先解除主模型和备用模型的引用'
                                  : '撤回尚未保存的提供商'
                              }
                              onClick={() =>
                                setDraft((current) => {
                                  if (!current) return current;
                                  const urls = { ...current.base_urls };
                                  delete urls[name];
                                  return { ...current, base_urls: urls };
                                })
                              }
                            >
                              <Trash2 size={15} />
                            </button>
                          )}
                        </header>
                        <label className="mc-url-label">
                          <span>Base URL</span>
                          <input
                            aria-label={`${name} Base URL`}
                            aria-invalid={!!validation[`url:${name}`]}
                            aria-describedby={
                              validation[`url:${name}`]
                                ? `url-error-${name}`
                                : undefined
                            }
                            value={draft.base_urls[name]}
                            placeholder="https://api.example.com/v1"
                            onChange={(e) => {
                              const value = e.target.value;
                              setDraft(
                                (current) =>
                                  current && {
                                    ...current,
                                    base_urls: {
                                      ...current.base_urls,
                                      [name]: value,
                                    },
                                  },
                              );
                              setNotice('');
                            }}
                            autoComplete="off"
                            spellCheck={false}
                          />
                        </label>
                        {validation[`url:${name}`] && (
                          <p
                            className="mc-field-error"
                            id={`url-error-${name}`}
                          >
                            {validation[`url:${name}`]}
                          </p>
                        )}
                        {baseline?.environment_overrides?.includes(name) && (
                          <p className="mc-inline-warning">
                            环境变量 EAS_LLM_BASE_URL_{name.toUpperCase()}{' '}
                            优先于此地址；修改后可能不会生效。
                          </p>
                        )}
                        <div className="mc-key-note">
                          <KeyRound size={15} />
                          <div>
                            <span>API Key · 环境变量注入</span>
                            <code>EAS_LLM_API_KEY_{name.toUpperCase()}</code>
                            <p>不回显、不在浏览器中保存密钥。</p>
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
                {!shownProviders.length && (
                  <div className="mc-empty">
                    <Server size={25} />
                    <h3>
                      {providers.length
                        ? '没有匹配的提供商'
                        : '先添加一个服务提供商'}
                    </h3>
                    <p>添加端点后，即可为模型角色选择该提供商。</p>
                  </div>
                )}
                <div className="mc-add-provider">
                  <div>
                    <h4>
                      <Plus size={16} />
                      添加服务提供商
                    </h4>
                    <p>
                      兼容自定义端点；已保存的提供商不在此删除，以免破坏模板及备用模型引用。
                    </p>
                  </div>
                  <div>
                    <div className="mc-add-input">
                      <input
                        aria-label="新提供商标识"
                        placeholder="例如 openai 或 local_llm"
                        value={newName}
                        onChange={(e) => {
                          setNewName(e.target.value);
                          setNameError('');
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            addProvider();
                          }
                        }}
                      />
                      <button
                        className="mc-button mc-secondary"
                        onClick={addProvider}
                        disabled={!newName.trim()}
                      >
                        <Plus size={15} />
                        添加
                      </button>
                    </div>
                    {nameError && (
                      <p className="mc-field-error" role="alert">
                        {nameError}
                      </p>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </fieldset>
      )}
      <div className="mc-savebar">
        <div>
          <span className={`mc-save-indicator ${dirty ? 'mc-unsaved' : ''}`} />
          <div>
            <strong>
              {dirty ? `${changedCount} 项未保存修改` : '没有未保存的修改'}
            </strong>
            <p>
              {Object.keys(validation).length
                ? `有 ${Object.keys(validation).length} 处配置需要检查，请查看两个分类中的字段提示。`
                : '保存不重启服务，不触发模型请求，也不影响当前任务。'}
            </p>
          </div>
        </div>
        <div className="mc-save-actions">
          <button
            className="mc-button mc-secondary"
            onClick={() => openDiscard()}
            disabled={!dirty || busy}
          >
            放弃修改
          </button>
          <button
            className="mc-button mc-save"
            onClick={save}
            disabled={
              !supported || !dirty || busy || !!Object.keys(validation).length
            }
          >
            {saving ? (
              <Loader2 size={16} className="mc-spin" />
            ) : (
              <Save size={16} />
            )}
            {saving ? '正在保存…' : '保存配置'}
          </button>
        </div>
      </div>
      {confirmReload && (
        <div className="mc-dialog-backdrop">
          <div
            className="mc-dialog"
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                closeDiscard();
                return;
              }
              if (event.key === 'Tab') {
                const buttons = event.currentTarget.querySelectorAll('button');
                const first = buttons[0],
                  last = buttons[buttons.length - 1];
                if (event.shiftKey && document.activeElement === first) {
                  event.preventDefault();
                  last.focus();
                } else if (!event.shiftKey && document.activeElement === last) {
                  event.preventDefault();
                  first.focus();
                }
              }
            }}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="mc-discard-title"
            aria-describedby="mc-discard-desc"
          >
            <AlertCircle size={25} />
            <h3 id="mc-discard-title">放弃未保存的修改？</h3>
            <p id="mc-discard-desc">
              重新读取服务器上的已保存配置。只有加载成功后，才会替换当前草稿。
            </p>
            <div>
              <button
                className="mc-button mc-secondary"
                autoFocus
                onClick={() => closeDiscard()}
              >
                继续编辑
              </button>
              <button
                className="mc-button mc-save"
                onClick={() => {
                  closeDiscard();
                  void load();
                }}
              >
                放弃并重新加载
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
