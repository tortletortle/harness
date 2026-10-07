import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Eye,
  EyeOff,
  ExternalLink,
  FlaskConical,
  KeyRound,
  Loader2,
  Radio,
  Server,
  Trash2,
} from 'lucide-react';
import { apiClient } from '../../api/client';
import type {
  ModelCatalogEntry,
  ModelConfiguration,
  ModelProbeResult,
  ModelRole,
} from '../../types/modelConfig';
import { ConfirmDialog } from './ConfirmDialog';
import { ModelPicker, type PickerOption } from './ModelPicker';
import { matchPreset, presetById } from './providerPresets';
import {
  SLOT_LABEL,
  envKeyName,
  envUrlName,
  formatFetchedAt,
  roleTitle,
  usageIndex,
  type Slot,
} from './shared';

export interface KeyState {
  saving?: boolean;
  message?: string;
  failed?: boolean;
}

interface Props {
  name: string;
  /** Last saved snapshot (revision, key flags, probe support). */
  baseline: ModelConfiguration;
  url: string;
  urlError?: string;
  onUrlChange: (value: string) => void;
  isNew: boolean;
  inUse: boolean;
  onWithdraw: () => void;
  keyDraft: string;
  keyState?: KeyState;
  onKeyDraftChange: (value: string) => void;
  onSaveKey: (value: string) => void;
  catalog?: ModelCatalogEntry;
  onCatalogFetched: (models: string[]) => void;
  onCatalogForget: () => void;
  roles: [string, ModelRole][];
  onAssign: (role: string, slot: Slot, model: string) => void;
  /** Draft differs from the saved snapshot. */
  dirty: boolean;
  /** Draft cannot be saved right now (validation errors). */
  blocked: boolean;
  readOnly: boolean;
  /**
   * Save the draft and any pending key for this provider, returning the saved
   * configuration (or null when saving failed). Probes only ever target saved
   * endpoints, so "save and check" is the one-click path for new providers.
   */
  prepare: () => Promise<ModelConfiguration | null>;
}

type Mode = 'connectivity' | 'model';

export function ProviderPanel({
  name,
  baseline,
  url,
  urlError,
  onUrlChange,
  isNew,
  inUse,
  onWithdraw,
  keyDraft,
  keyState,
  onKeyDraftChange,
  onSaveKey,
  catalog,
  onCatalogFetched,
  onCatalogForget,
  roles,
  onAssign,
  dirty,
  blocked,
  readOnly,
  prepare,
}: Props) {
  const [reveal, setReveal] = useState(false);
  const [testModel, setTestModel] = useState('');
  const [filter, setFilter] = useState('');
  const [result, setResult] = useState<ModelProbeResult | null>(null);
  const [error, setError] = useState('');
  const [testing, setTesting] = useState<Mode | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [assigning, setAssigning] = useState<string | null>(null);
  const active = useRef<{ controller: AbortController; revision: string } | null>(
    null,
  );
  const testTrigger = useRef<HTMLButtonElement | null>(null);

  const supported = baseline.probe_version === 1;
  const keyConfigured = !!baseline.api_keys_configured[name];
  const keyPersisted = !!baseline.api_keys_persisted?.[name];
  const envOverride = !!baseline.environment_overrides?.includes(name);
  const keyEnvOverride = !!baseline.api_key_environment_overrides?.includes(name);
  const preset = matchPreset(url) || presetById(name);
  const pendingKey = keyDraft.trim().length > 0;
  const needsSave = dirty || pendingKey;
  const busy = !!testing || preparing;
  const probeDisabled =
    busy || readOnly || !supported || (needsSave && blocked) || (isNew && !url.trim());
  const usage = useMemo(() => usageIndex(roles, name), [roles, name]);

  // Switching provider: drop transient state; a probe for another provider
  // must never be shown here.
  useEffect(() => {
    active.current?.controller.abort();
    active.current = null;
    setResult(null);
    setError('');
    setTesting(null);
    setPreparing(false);
    setConfirm(false);
    setTestModel('');
    setFilter('');
    setReveal(false);
    setAssigning(null);
  }, [name]);
  // Configuration reload/save: abort probes started against an older revision
  // and ignore their late results. A probe that "save and check" started for
  // the freshly saved revision keeps running.
  useEffect(() => {
    const current = active.current;
    if (current && current.revision !== baseline.revision) {
      current.controller.abort();
      active.current = null;
      setTesting(null);
      setResult(null);
      setError('');
    }
  }, [baseline.revision]);
  useEffect(
    () => () => {
      active.current?.controller.abort();
      active.current = null;
    },
    [],
  );

  const rows = useMemo(() => {
    const catalogIds = catalog?.models || [];
    const seen = new Set(catalogIds);
    const configured = [...usage.keys()].filter((id) => !seen.has(id));
    const query = filter.trim().toLowerCase();
    return [
      ...configured.map((id) => ({ id, inCatalog: false })),
      ...catalogIds.map((id) => ({ id, inCatalog: true })),
    ].filter((row) => !query || row.id.toLowerCase().includes(query));
  }, [catalog, usage, filter]);
  const testCandidates: PickerOption[] = useMemo(
    () => [
      ...(catalog?.models || []).map((id) => ({ id, source: 'catalog' as const })),
      ...[...usage.keys()]
        .filter((id) => !catalog?.models.includes(id))
        .map((id) => ({ id, source: 'configured' as const })),
    ],
    [catalog, usage],
  );

  const run = async (mode: Mode) => {
    if (probeDisabled) return;
    if (mode === 'model' && (needsSave || !testModel.trim())) return;
    let cfg: ModelConfiguration | null = baseline;
    if (needsSave) {
      setPreparing(true);
      setResult(null);
      setError('');
      try {
        cfg = await prepare();
      } finally {
        setPreparing(false);
      }
      if (!cfg?.revision) return;
    }
    const revision = cfg.revision!;
    const controller = new AbortController();
    active.current = { controller, revision };
    setTesting(mode);
    setResult(null);
    setError('');
    const timer = window.setTimeout(() => controller.abort(), 25000);
    try {
      const response = await apiClient.probeModelConfig(
        {
          provider: name,
          revision,
          mode,
          ...(mode === 'model'
            ? { model: testModel.trim(), acknowledge_cost: true }
            : {}),
        },
        controller.signal,
      );
      if (active.current?.controller !== controller || controller.signal.aborted)
        return;
      setResult(response);
      if (mode === 'connectivity' && response.ok) onCatalogFetched(response.models);
    } catch (failure) {
      if (active.current?.controller === controller)
        setError(
          (failure instanceof Error && failure.name === 'AbortError'
            ? '本地等待超时。'
            : failure instanceof Error
              ? failure.message
              : '检测请求未完成，请检查后台连接。') +
            (mode === 'model'
              ? ' 已发出的模型请求仍可能计费，请勿立即重复发送。'
              : ' 未自动重试。'),
        );
    } finally {
      window.clearTimeout(timer);
      if (active.current?.controller === controller) {
        active.current = null;
        setTesting(null);
      }
    }
  };
  const closeConfirm = () => {
    setConfirm(false);
    testTrigger.current?.focus();
  };
  const pickForTest = (id: string) => {
    setTestModel(id);
    setResult(null);
    setError('');
  };

  const connectivityLabel =
    testing === 'connectivity'
      ? '检测中…'
      : preparing
        ? '保存中…'
        : needsSave
          ? '保存并检测 / 获取模型列表'
          : '检测连通性 / 获取模型列表';

  return (
    <article className="mc-detail" aria-labelledby="mc-detail-title">
      <header className="mc-detail-header">
        <div className="mc-role-icon">
          <Server size={19} />
        </div>
        <div className="mc-detail-title">
          <h3 id="mc-detail-title">{name}</h3>
          <p>
            {preset ? preset.name : 'OpenAI 兼容端点'}
            {isNew ? ' · 新增，尚未保存' : ''}
            {envOverride ? ' · 端点由环境变量覆盖' : ''}
          </p>
        </div>
        <span className={`mc-status ${keyConfigured ? 'mc-configured' : ''}`}>
          {keyConfigured ? '密钥已注入' : '未检测到密钥'}
        </span>
        {isNew && (
          <button
            type="button"
            className="mc-icon-button"
            disabled={inUse}
            aria-label={`撤回新增 ${name}`}
            title={inUse ? '请先解除主模型和备用模型的引用' : '撤回尚未保存的提供商'}
            onClick={onWithdraw}
          >
            <Trash2 size={15} />
          </button>
        )}
      </header>

      <div className="mc-detail-grid">
        <label className="mc-field">
          <span>Base URL</span>
          <input
            aria-label={`${name} Base URL`}
            aria-invalid={!!urlError}
            aria-describedby={urlError ? `url-error-${name}` : undefined}
            value={url}
            placeholder="https://api.example.com/v1"
            autoComplete="off"
            spellCheck={false}
            onChange={(event) => onUrlChange(event.target.value)}
          />
          {urlError && (
            <span className="mc-field-error" id={`url-error-${name}`}>
              {urlError}
            </span>
          )}
          {envOverride && (
            <span className="mc-inline-warning">
              环境变量 {envUrlName(name)} 正在覆盖此地址。点底部「保存配置」会改用输入框中的地址，并停用 .env 中的对应项。
            </span>
          )}
          {preset && !url.trim() && preset.baseUrl && (
            <button
              type="button"
              className="mc-link mc-link-button"
              onClick={() => onUrlChange(preset.baseUrl)}
            >
              填入预设地址 {preset.baseUrl}
            </button>
          )}
        </label>

        <div className="mc-field mc-key-field">
          <span>
            <KeyRound size={13} /> API Key · 加密保存{' '}
            <code>{envKeyName(name)}</code>
          </span>
          <div className="mc-key-row">
            <input
              type={reveal ? 'text' : 'password'}
              aria-label={`${name} API Key`}
              placeholder={
                keyPersisted ? '已保存密钥（输入可覆盖）' : preset?.local ? '本地服务通常无需密钥' : '粘贴密钥'
              }
              value={keyDraft}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => onKeyDraftChange(event.target.value)}
            />
            <button
              type="button"
              className="mc-icon-button"
              aria-label={reveal ? '隐藏密钥' : '显示密钥'}
              aria-pressed={reveal}
              onClick={() => setReveal((state) => !state)}
            >
              {reveal ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
            <button
              type="button"
              className="mc-button mc-secondary"
              aria-label={`${name} 保存密钥`}
              disabled={keyState?.saving || isNew}
              title={isNew ? '新提供商需先保存配置；「保存并检测」会自动依次完成' : undefined}
              onClick={() => onSaveKey(keyDraft)}
            >
              {keyState?.saving ? '保存中…' : '保存'}
            </button>
            {keyPersisted && !keyDraft && (
              <button
                type="button"
                className="mc-button mc-secondary"
                aria-label={`${name} 清除密钥`}
                disabled={keyState?.saving}
                onClick={() => onSaveKey('')}
              >
                清除
              </button>
            )}
          </div>
          <p className="mc-field-note">
            仅在后台加密落盘、不回显。保存后立即用于当前会话。
            {isNew ? ' 新提供商会在「保存并检测」时先保存配置，再保存密钥。' : ''}
          </p>
          {keyEnvOverride && (
            <span className="mc-inline-warning">
              环境变量 {envKeyName(name)} 正在覆盖配置页密钥。点「保存」会改用你刚输入的密钥，并停用 .env 中的对应项。
            </span>
          )}
          {keyState?.message && (
            <p
              className={keyState.failed ? 'mc-field-error' : 'mc-key-confirm'}
              role="status"
            >
              {keyState.message}
            </p>
          )}
          {preset?.keyUrl && (
            <a
              className="mc-link"
              href={preset.keyUrl}
              target="_blank"
              rel="noreferrer noopener"
            >
              <ExternalLink size={13} /> 前往 {preset.name} 控制台获取密钥
            </a>
          )}
        </div>
      </div>

      <div className="mc-actions">
        <button
          type="button"
          className="mc-button mc-save"
          disabled={probeDisabled}
          onClick={() => void run('connectivity')}
        >
          {testing === 'connectivity' || preparing ? (
            <Loader2 size={15} className="mc-spin" />
          ) : (
            <Radio size={15} />
          )}
          {connectivityLabel}
        </button>
        <div className="mc-test-row">
          <ModelPicker
            label="待测模型 ID"
            value={testModel}
            options={testCandidates}
            disabled={busy}
            placeholder="选择或输入上游模型 ID"
            onChange={pickForTest}
          />
          <button
            type="button"
            className="mc-button mc-secondary"
            ref={testTrigger}
            disabled={probeDisabled || needsSave || !testModel.trim()}
            title={needsSave ? '请先保存配置，再测试已保存端点' : undefined}
            onClick={() => setConfirm(true)}
          >
            {testing === 'model' ? (
              <Loader2 size={15} className="mc-spin" />
            ) : (
              <FlaskConical size={15} />
            )}
            {testing === 'model' ? '模型测试中…' : '测试模型'}
          </button>
        </div>
      </div>
      {!supported && (
        <p className="mc-inline-warning">后台尚未提供检测接口，请更新后台后重新加载。</p>
      )}
      {needsSave && blocked && (
        <p className="mc-inline-warning">
          草稿中有需要修正的字段，修正后才能保存并检测。
        </p>
      )}
      <p className="mc-probe-note">
        检测使用已保存的 Base URL 与后台密钥；GET /models 不发起推理。模型测试会发送一次
        “Reply with OK.”（输出上限 16 tokens），可能计费。均不自动重试。
      </p>
      {error && (
        <div className="mc-message mc-danger" role="alert">
          <AlertCircle size={16} />
          <span>{error}</span>
        </div>
      )}
      {result && (
        <div
          className={`mc-probe-result ${result.ok ? 'mc-success' : 'mc-warning'}`}
          role="status"
        >
          <div>
            {result.ok ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
            <strong>
              {result.mode === 'model' ? '模型推理' : '端点检测'} ·{' '}
              {result.ok ? '通过' : '未通过'}
            </strong>
            <span>
              {result.http_status != null
                ? `HTTP ${result.http_status}`
                : '未取得 HTTP 响应'}{' '}
              · {result.latency_ms} ms
            </span>
          </div>
          <p>{result.message}</p>
          <small>
            {result.provider}
            {result.model ? ` / ${result.model}` : ''} · {result.code} ·{' '}
            {result.reachable ? '已收到上游 HTTP 响应' : '未确认可达'}
          </small>
        </div>
      )}

      <section className="mc-models" aria-labelledby="mc-models-title">
        <div className="mc-models-head">
          <div>
            <h4 id="mc-models-title">
              模型列表
              {catalog ? ` · 已获取 ${catalog.models.length} 个模型` : ''}
            </h4>
            <p>
              {catalog
                ? `获取于 ${formatFetchedAt(catalog.fetched_at) || '未知时间'} · 点击模型 ID 填入测试框，或直接分配给角色。`
                : '尚未获取模型列表。点击「检测连通性 / 获取模型列表」从上游拉取；也可以在角色路由中直接输入 ID。'}
            </p>
            {catalog?.stale && (
              <p className="mc-inline-warning">
                端点自上次获取后已变化，列表可能过期，建议重新获取。
              </p>
            )}
          </div>
          <div className="mc-models-tools">
            <input
              aria-label="筛选模型"
              placeholder="筛选模型 ID"
              value={filter}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setFilter(event.target.value)}
            />
            {catalog && (
              <button
                type="button"
                className="mc-button mc-ghost"
                onClick={onCatalogForget}
              >
                清除目录
              </button>
            )}
          </div>
        </div>
        {rows.length > 0 && (
          <ul className="mc-model-list">
            {rows.map((row) => {
              const usedBy = usage.get(row.id) || [];
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    className="mc-model-id"
                    title="填入待测模型"
                    disabled={busy}
                    onClick={() => pickForTest(row.id)}
                  >
                    {row.id}
                  </button>
                  <span className="mc-model-tags">
                    {!row.inCatalog && <em>不在目录</em>}
                    {usedBy.map((label) => (
                      <span className="mc-tag" key={label}>
                        {label}
                      </span>
                    ))}
                  </span>
                  <div className="mc-assign">
                    <button
                      type="button"
                      className="mc-button mc-ghost"
                      aria-haspopup="menu"
                      aria-expanded={assigning === row.id}
                      aria-label={`${row.id} 分配到角色`}
                      onClick={() =>
                        setAssigning((current) =>
                          current === row.id ? null : row.id,
                        )
                      }
                    >
                      分配 ▾
                    </button>
                    {assigning === row.id && (
                      <div
                        className="mc-menu"
                        role="menu"
                        aria-label={`${row.id} 分配到角色`}
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') setAssigning(null);
                        }}
                      >
                        {roles.map(([role, settings]) => (
                          <div className="mc-menu-group" key={role}>
                            <span>
                              {roleTitle(role)} <code>{role}</code>
                            </span>
                            {(['primary', 'fallback'] as const)
                              .filter((slot) => !!settings[slot])
                              .map((slot) => {
                                const current =
                                  settings[slot]!.provider === name &&
                                  settings[slot]!.model === row.id;
                                return (
                                  <button
                                    type="button"
                                    role="menuitem"
                                    key={slot}
                                    disabled={current}
                                    onClick={() => {
                                      onAssign(role, slot, row.id);
                                      setAssigning(null);
                                    }}
                                  >
                                    {SLOT_LABEL[slot]}
                                    {current ? '（当前）' : ''}
                                  </button>
                                );
                              })}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {rows.length === 0 && (catalog || usage.size > 0) && (
          <p className="mc-muted">没有匹配的模型。</p>
        )}
      </section>

      {confirm && (
        <ConfirmDialog
          id="mc-probe-confirm"
          icon={<FlaskConical size={24} />}
          title="发送一次模型测试？"
          confirmLabel="确认发送一次"
          cancelLabel="取消测试"
          onCancel={closeConfirm}
          onConfirm={() => {
            closeConfirm();
            void run('model');
          }}
        >
          <p className="mc-probe-target">
            {name} / {testModel.trim()}
          </p>
          <p>
            将通过已保存的端点和后台密钥发送“Reply with OK.”，输出上限 16
            tokens，不带工具或业务数据。可能产生费用；超时或关闭页面也不保证上游停止计费。
          </p>
        </ConfirmDialog>
      )}
    </article>
  );
}
