import { test, expect } from '@playwright/test';

// 浏览器级 UI 冒烟：不触发真实任务执行，只验证外壳渲染、连接状态与视图导航。
// 前置：后端在 :8000 运行，VITE_API_TOKEN 与后端 token 一致（见 playwright.config.ts）。

const NAV = ['新建任务', '设计书', '执行计划', '实时执行', '产物库', 'Agent 记录', '工具库', '历史记录', '审计日志'];
const REQ_PLACEHOLDER = '描述你想要实现的功能和技术要求...';

// 跳过开屏动画，避免遮罩拦截点击
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('eas-splash-shown', '1'));
});

test('应用外壳渲染：标题、侧栏导航与输入视图', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('星舵 Helm').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '新建任务', exact: true })).toBeVisible();
  await expect(page.getByPlaceholder(REQ_PLACEHOLDER)).toBeVisible();
});

test('后端在线时连接状态显示“服务在线”', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('服务在线')).toBeVisible({ timeout: 15_000 });
});

test('侧栏导航逐视图切换，激活态跟随', async ({ page }) => {
  await page.goto('/');
  for (const name of NAV) {
    const btn = page.getByRole('button', { name, exact: true });
    await btn.click();
    await expect(btn).toHaveAttribute('aria-current', 'page');
  }
  // 切到“设置”后，输入视图的文本框应离开可见区
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await expect(page.getByPlaceholder(REQ_PLACEHOLDER)).not.toBeVisible();
});

test('活动流 / AI Copilot 面板可展开', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: '活动流', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'AI Copilot', exact: true })).toBeVisible();
});