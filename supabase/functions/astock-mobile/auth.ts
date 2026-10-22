// Phone API authentication now lives in ../_shared/security.ts so that every
// endpoint (astock-mobile, astock-micro) validates sessions the same way and
// no password hash or signing key is stored in the repository.
export {BRIDGE_ID, cors, json, rest, makeSession, validSessionToken, handleLogin, serverError}
  from '../_shared/security.ts';
export const MODEL_VERSION = 'mobile-v10-exchange-window-prospective';
