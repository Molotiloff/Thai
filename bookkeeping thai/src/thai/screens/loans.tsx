import styles from '../thai.module.css';
import {apiGet} from '../server';
import {rows,type Row} from '../types';
import {Page,Panel,Table,Notice,param,type Params,More} from '../ui';
export default async function Loans({searchParams}:{searchParams:Promise<Params>}){
 const p=await searchParams;const after=param(p,'after_client_id');const client=param(p,'client_id');const query=`?limit=50${after?'&after_client_id='+encodeURIComponent(after):''}${client?'&client_id='+encodeURIComponent(client):''}`;
 const [data,totals]=await Promise.all([apiGet<Row>('/loans'+query),apiGet<Row>('/loans/totals'+(client?'?client_id='+encodeURIComponent(client):''))]);
 return <Page title="Займы" subtitle="Средства, которые обменник занял у клиентов группы «Партнер»."><Notice>Это часть клиентских обязательств. Приём и возврат средств проводятся в кошельке того же клиента.</Notice><Panel title="Остатки по валютам"><Table data={rows(totals.by_instrument)} columns={[{key:'instrument_code',title:'Счёт'},{key:'exchange_owes',title:'Обменник должен',amount:true},{key:'clients_owe',title:'Партнеры должны',amount:true},{key:'balance',title:'Сальдо',amount:true}]}/></Panel><Panel title="Партнеры"><form className={styles.filters}><label>ID клиента <input name="client_id" inputMode="numeric" defaultValue={client}/></label> <button>Показать</button></form><Table data={rows(data.items)} columns={[{key:'client_id',title:'Кошелёк клиента',href:'/clients/{client_id}'},{key:'name',title:'Имя'},{key:'instrument_code',title:'Счёт'},{key:'balance',title:'Баланс',amount:true},{key:'carrying_usdt',title:'Оценка USDT',amount:true}]}/><More href={'/loans'+(client?'?client_id='+client:'')} cursor={data.next_after_client_id} name="after_client_id"/></Panel></Page>
}
