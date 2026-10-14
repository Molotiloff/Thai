import {ThemeSwitcher} from '@/components/ui/ThemeSwitcher';
import headerStyles from '@/components/deals/DealsHeader.module.css';
import metricStyles from '@/components/deals/DealStatusSummary.module.css';
import {Icon} from '@/components/ui/Icon';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { labels,money,monthNow,text,type Row } from './types';
import styles from './thai.module.css';
export type Params=Record<string,string|string[]|undefined>;
export function param(params: Params,key: string,fallback=''): string { const v=params[key];return typeof v==='string'?v:fallback; }
export function period(params: Params): string { const value=param(params,'period',monthNow()).slice(0,7); if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return monthNow();return value+'-01'; }
export function Page({title,subtitle,children}:{title:string;subtitle:string;children:ReactNode}) {return <div className={styles.page}><header className={headerStyles.header}><div><h1 className={headerStyles.title}>{title}</h1><p className={headerStyles.subtitle}>{subtitle}</p></div><ThemeSwitcher/></header>{children}</div>}
export function Panel({title,children}:{title:string;children:ReactNode}) {return <section className={styles.panel}><h2>{title}</h2>{children}</section>}
export function Metrics({values}:{values:[string,unknown,string?][]}) {return <div className={styles.metrics}>{values.map(([title,value,unit],index)=><div className={metricStyles.card} key={title}><div className={metricStyles.head}><span className={`${metricStyles.iconWrap} ${metricStyles[index%2?'done':'fixed']}`}><Icon name={index%2?'coins':'wallet'} size={14}/></span><span className={metricStyles.label}>{title}</span></div><strong className={metricStyles.count}>{money(value)} <small className={metricStyles.sum}>{unit}</small></strong></div>)}</div>}
export function PeriodFilter({value,extra}:{value:string;extra?:ReactNode}) {return <form className={styles.filters}><label>Месяц<input name="period" type="month" defaultValue={value.slice(0,7)} required/></label>{extra}<button>Показать</button></form>}
export interface Column {key:string;title:string;href?:string;amount?:boolean}
export function Table({data,columns,empty='За выбранный период записей нет'}:{data:Row[];columns:Column[];empty?:string}) {return data.length?<div className={styles.scroll}><table><thead><tr>{columns.map(c=><th key={c.key}>{c.title}</th>)}</tr></thead><tbody>{data.map((r,i)=><tr key={`${String(r.id??r.operation_id??r.instrument_code??i)}:${i}`}>{columns.map(c=>{const value=r[c.key];const content=c.amount?money(value):labels[text(value)]??(typeof value==='boolean'?(value?'Да':'Нет'):text(value));const href=c.href?.replace(/\{(\w+)\}/g,(_,k)=>encodeURIComponent(text(r[k])));return <td key={c.key}>{href&&value!==null&&value!==undefined?<Link className={styles.link} href={href}>{content} ↗</Link>:content}</td>})}</tr>)}</tbody></table></div>:<p className={styles.empty}>{empty}</p>}
export function More({href,cursor,name}:{href:string;cursor:unknown;name:string}) {if(!cursor)return null;const split=href.includes('?')?'&':'?';return <Link className={styles.link} href={`${href}${split}${name}=${encodeURIComponent(String(cursor))}`}>Следующая страница →</Link>}
export const opColumn:Column={key:'operation_id',title:'Операция',href:'/operations/{operation_id}'};
export function Notice({children}:{children:ReactNode}) {return <p className={styles.notice}>{children}</p>}
