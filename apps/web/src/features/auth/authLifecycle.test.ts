import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { JsxEmit, ModuleKind, ScriptTarget, transpileModule } from 'typescript';

// Run the actual modules with deterministic browser I/O and React hook boundaries.
function loadModule(url: URL, imports: Record<string, unknown>, globals: Record<string, unknown>) {
  const source = transpileModule(readFileSync(url, 'utf8'), {
    compilerOptions: {
      module: ModuleKind.CommonJS, jsx: JsxEmit.React,
      target: ScriptTarget.ES2022, esModuleInterop: true,
    },
  }).outputText;
  const exports: Record<string, any> = {};
  vm.runInNewContext(source, {
    exports,
    require: (name: string) => {
      assert.ok(name in imports, `Unexpected module ${name}`);
      return imports[name];
    },
    ...globals,
  });
  return exports;
}

function browser() {
  const storage = new Map([['eecp_access_token', 'original-token']]);
  const timers = new Map<number, () => void>();
  const intervals = new Map<number, () => void>();
  const events: string[] = [];
  let nextTimer = 0;
  const sessionStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  };
  const window = {
    location: { href: 'https://review.local/', protocol: 'https:' },
    setTimeout: (fn: () => void) => { timers.set(++nextTimer, fn); return nextTimer; },
    clearTimeout: (id: number) => timers.delete(id),
    setInterval: (fn: () => void) => { intervals.set(++nextTimer, fn); return nextTimer; },
    clearInterval: (id: number) => intervals.delete(id),
    dispatchEvent: (event: { type: string }) => events.push(event.type),
    addEventListener: () => {}, removeEventListener: () => {},
  };
  return { storage, sessionStorage, window, timers, intervals, events };
}

function realtimeHarness() {
  const state = browser();
  const sockets: FakeWebSocket[] = [];
  class FakeWebSocket {
    static OPEN = 1;
    static CONNECTING = 0;
    readyState = 0;
    onopen: () => void;
    onclose: (event: { code: number }) => void;
    onmessage: (event: { data: string }) => void;
    onerror: () => void;
    closed = false;
    sent: string[] = [];
    constructor(public url: string) { sockets.push(this); }
    close() { this.closed = true; }
    send(data: string) { this.sent.push(data); }
    open() { this.readyState = 1; this.onopen(); }
  }
  let cleanup: () => void;
  let changes = 0;
  const realtime = loadModule(new URL('../../shared/api/realtime.ts', import.meta.url), {
    react: {
      useEffect: (effect: () => () => void) => { cleanup = effect(); },
      useRef: (value: unknown) => ({ current: value }),
    },
  }, {
    window: state.window, sessionStorage: state.sessionStorage, URL,
    WebSocket: FakeWebSocket,
    CustomEvent: class { constructor(public type: string) {} },
  });
  realtime.useRealtimeRefresh(() => { changes += 1; }, ['sessions']);
  return { ...state, realtime, sockets, cleanup: () => cleanup(), changes: () => changes };
}

test('rotating realtime token ignores late close, error and messages from the old socket', () => {
  const h = realtimeHarness();
  const original = h.sockets[0];
  original.open();
  h.storage.set('eecp_access_token', 'refreshed-token');
  h.realtime.reconnectRealtime();
  const fresh = h.sockets[1];
  assert.equal(new URL(fresh.url).searchParams.get('token'), 'refreshed-token');
  assert.equal(original.closed, true);
  fresh.open();
  original.onclose({ code: 1008 });
  original.onerror();
  original.onmessage({ data: JSON.stringify({ type: 'change', resource: 'sessions' }) });
  assert.equal(h.storage.get('eecp_access_token'), 'refreshed-token');
  assert.deepEqual(h.events, []);
  assert.equal(fresh.closed, false);
  assert.equal(h.timers.size, 0);
  assert.equal(h.intervals.size, 1);
  h.intervals.values().next().value!();
  assert.equal(JSON.parse(fresh.sent[0]).type, 'ping');
  fresh.onmessage({ data: JSON.stringify({ type: 'change', resource: 'sessions' }) });
  h.timers.values().next().value!();
  assert.equal(h.changes(), 1);
  h.cleanup();
});

test('policy rejection of the current socket still signs the user out', () => {
  const h = realtimeHarness();
  h.sockets[0].open();
  h.sockets[0].onclose({ code: 1008 });
  assert.equal(h.storage.has('eecp_access_token'), false);
  assert.deepEqual(h.events, ['eecp:unauthorized']);
  assert.equal(h.timers.size, 0);
  h.cleanup();
});

test('disconnect cancels retry and heartbeat and ignores the delayed close', () => {
  const h = realtimeHarness();
  h.sockets[0].open();
  h.realtime.disconnectRealtime();
  h.sockets[0].onclose({ code: 1008 });
  assert.equal(h.timers.size, 0);
  assert.equal(h.intervals.size, 0);
  assert.deepEqual(h.events, []);
  h.cleanup();
});

async function authHarness(refresh: () => Promise<unknown>) {
  const state = browser();
  const effects: Array<() => unknown> = [];
  let reconnects = 0;
  const user = { id: 'admin', username: 'admin', role: 'ADMIN', session_scope: [] };
  const react = {
    createContext: () => ({ Provider: 'Provider' }),
    useCallback: (fn: unknown) => fn,
    useContext: () => null,
    useEffect: (fn: () => unknown) => effects.push(fn),
    useMemo: (fn: () => unknown) => fn(),
    useRef: (value: unknown) => ({ current: value }),
    useState: (initial: unknown) => [typeof initial === 'function' ? initial() : initial, () => {}],
    createElement: () => null,
  };
  const auth = loadModule(new URL('./authContext.tsx', import.meta.url), {
    react,
    './authApi': {
      fetchMe: async () => ({ authenticated: true, auth_enforced: true, user }),
      refreshToken: refresh,
    },
    './token': {
      getTokenRemainingMs: () => 3600000, isTokenExpired: () => false,
      parseTokenPayload: () => ({ sub: 'admin', role: 'ADMIN' }),
    },
    '@/src/shared/api/realtime': {
      disconnectRealtime: () => {}, reconnectRealtime: () => { reconnects += 1; },
    },
  }, { window: state.window, sessionStorage: state.sessionStorage });
  auth.AuthProvider({ children: null });
  effects[0]();
  await new Promise(resolve => setImmediate(resolve));
  return {
    ...state, reconnects: () => reconnects,
    refresh: state.timers.values().next().value as () => Promise<void>,
  };
}

test('successful refresh migrates realtime to the refreshed token', async () => {
  const h = await authHarness(async () => ({ access_token: 'refreshed-token', user: {} }));
  await h.refresh();
  assert.equal(h.storage.get('eecp_access_token'), 'refreshed-token');
  assert.equal(h.reconnects(), 1);
});

test('a refresh response arriving after logout cannot restore the session', async () => {
  let finish: (value: unknown) => void;
  const h = await authHarness(() => new Promise(resolve => { finish = resolve; }));
  const refreshing = h.refresh();
  h.storage.clear();
  finish!({ access_token: 'refreshed-token', user: {} });
  await refreshing;
  assert.equal(h.storage.has('eecp_access_token'), false);
  assert.equal(h.reconnects(), 0);
});

test('an old refresh failure cannot clear a newly signed-in session', async () => {
  let fail: (error: Error) => void;
  const h = await authHarness(() => new Promise((_, reject) => { fail = reject; }));
  const refreshing = h.refresh();
  h.storage.set('eecp_access_token', 'new-login-token');
  fail!(new Error('old session revoked'));
  await refreshing;
  assert.equal(h.storage.get('eecp_access_token'), 'new-login-token');
});

for (const tokenChanged of [false, true]) {
  test(`an HTTP 401 ${tokenChanged ? 'for an old token preserves the new session' : 'for the current token signs out'}`, async () => {
    const state = browser();
    let respond: (response: Response) => void;
    class ApiError extends Error {
      constructor(message: string, public status: number, public data?: unknown) { super(message); }
    }
    const api = loadModule(new URL('../../shared/api/client.ts', import.meta.url), {
      './config': { API_CONFIG: { BASE_URL: '/api/v1', DEFAULT_TIMEOUT_MS: 1000 } },
      './errors': { ApiError },
    }, {
      window: state.window, sessionStorage: state.sessionStorage,
      AbortController, setTimeout, clearTimeout,
      CustomEvent: class { constructor(public type: string) {} },
      fetch: () => new Promise<Response>(resolve => { respond = resolve; }),
    });
    const request = api.apiClient('/auth/refresh', { method: 'POST' });
    if (tokenChanged) state.storage.set('eecp_access_token', 'new-login-token');
    respond!(new Response(JSON.stringify({ detail: 'invalid or expired token' }), {
      status: 401, headers: { 'content-type': 'application/json' },
    }));
    await assert.rejects(request, { status: 401 });
    assert.equal(state.storage.get('eecp_access_token'), tokenChanged ? 'new-login-token' : undefined);
    assert.deepEqual(state.events, tokenChanged ? [] : ['eecp:unauthorized']);
  });
}
