/**
 * ToastHub / appStore Toast 行为测试
 *
 * 使用真实 zustand store（通过 addLog / setState 注入状态），
 * 不 mock api：Toast 只依赖 store，无需网络。
 */

import { render, screen, fireEvent, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastHub } from './ToastHub';
import { useAppStore } from '../stores/appStore';
import type { LogMessage } from '../types';

function log(level: LogMessage['level'], message: string): LogMessage {
  return { type: 'log', level, message };
}

beforeEach(() => {
  useAppStore.setState({ toasts: [], logs: [] });
});

describe('ToastHub 基本渲染与关闭', () => {
  it('addLog error 后渲染 toast，点击关闭后移除且 store 更新', () => {
    useAppStore.getState().addLog(log('error', '测试错误'));
    render(<ToastHub />);

    expect(screen.getByText('测试错误')).toBeInTheDocument();
    expect(useAppStore.getState().toasts).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: '关闭' }));

    expect(screen.queryByText('测试错误')).not.toBeInTheDocument();
    expect(useAppStore.getState().toasts).toHaveLength(0);
  });

  it('warning 日志同样浮出 toast', () => {
    useAppStore.getState().addLog(log('warning', '配置已保存，但自动重启失败'));
    render(<ToastHub />);

    expect(screen.getByText('配置已保存，但自动重启失败')).toBeInTheDocument();
  });
});

describe('ToastHub 自动消失', () => {
  it('5 秒后自动消失', () => {
    vi.useFakeTimers();
    try {
      useAppStore.getState().addLog(log('error', '自动消失'));
      render(<ToastHub />);
      expect(screen.getByText('自动消失')).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(5000);
      });

      expect(screen.queryByText('自动消失')).not.toBeInTheDocument();
      expect(useAppStore.getState().toasts).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('appStore toast 上限与去重', () => {
  it('最多保留 5 条 toast，最旧的被丢弃', () => {
    for (let i = 1; i <= 7; i++) {
      useAppStore.getState().addLog(log('error', `错误 ${i}`));
    }
    render(<ToastHub />);

    const toasts = useAppStore.getState().toasts;
    expect(toasts).toHaveLength(5);
    expect(toasts[0].message).toBe('错误 3');
    expect(toasts[4].message).toBe('错误 7');
    expect(screen.queryByText('错误 1')).not.toBeInTheDocument();
    expect(screen.queryByText('错误 2')).not.toBeInTheDocument();
    expect(screen.getByText('错误 7')).toBeInTheDocument();
  });

  it('连续相同错误消息去重：只保留一条并刷新时间戳', () => {
    useAppStore.getState().addLog(log('error', '重复错误'));
    const first = useAppStore.getState().toasts[0];

    useAppStore.getState().addLog(log('error', '重复错误'));

    const toasts = useAppStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].id).toBe(first.id);
    expect(toasts[0].timestamp >= first.timestamp).toBe(true);
    // 日志仍逐条记录
    expect(useAppStore.getState().logs).toHaveLength(2);
  });

  it('info 日志不产生 toast，但日志本身照常写入', () => {
    useAppStore.getState().addLog(log('info', '普通信息'));
    render(<ToastHub />);

    expect(screen.queryByText('普通信息')).not.toBeInTheDocument();
    expect(useAppStore.getState().toasts).toHaveLength(0);
    expect(useAppStore.getState().logs).toHaveLength(1);
  });
});
