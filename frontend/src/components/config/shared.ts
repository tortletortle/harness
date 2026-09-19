import type { KeyboardEvent } from 'react';
import type { ModelConfiguration, ModelRole } from '../../types/modelConfig';

export type Slot = 'primary' | 'fallback';

export const ROLE_NAMES: Record<string, [string, string]> = {
  designer: ['设计规划', '拆解需求，生成设计与执行计划'],
  executor: ['任务执行', '执行步骤，调用工具并校验结果'],
  toolmanager: ['工具管理', '选择工具与构建调用参数'],
  knowledge: ['知识检索', '理解上下文，检索与整理知识'],
  vision: ['视觉理解', '识别图片与界面中的信息'],
  codegen: ['代码生成', '编写、修改与分析代码'],
};
export const SLOT_LABEL: Record<Slot, string> = {
  primary: '主模型',
  fallback: '备用模型',
};
export const roleTitle = (role: string) => ROLE_NAMES[role]?.[0] || role;
export const roleDescription = (role: string) =>
  ROLE_NAMES[role]?.[1] || '专项能力模型 · 沿用配置文件中的角色定义';

/** New provider IDs stay ASCII; saved Unicode IDs are preserved by the backend. */
export const PROVIDER_ID = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;

export type Draft = {
  base_urls: Record<string, string>;
  models: Record<string, ModelRole>;
};
export const toDraft = (cfg: ModelConfiguration): Draft => ({
  base_urls: { ...cfg.base_urls },
  models: structuredClone(cfg.models || cfg.agents || {}),
});
export const sameDraft = (a: Draft | null, b: Draft | null) =>
  JSON.stringify(a) === JSON.stringify(b);

export const validURL = (value: string) => {
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

export const envKeyName = (provider: string) =>
  `EAS_LLM_API_KEY_${provider.toUpperCase()}`;
export const envUrlName = (provider: string) =>
  `EAS_LLM_BASE_URL_${provider.toUpperCase()}`;

/** modelId -> ["designer 主模型", ...] for one provider. */
export const usageIndex = (roles: [string, ModelRole][], provider: string) => {
  const map = new Map<string, string[]>();
  roles.forEach(([role, settings]) => {
    (['primary', 'fallback'] as const).forEach((slot) => {
      const choice = settings[slot];
      if (choice && choice.provider === provider && choice.model.trim()) {
        const list = map.get(choice.model) || [];
        list.push(`${role} ${SLOT_LABEL[slot]}`);
        map.set(choice.model, list);
      }
    });
  });
  return map;
};

export const formatFetchedAt = (iso: string | null | undefined) => {
  if (!iso) return '';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
};

/** Keyboard focus trap for the small in-page dialogs. */
export const trapFocus = (event: KeyboardEvent<HTMLElement>) => {
  if (event.key !== 'Tab') return;
  const focusable = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href]',
    ),
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
};
