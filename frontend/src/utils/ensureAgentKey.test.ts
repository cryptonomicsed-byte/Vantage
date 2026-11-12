import { beforeEach, describe, expect, it } from 'vitest'
import { hasEnteredApp, hasOwnAgentKey, markEnteredApp } from './ensureAgentKey'

// "/" must show the landing page to anyone who has not identified themselves.
// The auto-minted viewer-* key is present for every visitor, so it must not count.
describe('root landing decision', () => {
  beforeEach(() => localStorage.clear())

  it('a fresh visitor has no identity', () => {
    expect(hasOwnAgentKey()).toBe(false)
    expect(hasEnteredApp()).toBe(false)
  })

  it('an auto-minted viewer key is not an own identity', () => {
    localStorage.setItem('vantage_api_key', 'k')
    localStorage.setItem('vantage_agent_name', 'viewer-ab12cd34')
    expect(hasOwnAgentKey()).toBe(false)
  })

  it('a registered or pasted agent key is', () => {
    localStorage.setItem('vantage_api_key', 'k')
    localStorage.setItem('vantage_agent_name', 'Hermes-Ares')
    expect(hasOwnAgentKey()).toBe(true)
  })

  it('"Go to app" on the landing page is remembered', () => {
    markEnteredApp()
    expect(hasEnteredApp()).toBe(true)
  })
})
