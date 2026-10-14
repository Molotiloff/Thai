'use client';
export default function ErrorPage({reset}:{error:Error;reset:()=>void}){return <section role="alert"><h1>Не удалось загрузить данные</h1><p>Проверьте соединение с Thai API. Неполный отчёт не показывается.</p><button onClick={reset}>Повторить</button></section>}
