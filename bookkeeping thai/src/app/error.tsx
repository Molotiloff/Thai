"use client";
import { Icon } from '@/components/ui/Icon';
import styles from '@/thai/thai.module.css';
export default function ErrorPage({reset}:{error:Error;reset:()=>void}) {
  return <section className={styles.errorPage} role="alert"><Icon name="alert" size={24}/><h1>Не удалось загрузить данные</h1><p>Проверьте соединение и попробуйте ещё раз. Отчёт появится после полной загрузки данных.</p><button className={styles.button} onClick={reset}><Icon name="refresh" size={15}/>Повторить</button></section>;
}
