import Link from 'next/link';
import {Icon} from '@/components/ui/Icon';
import {ThemeSwitcher} from '@/components/ui/ThemeSwitcher';
import styles from './NewDealHeader.module.css';
export function NewDealHeader({formId,disabled}:{formId:string;disabled:boolean}){
 return <header className={styles.header}><div className={styles.titleGroup}><Link href="/deals" className={styles.backButton} aria-label="Назад к сделкам"><Icon name="arrow-left" size={17}/></Link><div><h1 className={styles.title}>Новая сделка</h1><p className={styles.subtitle}>Создание новой сделки</p></div></div><div className={styles.controls}><Link className={styles.cancelButton} href="/deals">Отмена</Link><button type="submit" form={formId} className={styles.submitButton} disabled={disabled}>Добавить сделку</button><ThemeSwitcher/></div></header>;
}
