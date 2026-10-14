import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import Script from 'next/script';
import { headers } from 'next/headers';
import { ThemeProvider } from '@/theme/ThemeProvider';
import { themeInitScript } from '@/theme/config';
import { Sidebar } from '@/components/layout/Sidebar';
import { NAV_ITEMS } from '@/components/layout/navigation';
import type { CurrentUser,UserRole } from '@/types/domain';
import { session } from '@/thai/server';
import styles from './layout.module.css';
import './globals.css';
export const metadata:Metadata={title:'SkyEX · Thai CRM',description:'Касса THB, капитал и прибыль USDT'};
export const dynamic='force-dynamic';
export default async function Layout({children}:{children:ReactNode}){
 const pathname=(await headers()).get('x-crm-pathname')??'';
 let content=children;
 if(pathname!=='/login'){
  const account=await session();
  const role=(['admin','owner','accountant','manager','cashier'].find(r=>account.roles.includes(r))??'cashier') as UserRole;
  const user:CurrentUser={name:account.display_name,role,roleLabel:{admin:'Администратор',owner:'Владелец',accountant:'Бухгалтер',manager:'Менеджер',cashier:'Кассир'}[role],initials:account.display_name.split(' ').map(s=>s[0]).join('').slice(0,2)};
  content=<div className={styles.shell}><Sidebar items={NAV_ITEMS} user={user}/><main id="main-content" className={styles.main}>{children}</main></div>;
 }
 return <html lang="ru" data-theme="dark" suppressHydrationWarning><head><Script id="theme-init" strategy="beforeInteractive">{themeInitScript}</Script></head><body>{pathname!=='/login'&&<a className="skip-link" href="#main-content">К основному содержимому</a>}<ThemeProvider>{content}</ThemeProvider></body></html>
}
