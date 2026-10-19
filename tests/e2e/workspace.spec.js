import { expect, test } from '@playwright/test';

test('home workspace remains usable and text-led', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /your video.*ready to answer/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /upload a video/i }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('online import presents progress and support copy', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /use a link/i }).click();
  await expect(page.locator('#video-url')).toBeVisible();
  await expect(page.getByText(/public.*link|youtube|instagram/i).first()).toBeVisible();
});
