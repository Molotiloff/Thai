'use client';
import Link from 'next/link';
import {Icon} from '@/components/ui/Icon';
import {ThemeSwitcher} from '@/components/ui/ThemeSwitcher';
import {STATUSES,OPERATIONS} from '@/thai/deals/model';
import header from './DealsHeader.module.css';
import styles from '@/thai/deals/deals.module.css';
export interface DealFilters {query:string;status:string;kind:string;limit:string;}
export function DealsHeader({query,status,kind,limit,canCreate}:DealFilters&{canCreate:boolean}){
 return (    <header className={header.header}>
      <div><h1 className={header.title}>Сделки</h1><p className={header.subtitle}>Управление текущими сделками</p></div>
      <div className={header.controls}>
        {canCreate&&<Link href="/deals/new" className={header.createButton}><Icon name="plus" size={15}/>Новая сделка</Link>}
        <form action="/deals" className={styles.filters}>
          <input name="query" defaultValue={query} placeholder="Поиск по клиенту или ID" aria-label="Поиск по сделкам"/>
          <select name="kind" defaultValue={kind} aria-label="Тип сделки"><option value="">Все операции</option>{OPERATIONS.map(o=><option key={o.type} value={o.type}>{o.label}</option>)}</select>
          <select name="status" defaultValue={status} aria-label="Статус сделки"><option value="">Все статусы</option>{STATUSES.map(s=><option key={s.code} value={s.code}>{s.label}</option>)}</select>
          <input type="hidden" name="limit" value={limit}/><button className={header.iconButton} aria-label="Применить фильтры"><Icon name="sliders" size={16}/></button>
        </form><ThemeSwitcher/>
      </div>
    </header>
 );
}
