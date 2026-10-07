import { describe, expect, it } from 'vitest';
import { computeLevels, stepsToFlowLayout } from './planFlowLayout';
import type { Step } from '../types';

function step(partial: Partial<Step> & Pick<Step, 'step_id' | 'description' | 'tool'>): Step {
  return {
    inputs: {},
    expected_outputs: [],
    order: 0,
    ...partial,
  };
}

describe('planFlowLayout', () => {
  it('computeLevels ranks by depends_on depth', () => {
    const steps = [
      step({ step_id: 'a', description: 'A', tool: 'file_creator', order: 1 }),
      step({ step_id: 'b', description: 'B', tool: 'test_runner', order: 2, depends_on: ['a'] }),
      step({ step_id: 'c', description: 'C', tool: 'lint_gate', order: 3, depends_on: ['a'] }),
    ];
    const levels = computeLevels(steps);
    expect(levels.get('a')).toBe(0);
    expect(levels.get('b')).toBe(1);
    expect(levels.get('c')).toBe(1);
  });

  it('stepsToFlowLayout builds edges from depends_on', () => {
    const steps = [
      step({ step_id: 'a', description: 'A', tool: 'file_creator', order: 1 }),
      step({ step_id: 'b', description: 'B', tool: 'test_runner', order: 2, depends_on: ['a'] }),
    ];
    const { nodes, edges } = stepsToFlowLayout(steps);
    expect(nodes.map((n) => n.id).sort()).toEqual(['a', 'b']);
    expect(edges).toEqual([{ id: 'a->b', source: 'a', target: 'b' }]);
    expect(nodes.find((n) => n.id === 'b')!.position.y).toBeGreaterThan(
      nodes.find((n) => n.id === 'a')!.position.y,
    );
  });

  it('without depends_on lays out a linear chain', () => {
    const steps = [
      step({ step_id: 'a', description: 'A', tool: 'file_creator', order: 1 }),
      step({ step_id: 'b', description: 'B', tool: 'test_runner', order: 2 }),
    ];
    const { edges } = stepsToFlowLayout(steps);
    expect(edges).toEqual([{ id: 'a->b', source: 'a', target: 'b' }]);
  });
});
