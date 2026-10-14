import 'server-only';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { ApiClient, ApiError } from '@/api/httpClient';
import type { Session } from './types';
export const COOKIE = 'thai_access_token';
export function baseUrl(): string {
  const value=process.env.THAI_API_BASE_URL;
  if (!value) throw new Error('Задайте THAI_API_BASE_URL для тайской CRM');
  const url=new URL(value);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !url.pathname.endsWith('/api/v1')) throw new Error('Некорректный THAI_API_BASE_URL');
  return value;
}
export async function apiGet<T>(path: string): Promise<T> {
  const token=(await cookies()).get(COOKIE)?.value;
  if (!token) redirect('/login');
  try { return await new ApiClient({baseUrl:baseUrl(),getToken:()=>token}).get<T>(path,{signal:AbortSignal.timeout(15000)}); }
  catch (error) { if (error instanceof ApiError && error.status===401) redirect('/login'); throw error; }
}
export function session(): Promise<Session> { return apiGet<Session>('/session'); }
export async function optionalGet<T>(path: string): Promise<T | null> {
  try { return await apiGet<T>(path); }
  catch(error) { if (error instanceof ApiError && error.status===422 && typeof error.data==='object' && error.data!==null && 'error' in error.data && String((error.data as {error:{message?:string}}).error.message).includes('не найден')) return null; throw error; }
}
