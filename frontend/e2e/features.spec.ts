import { test, expect, type Page } from '@playwright/test';

// 功能冒烟：逐页打开、点一遍新增/修复的按钮。
// 只做可逆操作：重命名（最后改回）、归档后取消归档、删除只点到确认框再取消。
test.describe.configure({ mode: 'serial' });

const SHOTS = 'e2e-shots';
const NAV = ['新建任务', '设计书', '执行计划', '实时执行', '产物库', 'Agent 记录', '工具库', '历史记录', '审计日志', '系统概览', '设置'];

type Problems = { pageErrors: string[]; consoleErrors: string[]; apiErrors: string[] };

function watch(page: Page): Problems {
  const p: Problems = { pageErrors: [], consoleErrors: [], apiErrors: [] };
  page.on('pageerror', (e) => p.pageErrors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') p.consoleErrors.push(m.text()); });
  page.on('response', (r) => {
    const u = r.url();
    // 外部引用产物读内容返回 404 属于预期（前端会展示文件位置）
    if (u.includes('/api/') && r.status() >= 400 && !(r.status() === 404 && u.includes('/api/artifacts/'))) {
      p.apiErrors.push(`${r.status()} ${r.request().method()} ${u.replace(/^https?:\/\/[^/]+/, '')}`);
    }
  });
  return p;
}

async function nav(page: Page, name: string) {
  await page.getByRole('button', { name, exact: true }).first().click();
}

test.describe('开屏动画', () => {
  test('首次打开播放并自动进入主界面', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    const splash = page.getByRole('status', { name: '星舵 Helm 正在启动' });
    await expect(splash).toBeVisible();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${SHOTS}/00-splash.png` });
    await expect(splash).toBeHidden({ timeout: 8000 });
    await expect(page.getByText('星舵 Helm').first()).toBeVisible();
    // 刷新后同一会话不再播放
    await page.reload();
    await expect(page.getByRole('status', { name: '星舵 Helm 正在启动' })).toHaveCount(0);
    expect(probs.pageErrors).toEqual([]);
  });
});

test.describe('主界面', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => sessionStorage.setItem('eas-splash-shown', '1'));
  });

  test('逐页打开所有页面无报错', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    await expect(page.getByText('服务在线')).toBeVisible({ timeout: 15_000 });
    for (const [i, name] of NAV.entries()) {
      await nav(page, name);
      await page.waitForTimeout(1200);
      await page.screenshot({ path: `${SHOTS}/1${String(i).padStart(2, '0')}-${name}.png` });
    }
    expect(probs.pageErrors, '页面脚本错误').toEqual([]);
    expect(probs.apiErrors, '接口错误').toEqual([]);
  });

  test('主题切换', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');
    const before = await html.getAttribute('data-theme');
    await page.getByRole('button', { name: /切换到(亮|暗)色主题/ }).click();
    await expect(html).not.toHaveAttribute('data-theme', before ?? '');
    await page.screenshot({ path: `${SHOTS}/20-theme-toggled.png` });
    await page.getByRole('button', { name: /切换到(亮|暗)色主题/ }).click();
    await expect(html).toHaveAttribute('data-theme', before ?? 'dark');
  });

  test('历史记录：设计书、执行计划、重命名、归档、删除确认', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    await nav(page, '历史记录');
    await expect(page.getByRole('button', { name: /^设计书 \(\d+\)$/ })).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/30-history-designs.png` });

    // 执行计划标签（之前会直接报错）
    await page.getByRole('button', { name: /^执行计划 \(\d+\)$/ }).click();
    const rows = page.locator('[role="button"]').filter({ has: page.getByLabel('重命名计划') });
    await expect(rows.first()).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/31-history-plans.png` });

    // 优先挑已有名字的计划，改完再改回原名
    const count = await rows.count();
    let idx = 0;
    for (let i = 0; i < count; i++) {
      const t = (await rows.nth(i).innerText()).split('\n')[0];
      if (t && t !== '未命名计划') { idx = i; break; }
    }
    const row = rows.nth(idx);
    const planId = (await row.locator('code').innerText()).trim();
    const original = (await row.innerText()).split('\n')[0].trim();
    const target = () => page.locator('[role="button"]').filter({ hasText: planId });

    await target().getByLabel('重命名计划').click();
    await target().getByPlaceholder('给这个计划起个名字').fill('E2E 重命名测试');
    await target().getByLabel('保存名称').click();
    await expect(target()).toContainText('E2E 重命名测试');
    const restoreTo = original === '未命名计划' ? `计划 ${planId.slice(0, 8)}` : original;
    await target().getByLabel('重命名计划').click();
    await target().getByPlaceholder('给这个计划起个名字').fill(restoreTo);
    await target().getByPlaceholder('给这个计划起个名字').press('Enter');
    await expect(target()).toContainText(restoreTo);
    test.info().annotations.push({ type: 'rename', description: `${planId}: "${original}" -> "${restoreTo}"` });

    // 归档 → 默认列表隐藏 → 显示已归档 → 取消归档
    await target().getByLabel('归档计划').click();
    await expect(target()).toHaveCount(0);
    await page.getByLabel(/显示已归档/).check();
    await expect(target()).toContainText('已归档');
    await page.screenshot({ path: `${SHOTS}/32-history-archived.png` });
    await target().getByLabel('取消归档').click();
    await expect(target()).not.toContainText('已归档');
    await page.getByLabel(/显示已归档/).uncheck();
    await expect(target()).toHaveCount(1);

    // 删除：只看确认框，然后取消
    let dialogText = '';
    page.once('dialog', async (d) => { dialogText = d.message(); await d.dismiss(); });
    const del = target().getByLabel('删除计划');
    if (await del.isEnabled()) {
      await del.click();
      expect(dialogText).toContain('确定删除执行计划');
      await expect(target()).toHaveCount(1);
    }

    // 打开计划 → 计划页 + 隔离分支面板
    await target().click();
    await expect(page.getByRole('region', { name: '隔离分支' })).toBeVisible({ timeout: 10_000 });
    await page.screenshot({ path: `${SHOTS}/33-plan-from-history.png` });

    // 设计书：打开完整内容
    await nav(page, '历史记录');
    const designRow = page.locator('[role="button"]').filter({ has: page.getByLabel('删除设计书') }).first();
    await designRow.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${SHOTS}/34-design-from-history.png` });

    expect(probs.pageErrors).toEqual([]);
    expect(probs.apiErrors).toEqual([]);
  });

  test('执行计划：隔离分支面板', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    // 从历史记录打开最近的计划
    await nav(page, '历史记录');
    await page.getByRole('button', { name: /^执行计划 \(\d+\)$/ }).click();
    await page.locator('[role="button"]').filter({ has: page.getByLabel('重命名计划') }).first().click();
    const panel = page.getByRole('region', { name: '隔离分支' });
    await expect(panel).toBeVisible({ timeout: 10_000 });
    await panel.getByLabel('刷新隔离分支状态').click();
    await expect(panel).not.toContainText('读取中');
    const header = panel.getByRole('button').first();
    await header.click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/40-worktree.png` });
    const hasBranch = await panel.getByRole('button', { name: /合并回主仓库/ }).count();
    test.info().annotations.push({ type: 'worktree', description: hasBranch ? '已绑定隔离分支（未点合并/丢弃）' : '未启用（未点创建）' });
    expect(probs.pageErrors).toEqual([]);
    expect(probs.apiErrors).toEqual([]);
  });

  test('系统概览', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    await nav(page, '系统概览');
    await expect(page.getByText('调用次数')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/^技能包（\d+）$/)).toBeVisible();
    await expect(page.getByText(/^能力清单（\d+）$/)).toBeVisible();
    await expect(page.getByText('读取失败')).toHaveCount(0);
    await page.getByRole('button', { name: '刷新' }).click();
    await expect(page.getByText('调用次数')).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/50-system.png`, fullPage: true });
    expect(probs.pageErrors).toEqual([]);
    expect(probs.apiErrors).toEqual([]);
  });

  test('产物库：外部引用显示文件位置', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    await nav(page, '产物库');
    await page.getByRole('button').filter({ has: page.locator('.font-mono') }).first().waitFor({ timeout: 10_000 }).catch(() => {});
    const ext = page.getByRole('button').filter({ hasText: '外部引用' }).first();
    if (await ext.count() === 0) test.skip(true, '没有外部引用产物');
    await ext.click();
    const dlg = page.getByRole('dialog', { name: '产物内容预览' });
    await expect(dlg).toContainText('文件位置');
    await page.screenshot({ path: `${SHOTS}/60-artifact-external.png` });
    await dlg.getByRole('button', { name: '关闭' }).click();
    await expect(dlg).toHaveCount(0);
    // 已入库产物仍能正常预览
    const stored = page.getByRole('button').filter({ hasNotText: '外部引用' }).filter({ has: page.locator('.font-mono') }).first();
    if (await stored.count()) {
      await stored.click();
      await expect(page.getByRole('dialog', { name: '产物内容预览' })).toBeVisible();
      await expect(page.getByRole('dialog', { name: '产物内容预览' })).not.toContainText('文件位置');
      await page.screenshot({ path: `${SHOTS}/61-artifact-stored.png` });
    }
    expect(probs.pageErrors).toEqual([]);
  });

  test('AI 面板：新对话、收起/展开', async ({ page }) => {
    await page.goto('/');
    const newChat = page.getByRole('button', { name: '开始新对话' });
    if (await newChat.count() === 0) await page.getByRole('button', { name: 'AI Copilot', exact: true }).click();
    await expect(page.getByRole('button', { name: '开始新对话' })).toBeVisible();
    await page.getByRole('button', { name: '开始新对话' }).click();
    await expect(page.getByText('你好，我是你的星舵 Copilot')).toBeVisible();
    await page.getByRole('button', { name: '收起 AI 面板' }).click();
    await expect(page.getByRole('button', { name: '开始新对话' })).toHaveCount(0);
    await page.getByRole('button', { name: 'AI Copilot', exact: true }).click();
    await expect(page.getByRole('button', { name: '开始新对话' })).toBeVisible();
  });

  test('各页刷新按钮', async ({ page }) => {
    const probs = watch(page);
    await page.goto('/');
    for (const name of ['产物库', 'Agent 记录', '审计日志', '历史记录', '系统概览']) {
      await nav(page, name);
      const btn = page.getByRole('main').getByRole('button', { name: /刷新/ }).first();
      await expect(btn).toBeVisible({ timeout: 10_000 });
      await btn.click();
      await page.waitForTimeout(800);
    }
    expect(probs.pageErrors).toEqual([]);
    expect(probs.apiErrors).toEqual([]);
  });
});
