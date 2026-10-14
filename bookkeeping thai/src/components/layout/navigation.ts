import type { IconName } from '@/components/ui/Icon';
import type { UserRole } from '@/types/domain';
export interface NavItem {href:string;label:string;icon:IconName;roles:UserRole[]}
const roles:UserRole[]=['cashier','manager','accountant','owner','admin'];
export const NAV_ITEMS:NavItem[]=[
 {href:'/',label:'Главная',icon:'home',roles},
 {href:'/clients',label:'Клиенты',icon:'clients',roles},
 {href:'/deals',label:'Сделки',icon:'deals',roles},
 {href:'/turnover',label:'Оборотка',icon:'pie',roles},
 {href:'/loans',label:'Займы',icon:'wallet',roles},
 {href:'/expenses',label:'Расходы',icon:'expenses',roles},
 {href:'/profit',label:'Прибыль',icon:'accounting',roles},
 {href:'/best-change',label:'BestChange',icon:'deals',roles},
 {href:'/operations',label:'Журнал',icon:'accounting',roles},
];
export function navItemsForRole(role:UserRole){return NAV_ITEMS.filter(item=>item.roles.includes(role));}
