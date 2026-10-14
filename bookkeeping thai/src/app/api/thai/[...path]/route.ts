import { sameOrigin } from '@/thai/origin';
import { cookies } from 'next/headers';
import { COOKIE,baseUrl } from '@/thai/server';
import { NextRequest, NextResponse } from 'next/server';
const roots=new Set(['clients','client-groups','capital','deals','cash-requests','wallet-transfers','expenses','expense-settlements','expense-operations','expense-periods','profit','best-change','finance']);
async function forward(request: NextRequest, context: { params: Promise<{path:string[]}> }) {
  const {path}=await context.params;
  if (!roots.has(path[0]) || path.some(p=>!/^[-\w]+$/.test(p))) return NextResponse.json({error:{message:'Неизвестный адрес'}},{status:404});
  if (!sameOrigin(request)) return NextResponse.json({error:{message:'Недопустимый источник запроса'}},{status:403});
  const token=(await cookies()).get(COOKIE)?.value;
  if (!token) return NextResponse.json({error:{message:'Войдите в Thai CRM'}},{status:401});
  try {
    const response=await fetch(baseUrl()+'/'+path.join('/')+request.nextUrl.search,{method:request.method,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:request.method==='GET'?undefined:await request.text(),cache:'no-store',signal:AbortSignal.timeout(20000)});
    return new NextResponse(await response.text(),{status:response.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  } catch { return NextResponse.json({error:{message:'Thai API недоступен. Повторите запрос; ключ операции сохранён.'}},{status:503}); }
}
export {forward as GET,forward as POST,forward as PUT};
