import { defineConfig, devices } from '@playwright/test'

// Real browser E2E tests -- the audit found zero automated UI coverage for
// the 85+ React components despite 8,716 lines of backend-only tests. No
// separate staging environment exists, so these run against the live
// production URL by default; kept deliberately read-mostly (page loads,
// console-error checks) with any state-creating test using a clearly
// test-prefixed name so it's identifiable/cleanable, same discipline used
// for manual verification throughout this session. Override BASE_URL to
// point at a local dev server (`npm run dev`) instead.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.BASE_URL || 'https://omokoda.duckdns.org',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
})
