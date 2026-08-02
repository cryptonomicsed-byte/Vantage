import React, { useEffect, useState } from 'react'
import { Download, X } from 'lucide-react'

const DISMISSED_KEY = 'vantage_install_dismissed_at'
const DISMISS_SNOOZE_MS = 7 * 24 * 60 * 60 * 1000 // re-offer after a week, not never-again

// Chrome/Edge/Android fire `beforeinstallprompt` when the manifest+SW
// eligibility bar is met; Safari/iOS never fires it at all (there's no
// programmatic install API there -- "Add to Home Screen" is a manual
// share-sheet action) so this banner simply doesn't render on iOS Safari,
// which is correct rather than a bug to fix.
export default function InstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] = useState<any>(null)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const isStandalone =
      window.matchMedia('(display-mode: standalone)').matches ||
      (window.navigator as any).standalone === true
    if (isStandalone) return

    const dismissedAt = Number(localStorage.getItem(DISMISSED_KEY) || 0)
    if (dismissedAt && Date.now() - dismissedAt < DISMISS_SNOOZE_MS) return

    function onBeforeInstallPrompt(e: Event) {
      e.preventDefault()
      setDeferredPrompt(e)
      setVisible(true)
    }
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt)
    window.addEventListener('appinstalled', () => setVisible(false))
    return () => window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt)
  }, [])

  async function install() {
    if (!deferredPrompt) return
    deferredPrompt.prompt()
    await deferredPrompt.userChoice.catch(() => {})
    setDeferredPrompt(null)
    setVisible(false)
  }

  function dismiss() {
    localStorage.setItem(DISMISSED_KEY, String(Date.now()))
    setVisible(false)
  }

  if (!visible) return null

  return (
    <div
      style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 200,
        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12,
        padding: '10px 16px', background: 'rgba(8,8,16,0.95)', backdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(0,245,255,0.25)', fontFamily: 'Inter, sans-serif', fontSize: 13,
      }}
    >
      <span style={{ color: 'var(--muted-hi, #c8ccd8)' }}>
        Install <strong style={{ color: 'var(--cyan, #00f5ff)' }}>Vantage</strong> on this device for the full app experience.
      </span>
      <button className="btn btn-primary btn-sm" onClick={install} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Download size={13} /> Install
      </button>
      <button
        onClick={dismiss}
        title="Not now"
        style={{ background: 'none', border: 'none', color: 'var(--muted, #8892a6)', cursor: 'pointer', display: 'flex' }}
      >
        <X size={14} />
      </button>
    </div>
  )
}
