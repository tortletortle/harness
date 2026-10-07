import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  Check,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Search,
  Server,
} from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type {
  ModelCatalog,
  ModelConfiguration,
  ModelConfigUpdate,
} from '../types/modelConfig';
import { AddProviderDialog } from './config/AddProviderDialog';
import { ConfirmDialog } from './config/ConfirmDialog';
import type { PickerOption } from './config/ModelPicker';
import { ProviderPanel, type KeyState } from './config/ProviderPanel';
import { RoleRoutingTable } from './config/RoleRoutingTable';
import {
  ROLE_NAMES,
  sameDraft,
  toDraft,
  validURL,
  type Draft,
  type Slot,
} from './config/shared';
import './ConfigView.css';

/**
 * Model workbench: provider master-detail on top, role routing table below.
 *
 * Contract preserved from the previous page: keys only travel through
 * /api/config/key and are never echoed; probes only target saved endpoints;
 * saving needs the revision from the last load and never restarts the backend.
 */
export function ConfigView() {
  const [baseline, setBaseline] = useState<ModelConfiguration | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  const [adding, setAdding] = useState(false);
  const [keyDraft, setKeyDraft] = useState<Record<string, string>>({});
  const [keyState, setKeyState] = useState<Record<string, KeyState>>({});
  const [confirmReload, setConfirmReload] = useState(false);
  const sequence = useRef(0);
  const discardTrigger = useRef<HTMLElement | null>(null);
  const setConfig = useAppStore((s) => s.setConfig);
  const addLog = useAppStore((s) => s.addLog);

  const baselineDraft = useMemo(
    () => (baseline ? toDraft(baseline) : null),
    [baseline],
  );
  const dirty = !!draft && !sameDraft(draft, baselineDraft);
  const pendingKeys = Object.values(keyDraft).filter((value) => value.trim()).length;
  const unsaved = dirty || pendingKeys > 0;
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
      setSelected((current) =>
        current && current in cfg.base_urls
          ? current
          : Object.keys(cfg.base_urls)[0] || '',
      );
      if (cfg.catalog_version === 1) {
        // The catalog is a convenience cache; an older backend or a read
        // failure must never block the configuration page.
        try {
          const cached = await apiClient.getModelCatalog();
          if (id === sequence.current) setCatalog(cached || {});
        } catch {
          if (id === sequence.current) setCatalog({});
        }
      }
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
    useAppStore.setState({ configDirty: unsaved });
    const leave = (event: BeforeUnloadEvent) => {
      if (unsaved) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', leave);
    return () => {
      window.removeEventListener('beforeunload', leave);
      useAppStore.setState({ configDirty: false });
    };
  }, [unsaved]);

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
  const blocked = Object.keys(validation).length > 0;

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
    `${name} ${ROLE_NAMES[name]?.join(' ') || ''} ${role.primary.provider} ${role.primary.model} ${role.fallback?.provider || ''} ${role.fallback?.model || ''}`
      .toLowerCase()
      .includes(query),
  );
  const shownProviders = providers.filter((p) =>
    `${p} ${draft?.base_urls[p] || ''} ${catalog[p]?.models.join(' ') || ''}`
      .toLowerCase()
      .includes(query),
  );
  const configured = providers.filter(
    (p) => baseline?.api_keys_configured[p],
  ).length;
  const catalogued = providers.filter((p) => catalog[p]?.models.length).length;
  const rolesUsing = (provider: string) =>
    roles.filter(
      ([, r]) => r.primary.provider === provider || r.fallback?.provider === provider,
    ).length;

  /** Catalog entry with staleness re-evaluated against the current draft URL. */
  const catalogFor = (provider: string) => {
    const entry = catalog[provider];
    if (!entry) return undefined;
    const overridden = baseline?.environment_overrides?.includes(provider);
    const draftUrl = (draft?.base_urls[provider] || '').trim();
    const stale =
      entry.stale || (!overridden && !!entry.base_url && draftUrl !== entry.base_url);
    return stale === entry.stale ? entry : { ...entry, stale };
  };
  const candidates = (provider: string): PickerOption[] => {
    const fromCatalog = catalog[provider]?.models || [];
    const seen = new Set(fromCatalog);
    const configuredIds = [
      ...new Set(
        roles
          .flatMap(([, role]) => [role.primary, role.fallback])
          .filter((c) => c?.provider === provider)
          .map((c) => c!.model.trim())
          .filter((id) => id && !seen.has(id)),
      ),
    ];
    return [
      ...fromCatalog.map((id) => ({ id, source: 'catalog' as const })),
      ...configuredIds.map((id) => ({ id, source: 'configured' as const })),
    ];
  };

  const changeChoice = (
    role: string,
    slot: Slot,
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
  const assign = (role: string, slot: Slot, provider: string, model: string) => {
    setDraft(
      (current) =>
        current && {
          ...current,
          models: {
            ...current.models,
            [role]: {
              ...current.models[role],
              [slot]: { ...current.models[role][slot], provider, model },
            },
          },
        },
    );
    setNotice('');
  };
  const changeUrl = (provider: string, value: string) => {
    setDraft(
      (current) =>
        current && {
          ...current,
          base_urls: { ...current.base_urls, [provider]: value },
        },
    );
    setNotice('');
  };
  const addProvider = (id: string, url: string) => {
    setDraft(
      (current) =>
        current && { ...current, base_urls: { ...current.base_urls, [id]: url } },
    );
    setSelected(id);
    setSearch('');
    setNotice('');
    setAdding(false);
  };
  const withdrawProvider = (provider: string) => {
    setDraft((current) => {
      if (!current) return current;
      const urls = { ...current.base_urls };
      delete urls[provider];
      return { ...current, base_urls: urls };
    });
    setKeyDraft((current) => {
      const next = { ...current };
      delete next[provider];
      return next;
    });
    setSelected((current) =>
      current === provider
        ? Object.keys(baseline?.base_urls || {})[0] || ''
        : current,
    );
  };

  const save = async (): Promise<ModelConfiguration | null> => {
    if (!draft || !supported || busy) return null;
    if (!dirty) return baseline;
    if (blocked) return null;
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
          ? '配置已保存。部分运行项仍待切换，请再保存一次或重启后台。'
          : '配置已保存，并已应用到当前会话。',
      );
      addLog({
        type: 'log',
        level: 'success',
        message: '模型配置已保存并热加载。',
      });
      return cfg;
    } catch (failure) {
      // Never dump form values or keys into logs.
      setError(
        failure instanceof Error &&
          failure.message.includes('配置已被其他操作更新')
          ? '配置已被其他操作更新。你的草稿已保留；请先核对，再重新加载配置。'
          : '保存失败，草稿已保留。请检查后台版本、配置权限与网络连接后重试。',
      );
      return null;
    } finally {
      setSaving(false);
    }
  };
  const saveKey = async (name: string, value: string): Promise<boolean> => {
    setKeyState((current) => ({
      ...current,
      [name]: { ...current[name], saving: true, message: undefined, failed: false },
    }));
    try {
      const result = await apiClient.setConfigKey(name, value);
      // Patch only the key status: a full reload here would discard an
      // unsaved endpoint/model draft the user is still editing.
      setBaseline((current) =>
        current
          ? {
              ...current,
              api_keys_configured: {
                ...current.api_keys_configured,
                [name]: result.configured,
              },
              api_keys_persisted: {
                ...current.api_keys_persisted,
                [name]: !value.trim() ? false : !!result.persisted,
              },
              api_key_environment_overrides: (
                current.api_key_environment_overrides || []
              ).filter((provider) =>
                result.env_released ? provider !== name : true,
              ),
            }
          : current,
      );
      const live = result.applied && !result.environment_override;
      setKeyState((current) => ({
        ...current,
        [name]: {
          saving: false,
          message: value.trim()
            ? live
              ? result.env_released
                ? '密钥已生效，并已停用环境变量中的旧密钥。'
                : '密钥已生效，当前会话无需重启。'
              : result.environment_override
                ? '已加密保存，但环境变量仍优先，当前不会使用这份密钥。'
                : '已加密保存；当前进程未能热加载，请重启后台。'
            : '已清除本地密钥。',
        },
      }));
      setKeyDraft((current) => {
        const next = { ...current };
        delete next[name];
        return next;
      });
      return true;
    } catch (failure) {
      setKeyState((current) => ({
        ...current,
        [name]: {
          saving: false,
          failed: true,
          message:
            failure instanceof Error ? failure.message : '密钥保存失败，请重试。',
        },
      }));
      return false;
    }
  };
  /** One click for new providers: save draft → save pending key → probe. */
  const prepareProbe = async (provider: string) => {
    let cfg: ModelConfiguration | null = baseline;
    if (dirty) {
      cfg = await save();
      if (!cfg) return null;
    }
    const pending = (keyDraft[provider] ?? '').trim();
    if (pending && !(await saveKey(provider, pending))) return null;
    return cfg;
  };
  const rememberCatalog = (provider: string, models: string[]) => {
    setCatalog((current) => ({
      ...current,
      [provider]: {
        models,
        fetched_at: new Date().toISOString(),
        base_url: (draft?.base_urls[provider] || '').trim() || null,
        stale: false,
      },
    }));
  };
  const forgetCatalog = (provider: string) => {
    setCatalog((current) => {
      const next = { ...current };
      delete next[provider];
      return next;
    });
    void apiClient.deleteModelCatalog(provider).catch(() => undefined);
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
    if (unsaved) openDiscard();
    else void load();
  };

  const selectedExists = !!draft && selected in draft.base_urls;
  const savedProviders = Object.keys(baseline?.base_urls || {});

  return (
    <div className="model-config">
      <div className="mc-header">
        <div>
          <div className="mc-eyebrow">
            <span /> WORKSPACE SETTINGS
          </div>
          <h2>
            模型配置<span className="mc-version">MODEL WORKBENCH</span>
          </h2>
          <p className="mc-summary">
            {draft
              ? `${providers.length} 个提供商 · ${roles.length} 个角色 · 密钥已注入 ${configured}/${providers.length} · 已获取目录 ${catalogued}/${providers.length}`
              : '统一管理服务端点、密钥与角色模型分配。'}
          </p>
        </div>
        <div className="mc-header-actions">
          <label className="mc-search">
            <Search size={16} />
            <input
              aria-label="搜索配置"
              placeholder="搜索提供商、角色或模型"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <button
            className="mc-button mc-secondary"
            onClick={refresh}
            disabled={busy}
          >
            <RefreshCw size={15} className={loading ? 'mc-spin' : ''} />
            重新加载
          </button>
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
            磁盘配置与运行中配置仍不一致。再保存一次可热加载；此页面不会中断进行中的任务。
          </span>
        </div>
      )}

      {loading && !draft ? (
        <div className="mc-empty" role="status">
          <Loader2 className="mc-spin" size={24} />
          <h3>正在读取模型配置</h3>
          <p>只读取配置状态，不获取或展示密钥。</p>
        </div>
      ) : !draft || !baseline ? (
        <div className="mc-empty">
          <Server size={28} />
          <h3>暂时无法读取配置</h3>
          <p>请确认后台已启动，再点击重新加载。</p>
        </div>
      ) : (
        <>
          <div className="mc-workbench">
            <aside className="mc-provider-list" aria-label="提供商列表">
              <div className="mc-list-head">
                <h3>
                  <Server size={16} /> 提供商
                </h3>
                <span className="mc-hint">{shownProviders.length}</span>
              </div>
              <ul>
                {shownProviders.map((name) => {
                  const isNew = !(name in baseline.base_urls);
                  const changed =
                    isNew || draft.base_urls[name] !== baseline.base_urls[name];
                  const entry = catalog[name];
                  return (
                    <li key={name}>
                      <button
                        type="button"
                        className={`mc-provider-item ${selected === name ? 'mc-selected' : ''}`}
                        aria-pressed={selected === name}
                        onClick={() => setSelected(name)}
                      >
                        <span
                          className={`mc-dot ${baseline.api_keys_configured[name] ? 'mc-dot-ok' : ''}`}
                          aria-hidden="true"
                        />
                        <span className="mc-provider-name">{name}</span>
                        <span className="mc-provider-meta">
                          {rolesUsing(name)} 角色
                          {entry ? ` · ${entry.models.length} 模型` : ''}
                          {isNew ? ' · 未保存' : ''}
                        </span>
                        {changed && (
                          <span className="mc-draft-dot" title="未保存修改" />
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
              {shownProviders.length === 0 && (
                <p className="mc-muted mc-list-empty">
                  {providers.length ? '没有匹配的提供商' : '先添加一个服务提供商'}
                </p>
              )}
              <button
                type="button"
                className="mc-button mc-secondary mc-add-button"
                onClick={() => setAdding(true)}
                disabled={busy || !supported}
              >
                <Plus size={15} /> 添加提供商
              </button>
            </aside>
            <fieldset
              disabled={busy || !supported}
              className="mc-fieldset mc-provider-detail"
            >
              {selectedExists ? (
                <ProviderPanel
                  key={selected}
                  name={selected}
                  baseline={baseline}
                  url={draft.base_urls[selected]}
                  urlError={validation[`url:${selected}`]}
                  onUrlChange={(value) => changeUrl(selected, value)}
                  isNew={!savedProviders.includes(selected)}
                  inUse={rolesUsing(selected) > 0}
                  onWithdraw={() => withdrawProvider(selected)}
                  keyDraft={keyDraft[selected] ?? ''}
                  keyState={keyState[selected]}
                  onKeyDraftChange={(value) => {
                    setKeyDraft((current) => ({ ...current, [selected]: value }));
                    setKeyState((current) => ({
                      ...current,
                      [selected]: { message: undefined, failed: false },
                    }));
                  }}
                  onSaveKey={(value) => void saveKey(selected, value)}
                  catalog={catalogFor(selected)}
                  onCatalogFetched={(models) => rememberCatalog(selected, models)}
                  onCatalogForget={() => forgetCatalog(selected)}
                  roles={roles}
                  onAssign={(role, slot, model) => assign(role, slot, selected, model)}
                  dirty={dirty}
                  blocked={blocked}
                  readOnly={!supported}
                  prepare={() => prepareProbe(selected)}
                />
              ) : (
                <div className="mc-empty">
                  <Server size={26} />
                  <h3>选择一个提供商</h3>
                  <p>在左侧选择或添加提供商后，即可配置端点、密钥并获取模型列表。</p>
                </div>
              )}
            </fieldset>
          </div>

          <fieldset disabled={busy || !supported} className="mc-fieldset">
            <RoleRoutingTable
              roles={shownRoles}
              totalRoles={roles.length}
              baselineModels={baselineDraft?.models}
              providers={providers}
              validation={validation}
              candidates={candidates}
              onChange={changeChoice}
            />
          </fieldset>
        </>
      )}

      <div className="mc-savebar">
        <div>
          <span className={`mc-save-indicator ${unsaved ? 'mc-unsaved' : ''}`} />
          <div>
            <strong>
              {dirty ? `${changedCount} 项未保存修改` : '没有未保存的配置修改'}
              {pendingKeys ? ` · ${pendingKeys} 个密钥待保存` : ''}
            </strong>
            <p>
              {blocked
                ? `有 ${Object.keys(validation).length} 处配置需要检查，请查看提供商与角色路由中的字段提示。`
                : '保存后立即用于新的模型调用。进行中的任务仍使用开始时的提供商。密钥在提供商面板单独加密保存。'}
            </p>
          </div>
        </div>
        <div className="mc-save-actions">
          <button
            className="mc-button mc-secondary"
            onClick={openDiscard}
            disabled={!dirty || busy}
          >
            放弃修改
          </button>
          <button
            className="mc-button mc-save"
            onClick={() => void save()}
            disabled={!supported || !dirty || busy || blocked}
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

      {adding && draft && (
        <AddProviderDialog
          existing={providers}
          onAdd={addProvider}
          onClose={() => setAdding(false)}
        />
      )}
      {confirmReload && (
        <ConfirmDialog
          id="mc-discard"
          icon={<AlertCircle size={25} />}
          title="放弃未保存的修改？"
          confirmLabel="放弃并重新加载"
          cancelLabel="继续编辑"
          onCancel={closeDiscard}
          onConfirm={() => {
            closeDiscard();
            setKeyDraft({});
            void load();
          }}
        >
          <p>重新读取服务器上的已保存配置。只有加载成功后，才会替换当前草稿。</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
