import { ClientsView } from '@/components/clients/ClientsView';
import { clientsService } from '@/services';
import { session } from '@/thai/server';
export default async function ClientsPage(){
 const [data,user]=await Promise.all([clientsService.getClientsPage(),session()]);
 return <ClientsView data={data} session={user}/>;
}
