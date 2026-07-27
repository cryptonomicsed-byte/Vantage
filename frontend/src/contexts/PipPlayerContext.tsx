import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, Maximize2 } from 'lucide-react'

/**
 * A single real <video> element lives ONCE at the app root and NEVER moves
 * into a page-owned DOM node -- it's always rendered here, position:fixed,
 * and just visually repositioned every frame to overlay wherever a page
 * wants it shown "inline" (via registerSlot), or bottom-right once nothing
 * claims a slot (i.e. you navigated away from Cinema/Agent.TV).
 *
 * Earlier version portaled the video INTO a placeholder div rendered by
 * the owning page. That placeholder was part of the page's own React
 * subtree, so navigating away made React remove it (and the video
 * physically nested inside it) in the SAME commit as the route change --
 * before any cleanup code could retarget the portal. The video was gone
 * before it had a chance to float. Keeping the video permanently at the
 * root and only ever moving it with CSS (position/size), never touching
 * its DOM parent, makes it immune to that race entirely.
 */

interface PipState {
  src: string | null
  title: string
  returnPath: string | null
}

interface PipApi {
  play: (opts: { src: string; title: string; startTime?: number; returnPath?: string }) => void
  close: () => void
  /** Pages call this with a ref to an (empty, layout-only) placeholder div
   * sized/positioned where they want the video to visually appear "inline".
   * Passing null (or letting the node detach from the document) falls back
   * to the floating bottom-right box automatically. */
  registerSlot: (el: HTMLDivElement | null) => void
  state: PipState
}

const PipContext = createContext<PipApi | null>(null)

export function usePip(): PipApi {
  const ctx = useContext(PipContext)
  if (!ctx) throw new Error('usePip must be used within PipProvider')
  return ctx
}

export function PipProvider({ children }: { children: React.ReactNode }) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const [state, setState] = useState<PipState>({ src: null, title: '', returnPath: null })
  const pendingStartTime = useRef<number | undefined>(undefined)
  const slotRef = useRef<HTMLDivElement | null>(null)
  const [isFloating, setIsFloating] = useState(false)

  const play = useCallback((opts: { src: string; title: string; startTime?: number; returnPath?: string }) => {
    setState(prev => {
      if (prev.src === opts.src) return prev // same segment already loaded -- don't restart it
      pendingStartTime.current = opts.startTime
      return { src: opts.src, title: opts.title, returnPath: opts.returnPath || null }
    })
  }, [])

  const close = useCallback(() => {
    setState({ src: null, title: '', returnPath: null })
    slotRef.current = null
  }, [])

  const registerSlot = useCallback((el: HTMLDivElement | null) => {
    slotRef.current = el
  }, [])

  // Seek once to the real live offset when a fresh src's metadata loads --
  // this is the fix for Agent.TV channels restarting from 0:00 on every
  // click instead of joining the 24hr broadcast where it actually is.
  useEffect(() => {
    const v = videoRef.current
    if (!v) return
    const onLoaded = () => {
      if (pendingStartTime.current != null) {
        v.currentTime = pendingStartTime.current
        pendingStartTime.current = undefined
      }
    }
    v.addEventListener('loadedmetadata', onLoaded)
    return () => v.removeEventListener('loadedmetadata', onLoaded)
  }, [state.src])

  // Continuously re-position the (always-mounted-here) container to match
  // the current slot's on-screen rect, falling back to the floating box
  // whenever no slot is registered OR the registered node has been
  // detached from the document (self-healing against any unmount-timing
  // race -- we don't rely on registerSlot(null) firing at exactly the
  // right moment, document.contains() is checked fresh every frame).
  useEffect(() => {
    if (!state.src) return
    let raf = 0
    const tick = () => {
      const el = containerRef.current
      const slot = slotRef.current
      const floating = !slot || !document.body.contains(slot)
      if (el) {
        if (!floating) {
          const r = slot!.getBoundingClientRect()
          el.style.top = `${r.top}px`
          el.style.left = `${r.left}px`
          el.style.width = `${r.width}px`
          el.style.height = `${r.height}px`
          el.style.bottom = 'auto'
          el.style.right = 'auto'
          el.style.borderRadius = '0px'
        } else {
          el.style.top = 'auto'
          el.style.left = 'auto'
          el.style.bottom = '16px'
          el.style.right = '16px'
          el.style.width = '300px'
          el.style.height = '168px'
          el.style.borderRadius = '10px'
        }
      }
      setIsFloating(prev => (prev !== floating ? floating : prev))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [state.src])

  return (
    <PipContext.Provider value={{ play, close, registerSlot, state }}>
      {children}
      <div
        ref={containerRef}
        style={{
          position: 'fixed', zIndex: 999, overflow: 'hidden', background: '#000',
          boxShadow: isFloating ? '0 8px 30px rgba(0,0,0,0.6)' : 'none',
          border: isFloating ? '1px solid rgba(255,255,255,0.15)' : 'none',
          display: state.src ? 'block' : 'none',
        }}
      >
        {state.src && (
          <video
            ref={videoRef}
            key={state.src}
            src={state.src}
            autoPlay
            controls
            style={{ width: '100%', height: '100%', display: 'block', background: '#000' }}
          />
        )}
        {isFloating && state.src && (
          <div style={{
            position: 'absolute', top: 0, left: 0, right: 0, padding: '4px 6px', zIndex: 2,
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
            background: 'linear-gradient(rgba(0,0,0,0.75), transparent)', pointerEvents: 'none',
          }}>
            <span style={{
              fontSize: 10, color: '#e0e0ff', fontFamily: 'monospace', whiteSpace: 'nowrap',
              overflow: 'hidden', textOverflow: 'ellipsis', pointerEvents: 'none',
            }}>{state.title}</span>
            <div style={{ display: 'flex', gap: 4, pointerEvents: 'auto' }}>
              {state.returnPath && (
                <button
                  onClick={() => navigate(state.returnPath!)}
                  title="Back to full view"
                  style={{ background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none', borderRadius: 4, width: 20, height: 20, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                ><Maximize2 size={10} /></button>
              )}
              <button
                onClick={close}
                title="Close"
                style={{ background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none', borderRadius: 4, width: 20, height: 20, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              ><X size={10} /></button>
            </div>
          </div>
        )}
      </div>
    </PipContext.Provider>
  )
}
