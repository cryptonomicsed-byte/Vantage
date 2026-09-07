import React, { useState, useEffect } from 'react'
import { X } from 'lucide-react'
import CopilotChat from './CopilotChat'

export default function CopilotDock() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const handler = () => setOpen(o => !o)
    window.addEventListener('vantage:toggle-copilot', handler)
    return () => window.removeEventListener('vantage:toggle-copilot', handler)
  }, [])

  return (
    <>

      {open && (
        <div style={{
          // bottom: 28 (not 0) -- the global .status-bar footer is
          // position:fixed, bottom:0, full-width, z-index:98, which is
          // ABOVE this panel's z-index:92. At bottom:0 the dock's own
          // input row rendered underneath that footer and got visually
          // clipped/covered by it (not a flex/overflow bug -- confirmed
          // via computed getBoundingClientRect: the input row's own box
          // was fully within the viewport, just stacked under the
          // status bar). Reserving the status bar's exact height here
          // keeps the input row visible above it on every viewport.
          position: 'fixed', top: 0, bottom: 28, right: 0, zIndex: 92,
          width: 'min(440px, 100vw)', display: 'flex', flexDirection: 'column',
          background: 'rgba(5,5,10,0.45)', backdropFilter: 'blur(20px)', WebkitBackdropFilter: 'blur(20px)',
          borderLeft: '1px solid rgba(0,245,255,0.2)',
          animation: 'slideInRight .2s',
        }}>
          {/* CopilotChat brings its own header (title, acting-as, alerts) — the dock only adds a close control */}
          <button
            onClick={() => setOpen(false)}
            title="Close Copilot"
            style={{ position: 'absolute', top: '50%', left: -14, transform: 'translateY(-50%)', zIndex: 2, background: 'rgba(8,8,16,0.95)', border: '1px solid rgba(0,245,255,0.3)', borderRadius: '8px 0 0 8px', color: 'var(--muted, #8892a6)', cursor: 'pointer', padding: '14px 3px' }}
          >
            <X size={14} />
          </button>
          <div style={{ flex: 1, minHeight: 0, padding: 14, overflow: 'hidden' }}>
            <CopilotChat />
          </div>
        </div>
      )}
    </>
  )
}
