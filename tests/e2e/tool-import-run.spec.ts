import { expect, test } from '@playwright/test';

test('custom tool can be imported and executed from UI', async ({ page }) => {
  await page.goto('/');
  const toolName = `ui_custom_echo_${Date.now()}`;

  const manifest = JSON.stringify({
    name: toolName,
    description: 'UI echo wrapper',
    risk_level: 'normal',
    target_tool: 'echo',
    default_params: { message: 'default' },
    param_mapping: { text: 'message' },
    version: '1.0.0',
  });

  await page.locator('#tool-manifest-json').fill(manifest);
  await page.locator('#import-tool').click();
  await expect(page.locator('#skill-result')).toContainText(toolName);

  await page.locator('#tool-run-name').fill(toolName);
  await page.locator('#tool-run-params').fill('{"text":"hello tool"}');
  await page.locator('#run-custom-tool').click();

  await expect(page.locator('#skill-result')).toContainText('hello tool');
  await expect(page.locator('#tool-manifests-output')).toContainText(toolName);
});

