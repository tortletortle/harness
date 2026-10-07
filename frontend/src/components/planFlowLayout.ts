/**
 * 计划步骤 → 流程图布局（供 PlanFlowGraph / 测试复用）。
 */
import type { Step, StepStatus } from '../types';

export type FlowNodeData = {
  stepId: string;
  label: string;
  tool: string;
  status: StepStatus;
  order?: number;
};

export type FlowLayoutNode = {
  id: string;
  position: { x: number; y: number };
  data: FlowNodeData;
};

export type FlowLayoutEdge = {
  id: string;
  source: string;
  target: string;
};

const NODE_W = 220;
const NODE_H = 88;
const GAP_X = 48;
const GAP_Y = 72;

/** 计算每个步骤的拓扑层级（依赖链长度）。 */
export function computeLevels(steps: Step[]): Map<string, number> {
  const byId = new Map(steps.map((step) => [step.step_id, step]));
  const levels = new Map<string, number>();
  const visiting = new Set<string>();

  const levelOf = (step: Step): number => {
    if (levels.has(step.step_id)) return levels.get(step.step_id)!;
    if (visiting.has(step.step_id)) return 0;
    visiting.add(step.step_id);
    const deps = step.depends_on || [];
    let level = 0;
    for (const dep of deps) {
      const depStep = byId.get(dep);
      if (depStep) level = Math.max(level, levelOf(depStep) + 1);
    }
    visiting.delete(step.step_id);
    levels.set(step.step_id, level);
    return level;
  };

  steps.forEach(levelOf);
  return levels;
}

/**
 * 有 depends_on 时按层级排；否则按 order 排成一条链。
 */
export function stepsToFlowLayout(steps: Step[]): {
  nodes: FlowLayoutNode[];
  edges: FlowLayoutEdge[];
} {
  const ordered = [...steps].sort(
    (a, b) => (a.order ?? 0) - (b.order ?? 0) || a.step_id.localeCompare(b.step_id),
  );
  const hasDeps = ordered.some((s) => (s.depends_on || []).length > 0);
  const levels = hasDeps ? computeLevels(ordered) : new Map<string, number>();

  if (!hasDeps) {
    ordered.forEach((s, i) => levels.set(s.step_id, i));
  }

  const buckets = new Map<number, Step[]>();
  for (const step of ordered) {
    const level = levels.get(step.step_id) ?? 0;
    const list = buckets.get(level) ?? [];
    list.push(step);
    buckets.set(level, list);
  }

  const nodes: FlowLayoutNode[] = [];
  for (const [level, bucket] of [...buckets.entries()].sort((a, b) => a[0] - b[0])) {
    const width = bucket.length * NODE_W + Math.max(0, bucket.length - 1) * GAP_X;
    const startX = -width / 2;
    bucket.forEach((step, index) => {
      nodes.push({
        id: step.step_id,
        position: {
          x: startX + index * (NODE_W + GAP_X),
          y: level * (NODE_H + GAP_Y),
        },
        data: {
          stepId: step.step_id,
          label: step.description || step.tool || step.step_id,
          tool: step.tool,
          status: (step.status ?? 'Pending') as StepStatus,
          order: step.order,
        },
      });
    });
  }

  const idSet = new Set(ordered.map((s) => s.step_id));
  const edges: FlowLayoutEdge[] = [];

  if (hasDeps) {
    for (const step of ordered) {
      for (const dep of step.depends_on || []) {
        if (!idSet.has(dep)) continue;
        edges.push({
          id: `${dep}->${step.step_id}`,
          source: dep,
          target: step.step_id,
        });
      }
    }
  } else {
    for (let i = 1; i < ordered.length; i += 1) {
      const prev = ordered[i - 1];
      const cur = ordered[i];
      edges.push({
        id: `${prev.step_id}->${cur.step_id}`,
        source: prev.step_id,
        target: cur.step_id,
      });
    }
  }

  return { nodes, edges };
}

export const FLOW_NODE_SIZE = { width: NODE_W, height: NODE_H };
