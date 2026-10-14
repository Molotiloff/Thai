import { sameOrigin,browserUrl } from '@/thai/origin';
import { cookies } from 'next/headers';
import { COOKIE } from '@/thai/server';
import { NextRequest,NextResponse } from 'next/server';
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return new NextResponse(null,{status:403});
  (await cookies()).delete(COOKIE);
  return NextResponse.redirect(browserUrl('/login',request),303);
}
