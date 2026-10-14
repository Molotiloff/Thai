import type { IconName } from '@/components/ui/Icon';

/**
 * Типы страницы «Расходы»: две раздельные таблицы, как в Google Sheets —
 * постоянные (категория/сумма/дата/комментарий/город) и переменные
 * (расход/сумма/дата/город).
 */

export type ExpenseCity = 'Екатеринбург' | 'Челябинск' | 'Тюмень' | 'Москва';

export type ExpenseCityFilter = 'Все города' | ExpenseCity;

export type FixedExpenseCategory =
  | 'Аренда'
  | 'Бензин'
  | 'Доставка'
  | 'Такси'
  | 'Инкас члб'
  | 'Инкассация ЧЛБ'
  | 'Инкассация ЧПБ'
  | 'Интернет'
  | 'КВ'
  | 'Билеты'
  | 'Расходники офис';

export interface FixedExpense {
  id: string;
  category: FixedExpenseCategory;
  amount: number;
  date: string;
  comment: string;
  city: ExpenseCity | null;
}

export interface VariableExpense {
  id: string;
  name: string;
  amount: number;
  date: string;
  city: ExpenseCity | null;
}

export interface ExpenseMetric {
  id: string;
  title: string;
  value: number;
  tone: 'blue' | 'orange' | 'green' | 'purple' | 'gray';
  icon: IconName;
}

export type ExpenseTab = 'all' | 'reports';

export interface ExpensesPageData {
  periodLabel: string;
  cities: ExpenseCity[];
  metrics: ExpenseMetric[];
  fixedExpenses: FixedExpense[];
  variableExpenses: VariableExpense[];
  /** Сколько записей осталось за пределами таблиц («Показать ещё N») */
  moreFixedCount: number;
  moreVariableCount: number;
  /** Общее число расходов за период — для пагинации */
  totalCount: number;
}
