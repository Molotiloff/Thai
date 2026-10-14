import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import Module from 'node:module';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
const root=fileURLToPath(new URL('..',import.meta.url));
const requireProject=Module.createRequire(path.join(root,'package.json'));
const ts=requireProject('typescript');
const {NextRequest}=requireProject('next/server');
const cache=new Map();
function load(file){
 if(cache.has(file))return cache.get(file).exports;
 const m=new Module(file);cache.set(file,m);m.filename=file;
 m.require=(name)=>{
  if(name==='@/thai/server')return {baseUrl:()=> 'http://backend/api/v1',COOKIE:'thai_access_token'};
  if(name==='@/thai/origin')return load(path.join(root,'src/thai/origin.ts'));
  if(name.startsWith('.'))return load(path.resolve(path.dirname(file),name+'.ts'));
  return requireProject(name);
 };
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,file);
 return m.exports;
}
process.env.THAI_TELEGRAM_OIDC_CLIENT_ID='12345';
process.env.THAI_FRONTEND_ORIGIN='https://crm.example.org';
const base=path.join(root,'src/app/api/auth/telegram/oidc');
const {GET:start}=load(path.join(base,'start/route.ts'));
const {GET:callback}=load(path.join(base,'callback/route.ts'));
const helpers=load(path.join(base,'attempt.ts'));
const jar=new Map();
function req(url){return new NextRequest(url,{headers:{host:new URL(url).host,cookie:[...jar].map(([k,v])=> k+'='+v).join('; ')}});}
function apply(r){for(const c of r.cookies.getAll()){if(c.maxAge===0)jar.delete(c.name);else jar.set(c.name,c.value);}}
function begin(){const r=start(req('https://crm.example.org/api/auth/telegram/oidc/start'));apply(r);return new URL(r.headers.get('location')).searchParams;}
function callbackRequest(state,code='test-code'){return req('https://crm.example.org/api/auth/telegram/oidc/callback?'+new URLSearchParams({state,code}));}
let bodies=[],sessionValid=true;
global.fetch=async(url,options)=>{
 if(url.endsWith('/session'))return new Response('{}',{status:sessionValid?200:401});
 bodies.push(JSON.parse(options.body));
 return new Response(JSON.stringify({access_token:'mock-session',expires_in:3600}),{status:200});
};
(async()=>{
 const canonical=start(req('http://127.0.0.1:3000/api/auth/telegram/oidc/start'));
 assert.equal(canonical.headers.get('location'),'https://crm.example.org/api/auth/telegram/oidc/start');
 assert.equal(canonical.cookies.getAll().length,0);
 const a=begin(),b=begin();assert.equal(jar.size,2);
 const forged=await callback(callbackRequest('forged-state'));apply(forged);
 assert.equal(new URL(forged.headers.get('location')).searchParams.get('error'),'state');
 assert.equal(jar.size,2);assert.equal(bodies.length,0);
 const ra=await callback(callbackRequest(a.get('state')));apply(ra);
 assert.equal(ra.headers.get('location'),'https://crm.example.org/');
 assert(jar.has(helpers.attemptCookieName(b.get('state'))));
 assert(!jar.has(helpers.attemptCookieName(a.get('state'))));
 assert.equal(bodies[0].nonce,a.get('nonce'));
 assert.equal(crypto.createHash('sha256').update(bodies[0].code_verifier).digest('base64url'),a.get('code_challenge'));
 const replay=await callback(callbackRequest(a.get('state')));assert.equal(replay.headers.get('location'),'https://crm.example.org/');
 assert.equal(bodies.length,1);
 const rb=await callback(callbackRequest(b.get('state')));apply(rb);
 assert.equal(rb.headers.get('location'),'https://crm.example.org/');assert.equal(jar.size,1);
 assert.equal(bodies[1].nonce,b.get('nonce'));
 sessionValid=false;
 const expired=await callback(callbackRequest(a.get('state')));
 assert.equal(new URL(expired.headers.get('location')).searchParams.get('error'),'state');
 jar.clear();const active=begin();
 for(let i=0;i<12;i++)begin();
 assert.equal(jar.size,8);
 assert(!jar.has(helpers.attemptCookieName(active.get('state'))));
 const validState=crypto.randomBytes(32).toString('base64url');
 assert.equal(helpers.decodeAttempt('garbage',validState),null);
 assert.equal(helpers.attemptCookieName('../bad-cookie'),null);
 jar.clear();const failureA=begin(),failureB=begin();
 global.fetch=async()=>new Response('{}',{status:401});
 const rejected=await callback(callbackRequest(failureA.get('state')));apply(rejected);
 assert.equal(new URL(rejected.headers.get('location')).searchParams.get('error'),'service');
 assert(jar.has(helpers.attemptCookieName(failureB.get('state'))));
 assert(!jar.has(helpers.attemptCookieName(failureA.get('state'))));
 global.fetch=async()=>new Response(JSON.stringify({access_token:'mock-session',expires_in:3600}),{status:200});
 jar.clear();const oldState=crypto.randomBytes(32).toString('base64url');
 jar.set('thai_oidc_state',oldState);jar.set('thai_oidc_nonce',crypto.randomBytes(32).toString('base64url'));jar.set('thai_oidc_verifier',crypto.randomBytes(48).toString('base64url'));
 const legacy=await callback(callbackRequest(oldState));apply(legacy);
 assert.equal(legacy.headers.get('location'),'https://crm.example.org/');assert.equal(jar.size,1);
 console.log('PASS: parallel logins, PKCE/nonce, isolated cleanup, forged state, verified session replay, expired session, canonical host, bounded cookies, malformed payload, legacy transition');
})().catch(e=>{console.error(e);process.exitCode=1});
