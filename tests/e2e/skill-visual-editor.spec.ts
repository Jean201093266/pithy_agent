import { expect, test } from '@playwright/test';

test('visual skill editor can build and save skill', async ({ page }) => {
  await page.goto('/');

  // Invalid params should be rejected by validation.
  await page.locator('#visual-step-kind').selectOption('llm');
  await page.locator('#visual-step-name').fill('invalid_step');
  await page.locator('#visual-step-params').fill('{"bad":true}');
  await page.locator('#visual-add-step').click();
  await expect(page.locator('#visual-step-feedback')).toContainText('prompt');

  await page.locator('#visual-skill-name').fill('visual_skill_demo');
  await page.locator('#visual-skill-version').fill('1.0.0');
  await page.locator('#visual-step-kind').selectOption('llm');
  await page.locator('#visual-step-name').fill('draft_step');
  await page.locator('#visual-step-params').fill('{"prompt":"hello from visual editor"}');
  await page.locator('#visual-add-step').click();

  // Add second step then copy/reorder using visual controls.
  await page.locator('#visual-step-kind').selectOption('tool');
  await page.locator('#visual-step-name').fill('echo');
  await page.locator('#visual-step-params').fill('{"message":"ok"}');
  await page.locator('#visual-add-step').click();

  await page.locator('#visual-selected-step').fill('1');
  await page.locator('#visual-copy-step').click();
  await expect(page.locator('#visual-steps-output')).toContainText('draft_step_copy');

  await page.locator('#visual-selected-step').fill('3');
  await page.locator('#visual-move-up').click();
  await expect(page.locator('#visual-step-feedback')).toContainText('上移');

  await page.locator('#visual-build-skill').click();
  await expect(page.locator('#skill-json')).toHaveValue(/visual_skill_demo/);

  await page.locator('#save-skill').click();
  await expect(page.locator('#skill-result')).toContainText('skill id');
});

