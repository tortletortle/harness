import { defineConfig, devices } from '@playwright/test';

// 浏览器级 UI E2E：对 vite dev server（:3000）做冒烟。
// 前置：后端已在本机 :8000 起（vite 把 /api、/ws 代理到 8000），
//       且 frontend/.env.local 的 VITE_API_TOKEN 与后端 token 一致。
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
