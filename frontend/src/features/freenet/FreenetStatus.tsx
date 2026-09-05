/**
 * FreenetStatus — badge showing Freenet connection state.
 *
 * - Primary source: useFreenet() hook (live WebSocket status)
 * - Fallback: polls GET /api/freenet/status every 30 s in case the local
 *   WS node is inaccessible but the backend stub is reachable.
 */

import { useEffect, useRef, useState } from 'react';
import { useFreenet } from './useFreenet';
import type { FreenetStatus as FreenetStatusType } from './client';

interface ApiStatusResponse {
  status?: string;
}

const POLL_INTERVAL_MS = 30_000;

// Static lookup tables hoisted outside the component to avoid re-creation.
const LABEL: Record<FreenetStatusType | 'api_ok' | 'api_err', string> = {
  connected: 'Freenet: Connected',
  connecting: 'Freenet: Connecting...',
  disconnected: 'Freenet: Offline',
  error: 'Freenet: Offline',
  api_ok: 'Freenet: Connected',
  api_err: 'Freenet: Offline',
};

const DOT_COLOR: Record<FreenetStatusType | 'api_ok' | 'api_err', string> = {
  connected: '#22c55e',   // green-500
  connecting: '#f59e0b',  // amber-500
  disconnected: '#6b7280', // gray-500
  error: '#ef4444',       // red-500
  api_ok: '#22c55e',
  api_err: '#6b7280',
};

type DisplayKey = FreenetStatusType | 'api_ok' | 'api_err';

export function FreenetStatus() {
  const { status } = useFreenet();
  const [apiKey, setApiKey] = useState<'api_ok' | 'api_err' | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // Only poll the REST fallback when the WS client isn't connected.
    if (status === 'connected') {
      setApiKey(null);
      return;
    }

    async function poll() {
      try {
        const res = await fetch('/api/freenet/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: ApiStatusResponse = await res.json() as ApiStatusResponse;
        setApiKey(data.status === 'ok' ? 'api_ok' : 'api_err');
      } catch {
        setApiKey('api_err');
      }
    }

    poll();
    timerRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      if (timerRef.current !== null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [status]);

  // WebSocket connection takes priority; fall back to API poll result.
  const displayKey: DisplayKey =
    status === 'connected' ? 'connected' : apiKey ?? status;

  const label = LABEL[displayKey];
  const dotColor = DOT_COLOR[displayKey];

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '6px',
        fontSize: '0.75rem',
        color: '#d1d5db',
        userSelect: 'none',
      }}
      aria-label={label}
    >
      <span
        style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: dotColor,
          flexShrink: 0,
        }}
      />
      {label}
    </span>
  );
}
