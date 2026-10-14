import { NextRequest, NextResponse } from 'next/server';
import { baseUrl, COOKIE } from '@/thai/server';
import { browserUrl } from '@/thai/origin';
import { getTelegramOidcConfig, OIDC_COOKIES } from '../config';
import { attemptCookieName, decodeAttempt, safeEqual } from '../attempt';

export async function GET(request: NextRequest) {
  const oidc = getTelegramOidcConfig();
  if (!oidc) return loginRedirect(browserUrl('/',request).origin,'configuration');
  const code = request.nextUrl.searchParams.get('code');
  const state = request.nextUrl.searchParams.get('state');
  const cookieName = attemptCookieName(state);
  const attempt = decodeAttempt(cookieName ? request.cookies.get(cookieName)?.value : undefined, state);
  // Allow an in-flight login started just before deployment to finish.
  const legacy = !attempt && safeEqual(state, request.cookies.get(OIDC_COOKIES.state)?.value);
  const nonce = attempt?.nonce ?? (legacy ? request.cookies.get(OIDC_COOKIES.nonce)?.value : undefined);
  const verifier = attempt?.verifier ?? (legacy ? request.cookies.get(OIDC_COOKIES.verifier)?.value : undefined);
  const consumedCookies = attempt ? [cookieName!] : legacy ? Object.values(OIDC_COOKIES) : [];
  if (!code || !nonce || !verifier) {
    if (await hasValidSession(request)) {
      const response = NextResponse.redirect(new URL('/',oidc.origin));
      response.headers.set('Cache-Control','no-store');
      return response;
    }
    return loginRedirect(oidc.origin,'state',consumedCookies);
  }
  try {
    const response = await fetch(baseUrl()+'/auth/telegram/oidc',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code,code_verifier:verifier,redirect_uri:oidc.redirectUri,nonce}),
      cache:'no-store',signal:AbortSignal.timeout(15000),
    });
    if (!response.ok) return loginRedirect(oidc.origin,response.status===403?'access':response.status===503?'configuration':'service',consumedCookies);
    const session = await response.json();
    if (typeof session.access_token!=='string' || !session.access_token || !Number.isSafeInteger(session.expires_in) || session.expires_in<=0 || session.expires_in>604800) return loginRedirect(oidc.origin,'service',consumedCookies);
    const redirect = NextResponse.redirect(new URL('/',oidc.origin));
    clearOidcCookies(redirect,consumedCookies);
    redirect.headers.set('Cache-Control','no-store');
    redirect.cookies.set(COOKIE,session.access_token,{
      httpOnly:true,sameSite:'lax',secure:oidc.secureCookies,path:'/',maxAge:session.expires_in,
    });
    return redirect;
  } catch {
    return loginRedirect(oidc.origin,'service',consumedCookies);
  }
}
function clearOidcCookies(response:NextResponse,names:readonly string[]) {
  names.forEach(name=>response.cookies.set(name,'',{path:'/',maxAge:0}));
}
function loginRedirect(origin:string,error:string,consumedCookies:readonly string[]=[]):NextResponse {
  const response=NextResponse.redirect(new URL('/login?error='+error,origin));
  response.headers.set('Cache-Control','no-store');
  clearOidcCookies(response,consumedCookies);
  return response;
}
async function hasValidSession(request:NextRequest):Promise<boolean> {
  const token=request.cookies.get(COOKIE)?.value;
  if(!token)return false;
  try {
    const response=await fetch(baseUrl()+'/session',{
      headers:{Authorization:'Bearer '+token},cache:'no-store',signal:AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch { return false; }
}
