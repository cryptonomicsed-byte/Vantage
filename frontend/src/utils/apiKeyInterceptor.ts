// Since PR #39 locked down almost every /api/* endpoint behind X-Agent-Key,
// dozens of components across the app that pre-date that change call
// fetch('/api/...') with no headers at all, assuming reads stay public.
// Rewriting every one of those call sites individually is high-risk (easy to
// miss one) and doesn't scale to future components making the same
// assumption. Instead, patch the one thing all of them funnel through:
// window.fetch itself. Any request to a same-origin /api/ path gets
// X-Agent-Key attached automatically if the caller didn't already set one —
// callers that explicitly authenticate as a *different* identity (rare,
// e.g. an admin key flow) are left untouched.
//
// Requires ensureAgentKey() to have populated localStorage before requests
// fire — see AppLayout's keyReady gate in App.tsx.

let installed = false

function isSameOriginApiPath(input: RequestInfo | URL): string | null {
  const raw = input instanceof Request ? input.url : String(input)
  try {
    const url = new URL(raw, window.location.origin)
    if (url.origin !== window.location.origin) return null
    return url.pathname.startsWith('/api/') ? url.pathname : null
  } catch {
    return null
  }
}

export function installApiKeyInterceptor(): void {
  if (installed) return
  installed = true
  const originalFetch = window.fetch.bind(window)

  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (!isSameOriginApiPath(input)) return originalFetch(input, init)

    const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
    if (!headers.has('X-Agent-Key')) {
      const key = localStorage.getItem('vantage_api_key')
      if (key) headers.set('X-Agent-Key', key)
    }
    // Human accounts are a separate, additive identity layer -- both headers
    // can travel on the same request; each backend route only looks at
    // whichever one its specific auth dependency declares.
    if (!headers.has('X-Human-Session')) {
      const session = localStorage.getItem('vantage_human_session')
      if (session) headers.set('X-Human-Session', session)
    }
    return originalFetch(input, { ...init, headers })
  }
}
