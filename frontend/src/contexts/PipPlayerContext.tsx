import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import ReactDOM from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { X, Maximize2 } from 'lucide-react'

/**
 * A single real <video> element lives once at the app root and is portaled
 * either into whatever page currently "claims" an inline slot for it, or
 * (when no page claims it -- i.e. the user navigated away from Cinema/
 * Agent.TV) into a floating bottom-right PiP box. Because it's the SAME
 * DOM node moving between two portal targets, not two separate <video>
 * tags, playback/currentTime is never interrupted by navigation.
 */

interface PipState {
  src: string | null
  title: string
  returnPath: string | null
}

interface PipApi {
  play: (opts: { src: string; title: string; startTime?: number; returnPath?: string }) => void
  close: () => void
  claimInline: (el: HTMLDivElement | null) => void
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
  const navigate = useNavigate()
  const [state, setState] = useState<PipState>({ src: null, title: '', returnPath: null })
  const pendingStartTime = useRef<number | undefined>(undefined)
  const [inlineEl, setInlineEl] = useState<HTMLDivElement | null>(null)
  const [floatingEl, setFloatingEl] = useState<HTMLDivElement | null>(null)

  const play = useCallback((opts: { src: string; title: string; startTime?: number; returnPath?: string }) => {
    setState(prev => {
      if (prev.src === opts.src) return prev // same segment already loaded -- don't restart it
      pendingStartTime.current = opts.startTime
      return { src: opts.src, title: opts.title, returnPath: opts.returnPath || null }
    })
  }, [])

  const close = useCallback(() => {
    setState({ src: null, title: '', returnPath: null })
    setInlineEl(null)
  }, [])

  const claimInline = useCallback((el: HTMLDivElement | null) => {
    setInlineEl(el)
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

  const isFloating = !inlineEl && !!state.src

  const videoNode = state.src ? (
    <video
      ref={videoRef}
      key={state.src}
      src={state.src}
      autoPlay
      controls
      style={{ width: '100%', height: '100%', display: 'block', background: '#000' }}
    />
  ) : null

  const portalTarget = inlineEl || floatingEl

  return (
    <PipContext.Provider value={{ play, close, claimInline, state }}>
      {children}
      <div
        ref={setFloatingEl}
        style={isFloating ? {
          position: 'fixed', bottom: 16, right: 16, width: 300, height: 168, zIndex: 999,
          borderRadius: 10, overflow: 'hidden', boxShadow: '0 8px 30px rgba(0,0,0,0.6)',
          border: '1px solid rgba(255,255,255,0.15)', background: '#000',
        } : { width: 0, height: 0, overflow: 'hidden', position: 'fixed', pointerEvents: 'none' }}
      >
        {isFloating && (
          <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
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
          </div>
        )}
      </div>
      {portalTarget && videoNode ? ReactDOM.createPortal(videoNode, portalTarget) : null}
    </PipContext.Provider>
  )
}
