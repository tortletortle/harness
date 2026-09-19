import { Cpu, Search } from 'lucide-react';
import type { ModelRole } from '../../types/modelConfig';
import { ModelPicker, type PickerOption } from './ModelPicker';
import { SLOT_LABEL, roleDescription, roleTitle, type Slot } from './shared';

/**
 * Compact per-role routing table. Wide containers render one row per role;
 * narrow containers fall back to stacked cards via CSS container queries.
 */
export function RoleRoutingTable({
  roles,
  totalRoles,
  baselineModels,
  providers,
  validation,
  candidates,
  onChange,
}: {
  roles: [string, ModelRole][];
  totalRoles: number;
  baselineModels: Record<string, ModelRole> | undefined;
  providers: string[];
  validation: Record<string, string>;
  candidates: (provider: string) => PickerOption[];
  onChange: (
    role: string,
    slot: Slot,
    field: 'provider' | 'model',
    value: string,
  ) => void;
}) {
  const slotEditor = (name: string, role: ModelRole, slot: Slot) => {
    const choice = role[slot];
    if (!choice) {
      return (
        <span className="mc-muted mc-no-fallback">
          未定义备用模型（可在 models.yaml 中添加）
        </span>
      );
    }
    const label = `${name} ${SLOT_LABEL[slot]}`;
    const error = validation[`${name}:${slot}`];
    const errorId = `error-${name}-${slot}`;
    return (
      <div className="mc-slot">
        <div className="mc-slot-fields">
          <select
            aria-label={`${label}提供商`}
            value={choice.provider}
            onChange={(event) =>
              onChange(name, slot, 'provider', event.target.value)
            }
          >
            <option value="">选择提供商</option>
            {!providers.includes(choice.provider) && choice.provider && (
              <option value={choice.provider}>
                {choice.provider}（端点未配置）
              </option>
            )}
            {providers.map((provider) => (
              <option key={provider} value={provider}>
                {provider}
              </option>
            ))}
          </select>
          <ModelPicker
            label={`${label} ID`}
            value={choice.model}
            options={candidates(choice.provider)}
            invalid={!!error}
            describedBy={error ? errorId : undefined}
            onChange={(value) => onChange(name, slot, 'model', value)}
          />
        </div>
        {error && (
          <p className="mc-field-error" id={errorId}>
            {error}
          </p>
        )}
      </div>
    );
  };

  return (
    <section className="mc-routing" aria-labelledby="mc-routing-title">
      <div className="mc-section-heading">
        <div>
          <h3 id="mc-routing-title">
            <Cpu size={18} /> 角色路由
          </h3>
          <p>
            为每个角色选择提供商与模型。候选来自已获取的模型目录和现有配置，也可直接输入任意
            ID。
          </p>
        </div>
        <span className="mc-hint">{roles.length} 个角色</span>
      </div>
      {roles.length === 0 ? (
        <div className="mc-empty">
          <Search size={25} />
          <h3>{totalRoles ? '没有匹配的模型角色' : '尚未定义模型角色'}</h3>
          <p>
            {totalRoles
              ? '试试角色名称、提供商或模型 ID。'
              : '在 models.yaml 中定义角色后重新加载，不会自动生成虚假的默认角色。'}
          </p>
        </div>
      ) : (
        <table className="mc-table">
          <thead>
            <tr>
              <th scope="col">角色</th>
              <th scope="col">主模型</th>
              <th scope="col">备用模型</th>
              <th scope="col">参数</th>
            </tr>
          </thead>
          <tbody>
            {roles.map(([name, role]) => {
              const changed =
                JSON.stringify(role) !== JSON.stringify(baselineModels?.[name]);
              return (
                <tr key={name} className={changed ? 'mc-changed' : undefined}>
                  <th scope="row" className="mc-cell-role">
                    <div>
                      <strong>{roleTitle(name)}</strong>
                      {changed && (
                        <span className="mc-draft-dot" title="未保存修改" />
                      )}
                    </div>
                    <span className="mc-mono">{name}</span>
                    <small>{roleDescription(name)}</small>
                  </th>
                  <td data-label="主模型">{slotEditor(name, role, 'primary')}</td>
                  <td data-label="备用模型">
                    {slotEditor(name, role, 'fallback')}
                  </td>
                  <td data-label="参数" className="mc-cell-params">
                    {role.primary.temperature != null && (
                      <span>温度 {role.primary.temperature}</span>
                    )}
                    {role.primary.max_tokens != null && (
                      <span>{role.primary.max_tokens.toLocaleString()} tokens</span>
                    )}
                    {role.primary.temperature == null &&
                      role.primary.max_tokens == null && (
                        <span className="mc-muted">沿用文件默认</span>
                      )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
