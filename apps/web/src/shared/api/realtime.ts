import { useEffect, useRef } from 'react';

export type RealtimeResource =
  | 'agents'
  | 'api'
  | 'commands'
  | 'policies'
  | 'sessions';

export interface RealtimeEvent {
  type: 'change';
  resource: RealtimeResource;
  resource_id: string | null;
  action: string;
  path: string;
  occurred_at: string;
}

type Listener = (event: RealtimeEvent) => void;

const listeners = new Set<Listener>();
let socket: WebSocket | null = null;
let reconnectTimer: number | undefined;
let heartbeatTimer: number | undefined;
let reconnectAttempt = 0;

function websocketUrl(): string {
  const url = new URL('/api/v1/ws', window.location.href);
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token =
    typeof window !== 'undefined'
      ? sessionStorage.getItem('eecp_access_token')
      : null;
  if (token) {
    url.searchParams.set('token', token);
  }
  return url.toString();
}

function connect(): void {
  if (
    listeners.size === 0 ||
    socket?.readyState === WebSocket.OPEN ||
    socket?.readyState === WebSocket.CONNECTING
  ) {
    return;
  }

  socket = new WebSocket(websocketUrl());
  socket.onopen = () => {
    reconnectAttempt = 0;
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'ping' }));
      }
    }, 25_000);
  };
  socket.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data) as Partial<RealtimeEvent>;
      if (event.type === 'change' && event.resource) {
        listeners.forEach((listener) => listener(event as RealtimeEvent));
      }
    } catch {
      // Ignore malformed frames; the next valid server event remains usable.
    }
  };
  socket.onerror = () => socket?.close();
  socket.onclose = () => {
    socket = null;
    window.clearInterval(heartbeatTimer);
    if (listeners.size > 0) {
      const delay = Math.min(1000 * 2 ** reconnectAttempt, 15_000);
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(connect, delay);
    }
  };
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  connect();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.clearTimeout(reconnectTimer);
      window.clearInterval(heartbeatTimer);
      socket?.close();
      socket = null;
    }
  };
}

export function useRealtimeRefresh(
  onChange: () => void,
  resources: RealtimeResource[],
): void {
  const callbackRef = useRef(onChange);
  callbackRef.current = onChange;
  const resourceKey = resources.join('|');

  useEffect(() => {
    const acceptedResources = new Set(resourceKey.split('|'));
    let debounceTimer: number | undefined;
    const unsubscribe = subscribe((event) => {
      if (!acceptedResources.has(event.resource)) return;
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(() => callbackRef.current(), 150);
    });
    return () => {
      window.clearTimeout(debounceTimer);
      unsubscribe();
    };
  }, [resourceKey]);
}
