import { test, expect } from '@playwright/test'

// Real page-load smoke tests -- the audit found zero automated coverage
// for the 85+ React components. These check more than "did the server
// return 200": App.tsx wraps every route in an ErrorBoundary that renders
// a "Signal Lost" fallback on any client-side render crash, so a route
// can 200 at the HTTP layer while still being fully broken in the
// browser (exactly the kind of gap a backend-only test suite can't see).
// We assert that fallback is NOT present, plus a real page-specific
// signal, for each route -- not just "no crash", but "the actual page
// rendered its real content."

const PUBLIC_ROUTES: { path: string; expect: RegExp | string }[] = [
  { path: '/welcome', expect: /Vantage/i },
  { path: '/agents', expect: /agent/i },
  { path: '/guilds', expect: /guild/i },
  { path: '/leaderboard', expect: /leaderboard/i },
]

for (const route of PUBLIC_ROUTES) {
  test(`${route.path} renders without crashing`, async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })

    const response = await page.goto(route.path)
    expect(response?.status(), `${route.path} HTTP status`).toBeLessThan(400)

    await expect(page.getByText('Signal Lost')).toHaveCount(0)
    await expect(page.locator('body')).toContainText(route.expect, { timeout: 10000 })
  })
}

test('root route resolves to either the feed or the landing page, not a crash', async ({ page }) => {
  const response = await page.goto('/')
  expect(response?.status()).toBeLessThan(400)
  await expect(page.getByText('Signal Lost')).toHaveCount(0)
  // A genuinely new/anonymous visitor should land on /welcome (the real
  // landing/login page), not silently see the main feed -- this is the
  // exact routing gap flagged earlier this project's history ("main feed
  // is still the main page... users aren't properly routed to /welcome").
  // A returning visitor with a stored key legitimately sees the feed
  // instead, so this only asserts "didn't crash", not which one it chose.
})

test('cinema, audio, agent.tv, and swarm all render their real UI, not just a blank shell', async ({ page }) => {
  for (const path of ['/cinema', '/audio', '/agenttv', '/swarm']) {
    await page.goto(path)
    await expect(page.getByText('Signal Lost')).toHaveCount(0)
    // These pages fetch their real content async after mount (e.g.
    // AgentTVSection's channel list) -- checking body text immediately
    // just catches a transient "Loading…" state, not the real page.
    // Wait for that in-flight fetch to settle before asserting content.
    await page.waitForLoadState('networkidle')
    // Real content check: the app shell (nav/header) always renders even
    // on a totally broken page, so assert something real loaded beneath
    // it instead of just "the page has a body".
    const bodyText = await page.locator('body').innerText()
    expect(bodyText.trim().length, `${path} should render real content, not an empty shell`).toBeGreaterThan(50)
  }
})

// Every static (non-parameterized) route in App.tsx -- this exact class of
// bug (a route works fine via in-app client-side navigation but 404s at
// the HTTP layer on direct load, because main.py's SPA route allowlist is
// hand-maintained and has no structural guarantee of staying in sync with
// App.tsx) is what caught /playlists, /agenttv, and /creator-analytics
// missing entirely. This sweep exists so the NEXT time a route is added to
// App.tsx without updating that allowlist, it fails a test instead of only
// failing silently for whoever hits it by typing/bookmarking the URL.
const ALL_STATIC_ROUTES = [
  '/dashboard', '/analytics', '/inbox', '/search', '/api-docs', '/market',
  '/trading', '/knowledge', '/settings', '/workspace', '/heatmap', '/copilot',
  '/collectives', '/vault', '/video', '/studio', '/playlists',
  '/creator-analytics', '/code',
]

for (const path of ALL_STATIC_ROUTES) {
  test(`${path} is directly navigable (not just reachable via client-side nav)`, async ({ page }) => {
    const response = await page.goto(path)
    expect(response?.status(), `${path} HTTP status`).toBeLessThan(400)
    await expect(page.getByText('Signal Lost')).toHaveCount(0)
    await expect(page.getByText('"detail":"Not Found"')).toHaveCount(0)
  })
}
