export type Row = Record<string, unknown>;
export interface Session { id: number; display_name: string; roles: string[]; permissions: string[] }
export const instruments = [['THB_CASH','Наличный бат'],['THB_BANK','Безналичный бат'],['USDT','USDT'],['USD_WH','Доллар белый'],['USD_BL','Доллар синий'],['EUR','Евро'],['EUR500','Евро 500'],['RUB','Рубль']] as const;
export const participants = [['business','Общая касса'],['skyex','SkyEX'],['pe','ПЕ'],['daniyar','Данияр']] as const;
export const labels: Record<string,string> = {...Object.fromEntries([...instruments,...participants]),balance:'Баланс',partner:'Партнер',draft:'Черновик',ready:'Готова',posted:'Проведена',cancelled:'Отменена',cash:'Касса',wallet:'Кошелёк',debit:'Дебет',credit:'Кредит',trading_income:'Доход от обменов',trading_loss:'Убыток от обменов',fx_income:'Валютный доход',fx_loss:'Валютный убыток',platform_commission_expense:'Комиссия BestChange',operating_expense:'Расходы бизнеса',investor_interest_expense:'Проценты инвесторам',transfer_fee_income:'Комиссия переводов',wallet_deposit:'Приём от клиента',wallet_withdrawal:'Выдача клиенту',fx_trade:'Обмен',best_change:'BestChange',capital_contribution:'Вклад капитала',capital_withdrawal:'Возврат капитала',interest_accrual:'Начисление процентов',interest_payment:'Выплата процентов',expense:'Расход',expense_payment:'Погашение расхода',profit_closure:'Закрытие прибыли',profit_payment:'Выплата собственнику',reversal:'Сторно',capital_agreement:'Договор капитала',capital_term:'Условия капитала',expense_close:'Закрытие расходов',wallet_conversion:'Перевод наличный/безналичный',units_per_usdt:'Единиц за USDT',usdt_per_unit:'USDT за единицу',client_wallet:'Кошелёк клиента'};
export function text(value: unknown): string { return value === null || value === undefined ? '—' : String(value); }
export function money(value: unknown): string {
  const raw = text(value);
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return raw;
  const [integer,fraction] = raw.split('.');
  const decimals = fraction?.replace(/0+$/,'');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g,'\u202f') + (decimals ? ','+decimals : '');
}
export function monthNow(): string { return new Intl.DateTimeFormat('sv-SE',{ timeZone:'Asia/Bangkok',year:'numeric',month:'2-digit' }).format(new Date())+'-01'; }
export function rows(value: unknown): Row[] { return Array.isArray(value) ? value as Row[] : []; }
export function can(session: Session, permission: string): boolean { return session.permissions.includes(permission); }

export function named(data: Row[], parties: Row[]): Row[] {const names=new Map(parties.map(p=>[String(p.party_id),String(p.name)]));return data.map(r=>({...r,debtor_name:names.get(String(r.debtor_party_id)),creditor_name:names.get(String(r.creditor_party_id)),owner_name:names.get(String(r.owner_party_id)),party_name:names.get(String(r.party_id)),payer_name:r.payer_kind==='cash'?'Общая касса':names.get(String(r.payer_party_id))}));}
