/* eslint-disable check-file/filename-naming-convention */
/// <reference types="node" />
import { defineConfig, devices } from '@playwright/test';

import dotenv from 'dotenv';
import path from 'path';
const __dirname = path.dirname(new URL(import.meta.url).pathname);
// The project keeps a single env file at the repo root (see vite.config.ts envDir).
dotenv.config({ path: path.resolve(__dirname, '..', '.env'), quiet: true });

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        permissions: ['clipboard-read', 'clipboard-write'],
      },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
  ],
  webServer: {
    command: 'npm run start',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
});
