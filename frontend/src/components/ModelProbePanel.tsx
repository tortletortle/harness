import { useEffect, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  FlaskConical,
  Loader2,
  Radio,
} from 'lucide-react';
import { apiClient } from '../api/client';
import type {
  ModelConfiguration,
  ModelProbeResult,
} from '../types/modelConfig';

export function ModelProbePanel({
  config,
  dirty,
}: {
  config: ModelConfiguration;
  dirty: boolean;
}) {
  const names = Object.keys(config.base_urls);
  const [provider, setProvider] = useState(names[0] || '');
  const [model, setModel] = useState('');
  const [catalog, setCatalog] = useState<string[]>([]);
  const [result, setResult] = useState<ModelProbeResult | null>(null);
  const [error, setError] = useState('');
  const [testing, setTesting] = useState<'connectivity' | 'model' | null>(null);
  const [confirm, setConfirm] = useState(false);
  const active = useRef<AbortController | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const supported = config.probe_version === 1;
  const disabled = !!testing || dirty || !supported || !provider;
  useEffect(() => {
    active.current?.abort();
    active.current = null;
    setResult(null);
    setCatalog([]);
    setError('');
    setTesting(null);
    setConfirm(false);
    return () => {
      active.current?.abort();
      active.current = null;
    };
  }, [config.revision]);
  const savedModels = Object.values(config.models || {})
    .flatMap((role) => [role.primary, role.fallback])
    .filter((choice) => choice?.provider === provider)
    .map((choice) => choice!.model);
  const candidates = [...new Set([...catalog, ...savedModels])];
  const run = async (mode: 'connectivity' | 'model') => {
    if (disabled || (mode === 'model' && !model.trim())) return;
    const controller = new AbortController();
    active.current = controller;
    setTesting(mode);
    setResult(null);
    setError('');
    const timer = window.setTimeout(() => controller.abort(), 25000);
    try {
      const response = await apiClient.probeModelConfig(
        {
          provider,
          revision: config.revision!,
          mode,
          ...(mode === 'model'
            ? { model: model.trim(), acknowledge_cost: true }
            : {}),
        },
        controller.signal,
      );
      if (active.current !== controller || controller.signal.aborted) return;
      setResult(response);
      if (mode === 'connectivity' && response.ok) setCatalog(response.models);
    } catch (error) {
      if (active.current === controller)
        setError(
          (error instanceof Error && error.name === 'AbortError'
            ? '本地等待超时。'
            : error instanceof Error
              ? error.message
              : '检测请求未完成，请检查后台连接。') +
            (mode === 'model'
              ? ' 已发出的模型请求仍可能计费，请勿立即重复发送。'
              : ' 未自动重试。'),
        );
    } finally {
      window.clearTimeout(timer);
      if (active.current === controller) {
        active.current = null;
        setTesting(null);
      }
    }
  };
  const close = () => {
    setConfirm(false);
    trigger.current?.focus();
  };

  return (
    <section className="mc-probe" aria-labelledby="mc-probe-title">
      <div className="mc-probe-heading">
        <div>
          <h3 id="mc-probe-title">
            <Activity size={18} />
            上游检测
          </h3>
          <p>连通性与模型推理分开验证，结果不会自动改写配置。</p>
        </div>
        <span className="mc-hint">OpenAI 兼容协议</span>
      </div>
      {!supported && (
        <p className="mc-inline-warning">
          后台尚未提供检测接口，请更新后台后重新加载。
        </p>
      )}
      {dirty && (
        <p className="mc-inline-warning">
          请先保存或放弃配置草稿，再检测已保存的端点。
        </p>
      )}
      <div className="mc-probe-controls">
        <label>
          <span>检测提供商</span>
          <select
            aria-label="检测提供商"
            value={provider}
            disabled={!!testing}
            onChange={(e) => {
              setProvider(e.target.value);
              setModel('');
              setCatalog([]);
              setResult(null);
              setError('');
            }}
          >
            {names.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>待测模型 ID</span>
          <input
            aria-label="待测模型 ID"
            disabled={!!testing}
            value={model}
            onChange={(e) => {
              setModel(e.target.value);
              setResult(null);
              setError('');
            }}
            placeholder="选择或输入上游模型 ID"
            list="mc-probe-models"
            maxLength={256}
            autoComplete="off"
            spellCheck={false}
          />
          <datalist id="mc-probe-models">
            {candidates.map((id) => (
              <option key={id} value={id} />
            ))}
          </datalist>
        </label>
        <button
          className="mc-button mc-secondary"
          disabled={disabled}
          onClick={() => void run('connectivity')}
        >
          {testing === 'connectivity' ? (
            <Loader2 size={15} className="mc-spin" />
          ) : (
            <Radio size={15} />
          )}
          {testing === 'connectivity' ? '检测中…' : '检测连通性 / 获取模型'}
        </button>
        <button
          className="mc-button mc-save"
          disabled={disabled || !model.trim()}
          ref={trigger}
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
      <p className="mc-probe-note">
        检测使用已保存 Base URL
        {config.environment_overrides?.includes(provider)
          ? '（此提供商由环境变量覆盖）'
          : ''}
        与后台密钥。GET /models
        不发起推理；模型测试会发送一次最小文本请求，可能计费。均不自动重试。
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
      {catalog.length > 0 && (
        <details className="mc-probe-catalog">
          <summary>
            已获取 {catalog.length} 个模型 · 展开选择（不自动测试）
          </summary>
          <div>
            {catalog.map((id) => (
              <button
                key={id}
                disabled={!!testing}
                onClick={() => {
                  setModel(id);
                  setResult(null);
                }}
              >
                {id}
              </button>
            ))}
          </div>
        </details>
      )}
      <p className="mc-probe-note">
        文本测试不验证视觉、语音、结构化输出或工具调用能力；/models
        不支持也不能直接判定模型不可用。
      </p>
      {confirm && (
        <div className="mc-dialog-backdrop">
          <div
            className="mc-dialog"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="mc-probe-confirm"
            aria-describedby="mc-probe-cost"
            onKeyDown={(event) => {
              if (event.key === 'Escape') close();
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
          >
            <FlaskConical size={24} />
            <h3 id="mc-probe-confirm">发送一次模型测试？</h3>
            <p className="mc-probe-target">
              {provider} / {model.trim()}
            </p>
            <p id="mc-probe-cost">
              将通过已保存的端点和后台密钥发送“Reply with OK.”，输出上限 16
              tokens，不带工具或业务数据。可能产生费用；超时或关闭页面也不保证上游停止计费。
            </p>
            <div>
              <button
                className="mc-button mc-secondary"
                autoFocus
                onClick={close}
              >
                取消测试
              </button>
              <button
                className="mc-button mc-save"
                onClick={() => {
                  close();
                  void run('model');
                }}
              >
                确认发送一次
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
