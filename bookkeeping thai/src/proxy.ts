import { browserUrl } from '@/thai/origin';
import { NextRequest,NextResponse } from 'next/server';
const publicPaths=new Set(['/login','/api/thai-login','/api/auth/telegram/oidc/start','/api/auth/telegram/oidc/callback']);
export function proxy(request:NextRequest){
 const pathname=request.nextUrl.pathname;const headers=new Headers(request.headers);headers.set('x-crm-pathname',pathname);
 if(!publicPaths.has(pathname)&&!request.cookies.has('thai_access_token')){
  if(pathname.startsWith('/api/'))return NextResponse.json({error:{message:'Войдите в Thai CRM'}},{status:401});
  return NextResponse.redirect(browserUrl('/login',request));
 }
 return NextResponse.next({request:{headers}});
}
export const config={matcher:['/((?!_next/static|_next/image|favicon.ico).*)']};
