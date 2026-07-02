import React, { useState } from 'react'
import { Bot, X } from 'lucide-react'
import CopilotChat from './CopilotChat'

/**
 * Floating Copilot — companion to the Observer toggle (which sits at
 * bottom:56px right:16px). The Copilot follows you across every section
 * instead of occupying a sidebar slot; /copilot remains as a deep link.
 */
export default function CopilotDock() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <button
        onClick={() => setOpen(o => !o)}
        title={open ? 'Close Copilot' : 'Open Copilot — your connected agent'}
        style={{
          position: 'fixed', bottom: 96, right: 16, zIndex: 91,
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '6px 12px', borderRadius: 99, cursor: 'pointer',
          background: 'rgba(8,8,16,0.9)', backdropFilter: 'blur(8px)',
          border: `1px solid ${open ? 'rgba(0,245,255,0.55)' : 'rgba(0,245,255,0.35)'}`,
          color: open ? 'var(--cyan, #00f5ff)' : 'var(--muted, #8892a6)',
          fontFamily: 'Rajdhani, sans-serif', fontSize: 12, transition: 'all .2s',
        }}
      >
        <Bot size={14} />
        <span>Copilot</span>
      </button>

      {open && (
        <div style={{
          position: 'fixed', top: 0, bottom: 0, right: 0, zIndex: 92,
          width: 'min(440px, 100vw)', display: 'flex', flexDirection: 'column',
          background: 'rgba(5,5,10,0.97)', backdropFilter: 'blur(20px)',
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
