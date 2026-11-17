export interface ParsedTokenPayload {
  sub?: string;
  role?: 'ADMIN' | 'EXAMINER';
  name?: string;
  uid?: string;
  jti?: string;
  sessions?: string[];
  iat?: number;
  exp?: number;
}

export function parseTokenPayload(token: string): ParsedTokenPayload | null {
  try {
    const [encoded] = token.split('.');
    if (!encoded) return null;
    const base64 = encoded.replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const bytes = Uint8Array.from(atob(padded), (ch) => ch.charCodeAt(0));
    const json = new TextDecoder().decode(bytes);
    return JSON.parse(json) as ParsedTokenPayload;
  } catch {
    return null;
  }
}

export function isTokenExpired(token: string, skewSeconds = 10): boolean {
  const payload = parseTokenPayload(token);
  if (!payload || typeof payload.exp !== 'number') {
    return true;
  }
  const nowSeconds = Math.floor(Date.now() / 1000);
  return nowSeconds + skewSeconds >= payload.exp;
}

export function getTokenRemainingMs(token: string): number {
  const payload = parseTokenPayload(token);
  if (!payload || typeof payload.exp !== 'number') {
    return 0;
  }
  return Math.max(0, payload.exp * 1000 - Date.now());
}

