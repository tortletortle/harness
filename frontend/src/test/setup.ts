/**
 * Vitest 全局测试环境配置
 */
import '@testing-library/jest-dom/vitest';

// @xyflow/react 在 jsdom 里需要 ResizeObserver
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
