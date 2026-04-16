import { expect, test } from '@playwright/test';

test('settings theme and language can be updated', async ({ page }) => {
  await page.goto('/');

  await page.locator('#pref-language').selectOption('en-US');
  await page.locator('#pref-theme').selectOption('dark');
  await page.locator('#pref-log-lines').fill('80');
  await page.locator('#save-settings').click();

  await expect(page.locator('#skill-result')).toContainText('Saved successfully');
  await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('#chat-title')).toContainText('Chat Workspace');
});

