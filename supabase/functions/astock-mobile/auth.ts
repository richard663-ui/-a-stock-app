export const SUPABASE_URL = Deno.env.get('SUPABASE_URL') ?? '';
export const SERVICE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? Deno.env.get('SUPABASE_SERVICE_KEY') ?? '';
export const BRIDGE_ID = 'family-qmt-01';
export const MODEL_VERSION = 'mobile-v9-direction-confidence-macd-calibration';
const PASSWORD_SALT_B64 = 'j1uIbeJ1fb0EqE0f+l7nIg==';
const PASSWORD_HASH_B64 = 'vnR5thLXCEsFVuAz9omEzvCFCHQcc6j1bF3tcU6mzwA=';
const PASSWORD_ITERATIONS = 120000;
const ALLOWED_ORIGINS = new Set(['https://richard663-ui.github.io']);
const enc = new TextEncoder();
function b64ToBytes(s){ return Uint8Array.from(atob(s), c => c.charCodeAt(0)); }
function bytesToB64(a){ const u=a instanceof Uint8Array?a:new Uint8Array(a); let s=''; for(const b of u)s+=String.fromCharCode(b); return btoa(s); }
function b64url(a){ return bytesToB64(a).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_'); }
async function hmac(data){ const key=await crypto.subtle.importKey('raw',enc.encode(SERVICE_KEY),{name:'HMAC',hash:'SHA-256'},false,['sign']); return b64url(await crypto.subtle.sign('HMAC',key,enc.encode(data))); }
export async function makeSession(){ const payload=btoa(JSON.stringify({exp:Date.now()+7*86400000})).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_'); return payload+'.'+await hmac(payload); }
export async function validSessionToken(t){ try{ const [p,s]=t.split('.'); if(!p||!s||s!==await hmac(p))return false; let x=p.replace(/-/g,'+').replace(/_/g,'/'); while(x.length%4)x+='='; return Number(JSON.parse(atob(x)).exp)>Date.now(); }catch{return false;} }
export async function validPassword(password){ try{ const base=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveBits']); const bits=await crypto.subtle.deriveBits({name:'PBKDF2',hash:'SHA-256',salt:b64ToBytes(PASSWORD_SALT_B64),iterations:PASSWORD_ITERATIONS},base,256); return bytesToB64(bits)===PASSWORD_HASH_B64; }catch{return false;} }
export function cors(req){ const origin=req.headers.get('origin')||''; if(!ALLOWED_ORIGINS.has(origin)) return {}; return {'access-control-allow-origin':origin,'access-control-allow-methods':'GET,POST,OPTIONS','access-control-allow-headers':'content-type,x-astock-session','access-control-max-age':'86400','vary':'Origin'}; }
export function json(req,data,status=200){ return new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store',...cors(req)}}); }
export async function rest(path,init={}){ const r=await fetch(`${SUPABASE_URL}/rest/v1/${path}`,{...init,headers:{apikey:SERVICE_KEY,authorization:`Bearer ${SERVICE_KEY}`,'content-type':'application/json',...(init.headers||{})}}); if(!r.ok)throw new Error(`db ${r.status}`); if(r.status===204)return null; const text=await r.text(); return text?JSON.parse(text):null; }
