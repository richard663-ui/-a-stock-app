// argv: hash salt iterations password -> prints "true"/"false" from the edge function code.
const [hash, salt, iterations, password] = process.argv.slice(2);
const env = {ASTOCK_PASSWORD_HASH: hash, ASTOCK_PASSWORD_SALT: salt, ASTOCK_PASSWORD_ITERATIONS: iterations};
globalThis.Deno = {env: {get: name => env[name]}};
const {verifyPassword} = await import('../../supabase/functions/_shared/security.ts');
process.stdout.write(String(await verifyPassword(password)));
