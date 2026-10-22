import assert from 'node:assert/strict';

const env = {};
globalThis.Deno = {env: {get: name => env[name]}, serve: () => {}};
const sec = await import('../supabase/functions/_shared/security.ts');

const enc = new TextEncoder();
const b64 = bytes => Buffer.from(bytes).toString('base64');
async function pbkdf2(password, salt, iterations) {
  const base = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({name: 'PBKDF2', hash: 'SHA-256', salt, iterations}, base, 256));
}
const request = (body, ip = '203.0.113.7') => new Request('https://test/login', {method: 'POST',
  headers: {'content-type': 'application/json', 'x-forwarded-for': ip}, body: JSON.stringify(body)});

// Not configured -> login disabled (503), never falls back to a hash in the repository.
assert.equal((await sec.handleLogin(request({password: 'x'}), async () => ({allowed: true}))).status, 503);

const salt = crypto.getRandomValues(new Uint8Array(16));
Object.assign(env, {ASTOCK_PASSWORD_SALT: b64(salt), ASTOCK_PASSWORD_ITERATIONS: '2000',
  ASTOCK_PASSWORD_HASH: b64(await pbkdf2('correct horse battery', salt, 2000)), ASTOCK_SESSION_SECRET: 'unit-test-secret'});
assert.equal(sec.authConfigured(), true);

const recorded = [];
const restOk = async (path, init) => { recorded.push({path, body: init?.body}); return path.startsWith('rpc/') ? {allowed: true} : null; };
assert.equal((await sec.handleLogin(request({password: 'wrong'}), restOk)).status, 401);
const good = await sec.handleLogin(request({password: 'correct horse battery'}), restOk);
assert.equal(good.status, 200);
const token = (await good.json()).session;
assert.equal(await sec.validSessionToken(token), true);
const attempts = recorded.filter(r => r.path === 'auth_login_attempts').map(r => JSON.parse(r.body));
assert.deepEqual(attempts.map(a => a.success), [false, true]);
assert.ok(attempts.every(a => !a.bucket.includes('203.0.113.7')), 'raw IP must not be stored');

// Rate limited -> 429 with Retry-After, password not even checked.
const blocked = await sec.handleLogin(request({password: 'correct horse battery'}),
  async path => (path.startsWith('rpc/') ? {allowed: false, retry_after_seconds: 900} : null));
assert.equal(blocked.status, 429);
assert.equal(blocked.headers.get('retry-after'), '900');

// Rate-limit backend missing -> fails open (logged), password still required.
const origError = console.error; console.error = () => {};
const noGate = async path => { if (path.startsWith('rpc/')) throw new Error('db 404'); return null; };
assert.equal((await sec.handleLogin(request({password: 'wrong'}), noGate)).status, 401);
console.error = origError;

// Session integrity: tampering, expiry and version bumps invalidate tokens.
const [payload, signature] = token.split('.');
assert.equal(await sec.validSessionToken(payload + '.' + signature.slice(0, -2) + 'AA'), false);
assert.equal(await sec.validSessionToken(token, Date.now() + 8 * 86400000), false);
env.ASTOCK_SESSION_VERSION = '2';
assert.equal(await sec.validSessionToken(token), false, 'bumping the version revokes old sessions');
delete env.ASTOCK_SESSION_VERSION;
env.ASTOCK_SESSION_SECRET = 'rotated';
assert.equal(await sec.validSessionToken(token), false);

// Key handling: new secret keys only in apikey; legacy JWT keys keep Bearer.
assert.deepEqual(sec.keyHeaders('sb_secret_abc'), {apikey: 'sb_secret_abc'});
assert.equal(sec.keyHeaders('eyJlegacy').authorization, 'Bearer eyJlegacy');
env.SUPABASE_SERVICE_ROLE_KEY = 'eyJlegacy';
assert.equal(sec.serviceKey(), 'eyJlegacy');
env.SUPABASE_SECRET_KEYS = JSON.stringify({default: 'sb_secret_new', other: 'sb_secret_other'});
assert.equal(sec.serviceKey(), 'sb_secret_new');
env.ASTOCK_SECRET_KEY_NAME = 'other';
assert.equal(sec.serviceKey(), 'sb_secret_other');

// Errors never leak internals to the phone.
console.error = () => {};
const err = await (sec.serverError(new Request('https://test/x'), new Error('db 500 secret detail'))).json();
console.error = origError;
assert.deepEqual(err, {error: 'temporary backend error'});
assert.equal(sec.constantTimeEqual('abc', 'abc'), true);
assert.equal(sec.constantTimeEqual('abc', 'abd'), false);
assert.equal(sec.constantTimeEqual('abc', 'abcd'), false);
console.log('Mobile authentication, rate limiting and key handling tests PASS');
