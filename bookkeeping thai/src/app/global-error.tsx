'use client';
export default function GlobalError({reset}:{error:Error;reset:()=>void}){return <html lang="ru"><body><h1>Thai CRM недоступна</h1><p>Не удалось проверить доступ к тайскому API.</p><button onClick={reset}>Повторить</button><form action="/api/thai-logout" method="post"><button>Выйти</button></form></body></html>}
