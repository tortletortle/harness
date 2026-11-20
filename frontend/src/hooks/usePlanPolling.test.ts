import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { ExecutionPlan } from '../types';
import { usePlanPolling } from './usePlanPolling';

const paused: ExecutionPlan = { plan_id: 'a', design_id: 'd', segments: [], status: 'Paused' };
const latest = { ...paused, design_id: 'new' };
let resolveOld: (value: ExecutionPlan) => void;
let rejectOld: (error: Error) => void;

beforeEach(() => {
  vi.useFakeTimers();
  useAppStore.setState({ currentPlan: paused, isExecuting: false });
  vi.spyOn(apiClient, 'getPlan').mockResolvedValue(latest).mockImplementationOnce(
    () => new Promise((resolve, reject) => { resolveOld = resolve; rejectOld = reject; })
  );
  vi.spyOn(apiClient, 'getServiceStatus').mockResolvedValue({
    status: 'running', current_execution: null, llm_providers: [], tools_count: 0,
  });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

async function push(id: string) {
  act(() => useAppStore.getState().notifyPlanEvent(id));
  await act(() => vi.advanceTimersByTimeAsync(300));
}

it.each(['success', 'failure'])('coalesces pushes during a pending %s into one follow-up', async outcome => {
  renderHook(usePlanPolling);
  await push('a');
  await push('a');
  expect(apiClient.getPlan).toHaveBeenCalledTimes(1);
  await act(async () => {
    if (outcome === 'success') resolveOld(paused);
    else rejectOld(new Error('offline'));
  });
  expect(apiClient.getPlan).toHaveBeenCalledTimes(2);
  expect(useAppStore.getState().currentPlan).toEqual(latest);
});

it('ignores other plans without cancelling a matching event in the debounce window', async () => {
  renderHook(usePlanPolling);
  await act(async () => resolveOld(paused));
  act(() => useAppStore.getState().notifyPlanEvent('a'));
  await push('b');
  expect(apiClient.getPlan).toHaveBeenCalledTimes(2);
});

it.each(['switch', 'unmount'])('discards a pending refresh on %s', async mode => {
  const hook = renderHook(usePlanPolling);
  await push('a');
  if (mode === 'unmount') hook.unmount();
  else {
    const next = { ...paused, plan_id: 'b' };
    vi.mocked(apiClient.getPlan).mockResolvedValue(next);
    await act(async () => useAppStore.getState().setCurrentPlan(next));
  }
  await act(async () => resolveOld(paused));
  expect(vi.mocked(apiClient.getPlan).mock.calls.filter(([id]) => id === 'a')).toHaveLength(1);
  if (mode === 'switch') expect(useAppStore.getState().currentPlan?.plan_id).toBe('b');
});
