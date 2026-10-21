import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const baseURL = process.argv[2] || 'http://127.0.0.1:4310';
const outputDir = new URL('../docs/product/', import.meta.url);
const outputPath = name => fileURLToPath(new URL(name, outputDir));

await mkdir(outputDir, { recursive: true });

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.screenshot({ path: outputPath('workspace.png'), fullPage: false });

  await page.getByRole('button', { name: 'Try demo' }).click();
  await page.getByRole('heading', { name: 'Alpine trail — demo.webm' }).waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: outputPath('video-studio.png'), fullPage: false });

  const mobile = await browser.newPage({ viewport: { width: 430, height: 932 } });
  await mobile.goto(baseURL, { waitUntil: 'networkidle' });
  await mobile.screenshot({ path: outputPath('mobile-workspace.png'), fullPage: false });
} finally {
  await browser.close();
}
