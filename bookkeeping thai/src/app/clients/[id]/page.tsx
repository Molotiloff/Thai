import { notFound } from 'next/navigation';
import { ClientsView } from '@/components/clients/ClientsView';
import { clientsService } from '@/services';
import { session } from '@/thai/server';
import { OperationForms } from '@/thai/forms';
import { walletForms } from '@/thai/form-specs';
export default async function ClientPage({params}:{params:Promise<{id:string}>}){
 const {id}=await params;if(!/^[1-9]\d*$/.test(id))notFound();
 const [data,user]=await Promise.all([clientsService.getClientsPage(),session()]);
 if(!data.clients.some(c=>c.id===id))notFound();
 return <><ClientsView data={data} session={user} initialClientId={id}/><OperationForms specs={walletForms(id)} session={user}/></>;
}
