import { expect, test } from '@playwright/test';

test('skill import -> versions -> rollback', async ({ page }) => {
  await page.goto('/');
  const skillName = `e2e_skill_${Date.now()}`;

  const skillPayload = JSON.stringify({
    name: skillName,
    version: '1.0.0',
    description: 'v1',
    steps: [{ kind: 'llm', name: 'v1_step', params: { prompt: 'v1 prompt' } }],
  });

  await page.locator('#skill-json').fill(skillPayload);
  await page.locator('#save-skill').click();
  await expect(page.locator('#skill-result')).toContainText('skill id');

  const importYaml = [
    `name: ${skillName}`,
    'version: 2.0.0',
    'description: v2',
    'steps:',
    '  - kind: llm',
    '    name: v2_step',
    '    params:',
    '      prompt: v2 prompt',
  ].join('\n');

  await page.locator('#skill-import-format').selectOption('yaml');
  await page.locator('#skill-import-content').fill(importYaml);
  await page.locator('#import-skill').click();
  await expect(page.locator('#skill-result')).toContainText('2.0.0');

  await page.locator('#load-skill-versions').click();
  await expect(page.locator('#skill-versions')).toContainText('2.0.0');
  await expect(page.locator('#skill-versions')).toContainText('1.0.0');

  const versionSelect = page.locator('#skill-version-select');
  const optionCount = await versionSelect.locator('option').count();
  expect(optionCount).toBeGreaterThan(1);

  // Select the oldest version (v1) and rollback.
  const oldestOption = versionSelect.locator('option').last();
  const targetVersionId = await oldestOption.getAttribute('value');
  expect(targetVersionId).toBeTruthy();
  await versionSelect.selectOption(targetVersionId || '');
  await page.locator('#skill-version-id').fill(targetVersionId || '');
  await page.locator('#rollback-skill').click();
  await expect(page.locator('#skill-result')).toContainText('"active_version": "1.0.0"');
});

