/**
 * Типы формы создания сделки (/deals/new): базовые поля, десять типов
 * операций с собственными наборами полей и итоговый payload.
 */

export type NewDealType =
  | 'sale'
  | 'purchase'
  | 'deposit'
  | 'withdrawal'
  | 'delivery'
  | 'transfer_city'
  | 'client_transfer'
  | 'conversion'
  | 'yuan'
  | 'invoice'
  | 'profit';

export interface BaseDealForm {
  city: string;
  counterpartyId: string;
  /** Процент КТ, например 0.5 */
  counterpartyPercent: number;
  clientId: string | null;
  comment: string;
}

export interface SaleDealFields {
  currency: string;
  /** Вход (курс компании), RUB за единицу */
  companyRate: number;
  quantity: number;
  /** Выход (клиенту), RUB */
  clientAmount: number;
}

export type PurchaseDealFields = SaleDealFields;

/** Внесение / выдача наличных */
export interface CashOperationFields {
  currency: string;
  amount: number;
}

export interface DeliveryDealFields {
  amount: number;
  feePercent: number;
}

export interface TransferCityDealFields {
  fromCity: string;
  toCity: string;
  amount: number;
  feePercent: number;
}

export interface ClientTransferFields {
  toClientId: string;
  currency: string;
  amount: number;
}

export interface ClientTransferRequest {
  fromClientId: number;
  toClientId: number;
  amount: string;
  currency: string;
  idempotencyKey: string;
  comment: string | null;
  allowNegative: boolean;
}

export interface ExchangeDealRequest {
  dealType: 'sale' | 'purchase' | 'conversion';
  clientId: number;
  city: string;
  recvCode: string;
  recvAmount: string;
  payCode: string;
  payAmount: string;
  idempotencyKey: string;
  comment: string | null;
  referrerClientId?: number | null;
  referrerPercent?: number;
  referrerSpreadRub?: string;
}

export interface CashDealRequest {
  dealType: 'deposit' | 'withdrawal';
  clientId: number;
  city: string;
  currency: string;
  amount: string;
  idempotencyKey: string;
  comment: string | null;
  time: string | null;
  contact1: string | null;
  contact2: string | null;
}

export interface ConversionDealFields {
  fromCurrency: string;
  toCurrency: string;
  quantity: number;
  payAmount: number;
}

export interface YuanDealFields {
  amountCny: number;
  rate: number;
}

export interface InvoiceDealFields {
  amount: number;
  currency: string;
  feePercent: number;
}

export interface ProfitDealFields {
  amount: number;
}

export interface CreateDealPayload {
  base: BaseDealForm;
  type: NewDealType;
  fields:
    | SaleDealFields
    | PurchaseDealFields
    | CashOperationFields
    | DeliveryDealFields
    | TransferCityDealFields
    | ClientTransferFields
    | ConversionDealFields
    | YuanDealFields
    | InvoiceDealFields
    | ProfitDealFields;
  calculations: Record<string, number>;
}

/** Справочники формы — приходят из сервиса сделок */
export interface NewDealContext {
  cities: string[];
  counterparties: { id: string; name: string }[];
  clients: { id: string; name: string; chatId?: string }[];
  /** Курсы компании с главной страницы, RUB за единицу */
  companyRates: Record<string, number>;
  defaultCounterpartyPercent: number;
}
