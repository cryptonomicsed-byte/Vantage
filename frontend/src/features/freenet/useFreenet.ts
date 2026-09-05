/**
 * useFreenet — React hook that exposes a singleton FreenetClient with reactive status.
 */

import { useEffect, useState, useCallback } from 'react';
import { FreenetClient, FreenetStatus } from './client';

// Module-level singleton — shared across all components.
let _client: FreenetClient | null = null;

function getClient(): FreenetClient {
  if (!_client) {
    _client = new FreenetClient();
  }
  return _client;
}

export interface UseFreenetReturn {
  status: FreenetStatus;
  isConnected: boolean;
  subscribe: FreenetClient['subscribe'];
  put: FreenetClient['put'];
  get: FreenetClient['get'];
}

export function useFreenet(): UseFreenetReturn {
  const client = getClient();
  const [status, setStatus] = useState<FreenetStatus>(client.status);

  useEffect(() => {
    // Listen for status changes from the singleton.
    const unsubscribe = client.onStatusChange((s) => setStatus(s));

    // Attempt connection on first mount (non-blocking).
    if (client.status === 'disconnected') {
      client.connect().catch(() => {
        // Errors are already handled inside connect().
      });
    }

    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const subscribe = useCallback<FreenetClient['subscribe']>(
    (contractKey, onState) => client.subscribe(contractKey, onState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const put = useCallback<FreenetClient['put']>(
    (contractKey, state) => client.put(contractKey, state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const get = useCallback<FreenetClient['get']>(
    (contractKey) => client.get(contractKey),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return {
    status,
    isConnected: client.isConnected(),
    subscribe,
    put,
    get,
  };
}
