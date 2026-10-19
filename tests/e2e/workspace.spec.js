import { expect, test } from '@playwright/test';

test('home workspace remains usable and text-led', async ({ page }) => {
  await page.goto('/');
  if (await page.getByRole('heading', { name: /sign in to your library/i }).isVisible()) {
    await expect(page.getByLabel('Email address')).toBeVisible();
    await expect(page.getByRole('button', { name: /email me a sign-in link/i })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    return;
  }
  await expect(page.getByRole('heading', { name: /your video.*ready to answer/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /upload a video/i }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('online import presents progress and support copy', async ({ page }) => {
  await page.goto('/');
  test.skip(await page.getByRole('heading', { name: /sign in to your library/i }).isVisible(), 'Requires an authenticated browser session');
  await page.getByRole('button', { name: /use a link/i }).click();
  await expect(page.locator('#video-url')).toBeVisible();
  await expect(page.getByText(/public.*link|youtube|instagram/i).first()).toBeVisible();
});
