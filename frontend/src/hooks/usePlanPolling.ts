/**
 * usePlanPolling
 *
 * 执行计划轮询 Hook：
 * - 计划状态为 Running / Pending（或 isExecuting，刚点击"开始执行"的竞态窗口）时，
 *   每 2s 从后端拉取最新计划并同步到 store，PlanView 由此获得权威的步骤状态。
 * - 进入终态（Completed/Failed/Paused/RolledBack）后停止轮询并复位 isExecuting。
 * - 挂载时调用 GET /api/status，若 current_execution 等于当前计划 ID，
 *   立即开始轮询（页面刷新后重新附着到执行中的计划）。
 * - 轮询错误吞掉并写一条 warning 日志（每次失败突发只记一条，避免刷屏）。
 */

import { useEffect, useRef } from 'react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';

const POLL_INTERVAL_MS = 5000;
/** plan_update 推送到达后的去抖窗口：合并同一次状态变更引发的事件突发 */
const EVENT_DEBOUNCE_MS = 300;

/** 终态集合：轮询到这些状态即停止 */
const TERMINAL_STATUSES = ['Completed', 'Failed', 'Paused', 'RolledBack'];

export function usePlanPolling(): void {
  const planId = useAppStore((s) => s.currentPlan?.plan_id ?? null);
  const planStatus = useAppStore((s) => s.currentPlan?.status);
  const isExecuting = useAppStore((s) => s.isExecuting);
  const planEvent = useAppStore((s) => s.planEventTick);
  const initialLoadedRef = useRef<string | null>(null);
  // 事件触发的立即刷新复用主 effect 里的 tick（保持单一状态应用路径）
  const tickRef = useRef<() => void>(() => {});

  useEffect(() => {
    if (!planId) return;

    let inFlight = false; // 防止请求重叠
    let failing = false; // 失败突发节流标记
    let timer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    const stopPolling = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };

    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const latest = await apiClient.getPlan(planId);
        if (cancelled) return;

        const store = useAppStore.getState();
        // 请求期间用户可能已切换到其他计划
        if (store.currentPlan?.plan_id !== planId) return;

        store.setCurrentPlan(latest);

        const status = latest.status;
        const terminal = TERMINAL_STATUSES.includes(status ?? '');
        if (terminal) {
          store.setIsExecuting(false);
          stopPolling();
        } else if (status === 'Running') {
          store.setIsExecuting(true);
        }
        // Pending 时不主动改 isExecuting，等待状态推进到 Running/终态

        failing = false;
      } catch (error) {
        if (!failing && !cancelled) {
          failing = true;
          useAppStore.getState().addLog({
            type: 'log',
            level: 'warning',
            message: `刷新计划状态失败: ${error instanceof Error ? error.message : String(error)}`,
          });
        }
      } finally {
        inFlight = false;
      }
    };

    const startPolling = () => {
      if (timer !== null) return;
      timer = setInterval(() => {
        void tick();
      }, POLL_INTERVAL_MS);
    };
    // 供 plan_update 事件触发的立即刷新使用
    tickRef.current = tick;

    const shouldPoll = planStatus === 'Running' || planStatus === 'Pending' || isExecuting;

    // 首次挂载/切换计划时无条件拉一次全量数据：
    // 历史列表接口返回的壳计划（segments 为空、无 status）由此补全。
    // StrictMode 双挂载时第一次的请求会被 cleanup 取消，
    // 因此 cleanup 里要回滚标记，让第二次运行重新拉取。
    let initiatedInitialFetch = false;
    if (initialLoadedRef.current !== planId) {
      initialLoadedRef.current = planId;
      initiatedInitialFetch = true;
      void tick();
    }

    if (shouldPoll) {
      startPolling();
    } else {
      // 非执行状态：检查后端是否有正在执行的该计划（页面刷新后重新附着）
      void (async () => {
        try {
          const serviceStatus = await apiClient.getServiceStatus();
          if (cancelled) return;
          if (serviceStatus.current_execution === planId) {
            const store = useAppStore.getState();
            if (store.currentPlan?.plan_id === planId) {
              store.setIsExecuting(true);
              startPolling();
              void tick();
            }
          }
        } catch {
          // 服务状态接口不可用时静默忽略
        }
      })();
    }

    return () => {
      cancelled = true;
      stopPolling();
      if (initiatedInitialFetch && initialLoadedRef.current === planId) {
        initialLoadedRef.current = null;
      }
    };
  }, [planId, planStatus, isExecuting]);

  // plan_update 推送到达（后端每个状态转换后广播）→ 去抖后立即全量刷新。
  // 30s 兜底轮询之外的真实时路径；事件不匹配当前计划时忽略。
  useEffect(() => {
    if (!planId || planEvent.planId !== planId) return;
    const timer = setTimeout(() => tickRef.current(), EVENT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [planEvent, planId]);
}
