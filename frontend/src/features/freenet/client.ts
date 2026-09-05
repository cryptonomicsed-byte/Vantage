/**
 * FreenetClient — wraps the Freenet local node WebSocket API.
 *
 * The local node listens at ws://localhost:50509/contract/command/
 * All methods degrade gracefully when the node is unavailable.
 */

export type FreenetStatus = 'disconnected' | 'connecting' | 'connected' | 'error';

type StateCallback = (state: unknown) => void;

interface FreenetRequest {
  type: string;
  key: string;
  state?: unknown;
}

interface FreenetResponse {
  type: string;
  key?: string;
  state?: unknown;
  error?: string;
}

const FREENET_WS_URL = 'ws://localhost:50509/contract/command/';
const RECONNECT_DELAY_MS = 5_000;

export class FreenetClient {
  private ws: WebSocket | null = null;
  private _status: FreenetStatus = 'disconnected';
  private subscriptions: Map<string, Set<StateCallback>> = new Map();
  private pendingGetResolvers: Map<string, (state: unknown | null) => void> = new Map();
  private pendingPutResolvers: Map<string, (ok: boolean) => void> = new Map();
  private statusListeners: Set<(s: FreenetStatus) => void> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;

  get status(): FreenetStatus {
    return this._status;
  }

  private setStatus(s: FreenetStatus): void {
    this._status = s;
    this.statusListeners.forEach((fn) => fn(s));
  }

  /** Register a listener for status changes. Returns unsubscribe fn. */
  onStatusChange(fn: (s: FreenetStatus) => void): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  /** Attempt connection. Resolves true if connected, false on failure. Never throws. */
  async connect(): Promise<boolean> {
    if (this._status === 'connected' || this._status === 'connecting') {
      return this._status === 'connected';
    }

    return new Promise<boolean>((resolve) => {
      try {
        this.setStatus('connecting');
        const ws = new WebSocket(FREENET_WS_URL);

        const onOpen = () => {
          this.ws = ws;
          this.setStatus('connected');
          cleanup();
          resolve(true);
        };

        const onError = () => {
          this.setStatus('error');
          cleanup();
          this.scheduleReconnect();
          resolve(false);
        };

        const onClose = () => {
          if (this._status !== 'error') {
            this.setStatus('disconnected');
          }
          this.ws = null;
          cleanup();
          this.scheduleReconnect();
          resolve(false);
        };

        const cleanup = () => {
          ws.removeEventListener('open', onOpen);
          ws.removeEventListener('error', onError);
          // Keep message/close listeners after connection for ongoing use.
        };

        ws.addEventListener('open', onOpen);
        ws.addEventListener('error', onError);
        ws.addEventListener('close', onClose);
        ws.addEventListener('message', (evt) => this.handleMessage(evt));
      } catch (err) {
        console.warn('[FreenetClient] connect() threw:', err);
        this.setStatus('error');
        resolve(false);
      }
    });
  }

  /** Clean disconnect. */
  disconnect(): void {
    this.destroyed = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch (_) {
        // ignore
      }
      this.ws = null;
    }
    this.setStatus('disconnected');
  }

  isConnected(): boolean {
    return this._status === 'connected' && this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  /**
   * Subscribe to state updates for a contract key.
   * Returns an unsubscribe function.
   */
  subscribe(contractKey: string, onState: StateCallback): () => void {
    if (!this.subscriptions.has(contractKey)) {
      this.subscriptions.set(contractKey, new Set());
      // Inform node of subscription intent when connected.
      this.sendRaw({ type: 'subscribe', key: contractKey });
    }
    this.subscriptions.get(contractKey)!.add(onState);

    return () => {
      const set = this.subscriptions.get(contractKey);
      if (set) {
        set.delete(onState);
        if (set.size === 0) {
          this.subscriptions.delete(contractKey);
          this.sendRaw({ type: 'unsubscribe', key: contractKey });
        }
      }
    };
  }

  /** Put (update) contract state. Resolves true on success, false on failure. */
  async put(contractKey: string, state: unknown): Promise<boolean> {
    if (!this.isConnected()) {
      console.warn('[FreenetClient] put() called while disconnected, skipping.');
      return false;
    }

    return new Promise<boolean>((resolve) => {
      this.pendingPutResolvers.set(contractKey, resolve);
      const sent = this.sendRaw({ type: 'put', key: contractKey, state });
      if (!sent) {
        this.pendingPutResolvers.delete(contractKey);
        resolve(false);
      }
      // Timeout safety valve
      setTimeout(() => {
        if (this.pendingPutResolvers.has(contractKey)) {
          this.pendingPutResolvers.delete(contractKey);
          console.warn(`[FreenetClient] put(${contractKey}) timed out.`);
          resolve(false);
        }
      }, 10_000);
    });
  }

  /** Get current contract state. Resolves to state or null on failure/timeout. */
  async get(contractKey: string): Promise<unknown | null> {
    if (!this.isConnected()) {
      console.warn('[FreenetClient] get() called while disconnected, returning null.');
      return null;
    }

    return new Promise<unknown | null>((resolve) => {
      this.pendingGetResolvers.set(contractKey, resolve);
      const sent = this.sendRaw({ type: 'get', key: contractKey });
      if (!sent) {
        this.pendingGetResolvers.delete(contractKey);
        resolve(null);
      }
      // Timeout safety valve
      setTimeout(() => {
        if (this.pendingGetResolvers.has(contractKey)) {
          this.pendingGetResolvers.delete(contractKey);
          console.warn(`[FreenetClient] get(${contractKey}) timed out.`);
          resolve(null);
        }
      }, 10_000);
    });
  }

  // -------------------------------------------------------------------------
  // Private helpers
  // -------------------------------------------------------------------------

  private handleMessage(evt: MessageEvent): void {
    let msg: FreenetResponse;
    try {
      msg = JSON.parse(evt.data as string) as FreenetResponse;
    } catch (err) {
      console.warn('[FreenetClient] Received non-JSON message:', evt.data, err);
      return;
    }

    const { type, key, state } = msg;

    switch (type) {
      case 'state_update':
        if (key) {
          const listeners = this.subscriptions.get(key);
          listeners?.forEach((fn) => fn(state ?? null));
        }
        break;

      case 'get_response':
        if (key) {
          const resolver = this.pendingGetResolvers.get(key);
          if (resolver) {
            this.pendingGetResolvers.delete(key);
            resolver(state ?? null);
          }
        }
        break;

      case 'put_response':
        if (key) {
          const resolver = this.pendingPutResolvers.get(key);
          if (resolver) {
            this.pendingPutResolvers.delete(key);
            resolver(!msg.error);
          }
        }
        break;

      default:
        console.warn('[FreenetClient] Unknown message type:', type);
    }
  }

  private sendRaw(req: FreenetRequest): boolean {
    if (!this.isConnected() || !this.ws) return false;
    try {
      this.ws.send(JSON.stringify(req));
      return true;
    } catch (err) {
      console.warn('[FreenetClient] send failed:', err);
      return false;
    }
  }

  private scheduleReconnect(): void {
    if (this.destroyed || this.reconnectTimer !== null) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.destroyed && this._status !== 'connected') {
        this.connect().catch(() => {
          // already handled inside connect()
        });
      }
    }, RECONNECT_DELAY_MS);
  }
}
