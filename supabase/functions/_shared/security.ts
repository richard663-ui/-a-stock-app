// Shared authentication, key handling and REST access for the AStock edge functions.
//
// Secrets (set in Supabase Dashboard -> Edge Functions -> Secrets, never in git):
//   ASTOCK_PASSWORD_HASH        base64 PBKDF2-SHA256 hash of the phone access code
//   ASTOCK_PASSWORD_SALT        base64 salt (16+ random bytes)
//   ASTOCK_PASSWORD_ITERATIONS  PBKDF2 iterations (default 600000)
//   ASTOCK_SESSION_SECRET       32+ random bytes (base64) used only to sign phone sessions
//   ASTOCK_SESSION_VERSION      bump to revoke every existing phone session (default "1")
// Database key: SUPABASE_SECRET_KEYS (new sb_secret_ keys, JSON by name; name from
// ASTOCK_SECRET_KEY_NAME, default "default"), falling back to the legacy
// SUPABASE_SERVICE_ROLE_KEY until the legacy keys are switched off.

const enc = new TextEncoder();
export const BRIDGE_ID = 'family-qmt-01';
export const SESSION_DAYS = 7;
const ALLOWED_ORIGINS = new Set(['https://richard663-ui.github.io']);

function env(name: string): string {
  const value = (globalThis as any).Deno?.env?.get(name);
  return typeof value === 'string' ? value.trim() : '';
}

export function supabaseUrl(): string { return env('SUPABASE_URL'); }

export function serviceKey(): string {
  const raw = env('SUPABASE_SECRET_KEYS');
  if (raw) {
    try {
      const key = JSON.parse(raw)[env('ASTOCK_SECRET_KEY_NAME') || 'default'];
      if (typeof key === 'string' && key) return key;
    } catch { /* fall through to the legacy key */ }
  }
  return env('SUPABASE_SERVICE_ROLE_KEY') || env('SUPABASE_SERVICE_KEY');
}

// New sb_secret_/sb_publishable_ keys are not JWTs: sending them as a Bearer
// token is rejected ("Invalid JWT"). Legacy JWT keys need both headers.
export function keyHeaders(key: string): Record<string, string> {
  return key.startsWith('sb_') ? {apikey: key} : {apikey: key, authorization: `Bearer ${key}`};
}

export async function rest(path: string, init: any = {}): Promise<any> {
  const key = serviceKey();
  const r = await fetch(`${supabaseUrl()}/rest/v1/${path}`, {...init,
    headers: {...keyHeaders(key), 'content-type': 'application/json', ...(init.headers || {})}});
  if (!r.ok) throw new Error(`db ${r.status}`);
  if (r.status === 204) return null;
  const text = await r.text();
  return text ? JSON.parse(text) : null;
}

export function cors(req: Request): Record<string, string> {
  const origin = req.headers.get('origin') || '';
  if (!ALLOWED_ORIGINS.has(origin)) return {};
  return {'access-control-allow-origin': origin, 'access-control-allow-methods': 'GET,POST,OPTIONS',
    'access-control-allow-headers': 'content-type,x-astock-session', 'access-control-max-age': '86400', 'vary': 'Origin'};
}

export function json(req: Request, data: unknown, status = 200, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {status,
    headers: {'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...cors(req), ...extra}});
}

// Generic failure response: details go to the function log, never to the client.
export function serverError(req: Request, error: unknown): Response {
  console.error('astock edge error:', error instanceof Error ? error.message : String(error));
  return json(req, {error: 'temporary backend error'}, 500);
}

function b64ToBytes(s: string): Uint8Array { return Uint8Array.from(atob(s), c => c.charCodeAt(0)); }
function bytesToB64(a: ArrayBuffer | Uint8Array): string {
  const u = a instanceof Uint8Array ? a : new Uint8Array(a);
  let s = ''; for (const b of u) s += String.fromCharCode(b); return btoa(s);
}
function b64url(a: ArrayBuffer | Uint8Array | string): string {
  const s = typeof a === 'string' ? btoa(a) : bytesToB64(a);
  return s.replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}
function fromB64url(s: string): string {
  let x = s.replace(/-/g, '+').replace(/_/g, '/'); while (x.length % 4) x += '='; return atob(x);
}

export function constantTimeEqual(a: Uint8Array | string, b: Uint8Array | string): boolean {
  const x = typeof a === 'string' ? enc.encode(a) : a;
  const y = typeof b === 'string' ? enc.encode(b) : b;
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

export function authConfigured(): boolean {
  return Boolean(env('ASTOCK_PASSWORD_HASH') && env('ASTOCK_PASSWORD_SALT') && env('ASTOCK_SESSION_SECRET'));
}

async function hmac(data: string): Promise<string> {
  const secret = env('ASTOCK_SESSION_SECRET');
  if (!secret) throw new Error('ASTOCK_SESSION_SECRET not configured');
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  return b64url(await crypto.subtle.sign('HMAC', key, enc.encode(data)));
}

export async function verifyPassword(password: string): Promise<boolean> {
  const hash = env('ASTOCK_PASSWORD_HASH'), salt = env('ASTOCK_PASSWORD_SALT');
  if (!hash || !salt || !password) return false;
  const iterations = Number(env('ASTOCK_PASSWORD_ITERATIONS') || 600000);
  try {
    const base = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
    const bits = await crypto.subtle.deriveBits({name: 'PBKDF2', hash: 'SHA-256', salt: b64ToBytes(salt), iterations}, base, 256);
    return constantTimeEqual(new Uint8Array(bits), b64ToBytes(hash));
  } catch { return false; }
}

export async function makeSession(now = Date.now()): Promise<string> {
  const payload = b64url(JSON.stringify({exp: now + SESSION_DAYS * 86400000, v: env('ASTOCK_SESSION_VERSION') || '1'}));
  return payload + '.' + await hmac(payload);
}

export async function validSessionToken(token: string, now = Date.now()): Promise<boolean> {
  try {
    const [payload, signature] = token.split('.');
    if (!payload || !signature || !constantTimeEqual(signature, await hmac(payload))) return false;
    const claims = JSON.parse(fromB64url(payload));
    return Number(claims.exp) > now && String(claims.v) === (env('ASTOCK_SESSION_VERSION') || '1');
  } catch { return false; }
}

// ---- login rate limiting (table + function from supabase/migrations/*login_rate_limit.sql)

export function clientAddress(req: Request): string {
  const forwarded = (req.headers.get('x-forwarded-for') || '').split(',')[0].trim();
  return forwarded || req.headers.get('cf-connecting-ip') || req.headers.get('x-real-ip') || 'unknown';
}

export async function loginBucket(req: Request): Promise<string> {
  // Store a keyed hash, never the raw IP address.
  return (await hmac('login:' + clientAddress(req))).slice(0, 32);
}

export async function loginGate(bucket: string, restFn = rest): Promise<{allowed: boolean, retry_after_seconds: number, available: boolean}> {
  try {
    const result = await restFn('rpc/astock_login_gate', {method: 'POST', body: JSON.stringify({p_bucket: bucket})});
    return {allowed: Boolean(result?.allowed), retry_after_seconds: Number(result?.retry_after_seconds || 0), available: true};
  } catch (error) {
    // Fail open with a log line: PBKDF2 cost and a strong access code still apply.
    console.error('login rate limit unavailable:', error instanceof Error ? error.message : String(error));
    return {allowed: true, retry_after_seconds: 0, available: false};
  }
}

export async function recordLoginAttempt(bucket: string, success: boolean, restFn = rest): Promise<void> {
  try {
    await restFn('auth_login_attempts', {method: 'POST', headers: {Prefer: 'return=minimal'},
      body: JSON.stringify({bucket, success})});
  } catch (error) {
    console.error('login attempt not recorded:', error instanceof Error ? error.message : String(error));
  }
}

// One handler for /login used by the phone page.
export async function handleLogin(req: Request, restFn = rest): Promise<Response> {
  if (!authConfigured()) return json(req, {ok: false, error: 'login not configured'}, 503);
  const body = await req.json().catch(() => ({}));
  const bucket = await loginBucket(req);
  const gate = await loginGate(bucket, restFn);
  if (!gate.allowed) {
    return json(req, {ok: false, error: 'too many attempts', retry_after_seconds: gate.retry_after_seconds}, 429,
      {'retry-after': String(gate.retry_after_seconds || 900)});
  }
  const ok = await verifyPassword(String((body as any).password || ''));
  await recordLoginAttempt(bucket, ok, restFn);
  if (!ok) return json(req, {ok: false}, 401);
  return json(req, {ok: true, session: await makeSession()});
}
