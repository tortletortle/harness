import { useEffect, useRef, useState } from 'react';
import { ExternalLink, Plus, Server, X } from 'lucide-react';
import {
  CUSTOM_PRESET,
  PROVIDER_PRESETS,
  uniqueProviderId,
  type ProviderPreset,
} from './providerPresets';
import { PROVIDER_ID, trapFocus, validURL } from './shared';

/**
 * Preset picker + tiny form. Adding only creates a draft entry; nothing is
 * saved or contacted until the user saves the configuration.
 */
export function AddProviderDialog({
  existing,
  onAdd,
  onClose,
}: {
  existing: string[];
  onAdd: (id: string, url: string) => void;
  onClose: () => void;
}) {
  const [preset, setPreset] = useState<ProviderPreset>(CUSTOM_PRESET);
  const [id, setId] = useState('');
  const [url, setUrl] = useState('');
  const [error, setError] = useState('');
  const idField = useRef<HTMLInputElement>(null);

  useEffect(() => {
    idField.current?.focus();
  }, []);

  const pick = (next: ProviderPreset) => {
    setPreset(next);
    setUrl(next.baseUrl);
    setId(next === CUSTOM_PRESET ? '' : uniqueProviderId(next.id, existing));
    setError('');
  };
  const submit = () => {
    const name = id.trim();
    if (!PROVIDER_ID.test(name)) {
      setError('使用字母开头的标识，可含数字、下划线或短横线（最多 64 位）。');
      return;
    }
    if (existing.some((item) => item.toLowerCase() === name.toLowerCase())) {
      setError('这个提供商已存在，请使用不同的标识。');
      return;
    }
    if (url.trim() && !validURL(url)) {
      setError('Base URL 需要是完整 HTTP(S) 地址，不含密钥、查询参数或片段。');
      return;
    }
    onAdd(name, url.trim());
  };

  return (
    <div className="mc-dialog-backdrop">
      <div
        className="mc-dialog mc-add-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mc-add-title"
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onClose();
            return;
          }
          trapFocus(event);
        }}
      >
        <div className="mc-add-head">
          <div>
            <h3 id="mc-add-title">
              <Server size={18} /> 添加服务提供商
            </h3>
            <p>选择预设自动填入地址，或自定义任意 OpenAI 兼容端点。添加后仍需保存配置。</p>
          </div>
          <button
            type="button"
            className="mc-icon-button"
            aria-label="关闭"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>
        <div className="mc-add-body">
          <div className="mc-presets" role="group" aria-label="预设提供商">
            {[...PROVIDER_PRESETS, CUSTOM_PRESET].map((item) => (
              <button
                type="button"
                key={item.id}
                className={`mc-preset ${preset.id === item.id ? 'mc-selected' : ''}`}
                aria-pressed={preset.id === item.id}
                onClick={() => pick(item)}
              >
                <strong>{item.name}</strong>
                <span>
                  {item.local
                    ? '本地 · 无需密钥'
                    : item.hosts[0] || '手动填写地址'}
                </span>
              </button>
            ))}
          </div>
          <div className="mc-add-form">
            <label className="mc-field">
              <span>标识</span>
              <input
                ref={idField}
                aria-label="新提供商标识"
                placeholder="例如 openai 或 local_llm"
                value={id}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => {
                  setId(event.target.value);
                  setError('');
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    submit();
                  }
                }}
              />
            </label>
            <label className="mc-field">
              <span>Base URL</span>
              <input
                aria-label="新提供商 Base URL"
                placeholder="https://api.example.com/v1"
                value={url}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => {
                  setUrl(event.target.value);
                  setError('');
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    submit();
                  }
                }}
              />
            </label>
            {preset.note && <p className="mc-field-note">{preset.note}</p>}
            {preset.keyUrl && (
              <a
                className="mc-link"
                href={preset.keyUrl}
                target="_blank"
                rel="noreferrer noopener"
              >
                <ExternalLink size={13} /> 前往 {preset.name} 控制台创建密钥
              </a>
            )}
            {error && (
              <p className="mc-field-error" role="alert">
                {error}
              </p>
            )}
            <div className="mc-dialog-actions">
              <button
                type="button"
                className="mc-button mc-secondary"
                onClick={onClose}
              >
                取消
              </button>
              <button
                type="button"
                className="mc-button mc-save"
                onClick={submit}
                disabled={!id.trim()}
              >
                <Plus size={15} /> 添加
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
