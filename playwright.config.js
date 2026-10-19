import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { baseURL: 'http://127.0.0.1:4310', trace: 'on-first-retry' },
  webServer: [
    { command: 'python3 -m uvicorn server.main:app --host 127.0.0.1 --port 8787', port: 8787, reuseExistingServer: true },
    { command: 'npx vite --host 127.0.0.1 --port 4310 --strictPort', port: 4310, reuseExistingServer: true },
  ],
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 7'] } },
  ],
});
