import type {Workspace} from '@/thai/deals/model';
import {DealsHeader,type DealFilters} from './DealsHeader';
import {DealStatusSummaryGrid} from './DealStatusSummary';
import {KanbanBoard} from './KanbanBoard';
import {AllDealsTable} from './AllDealsTable';
export function DealsView({data,canCreate,...filters}:{data:Workspace;canCreate:boolean}&DealFilters){
 return <><DealsHeader {...filters} canCreate={canCreate}/><DealStatusSummaryGrid summaries={data.summaries}/><KanbanBoard data={data} {...filters}/><AllDealsTable data={data} {...filters}/></>;
}
