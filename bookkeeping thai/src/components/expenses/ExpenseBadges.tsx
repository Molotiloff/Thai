import { Icon, type IconName } from '@/components/ui/Icon';
import type { ExpenseCity, FixedExpenseCategory } from '@/types/expenses';
import styles from './ExpenseBadges.module.css';

/** Цвет и иконка категории постоянного расхода */
const CATEGORY_META: Record<FixedExpenseCategory, { icon: IconName; className: string }> = {
  Аренда: { icon: 'home', className: styles.catRed },
  Бензин: { icon: 'expenses', className: styles.catAmber },
  Доставка: { icon: 'plane', className: styles.catGreen },
  Такси: { icon: 'deals', className: styles.catAmber },
  'Инкас члб': { icon: 'cash', className: styles.catSlate },
  'Инкассация ЧЛБ': { icon: 'cash', className: styles.catSlate },
  'Инкассация ЧПБ': { icon: 'cash', className: styles.catSlate },
  Интернет: { icon: 'chat', className: styles.catAmber },
  КВ: { icon: 'building', className: styles.catPurple },
  Билеты: { icon: 'file-text', className: styles.catBlue },
  'Расходники офис': { icon: 'paperclip', className: styles.catAmber },
};

export function ExpenseCategoryBadge({ category }: { category: FixedExpenseCategory }) {
  const meta = CATEGORY_META[category];
  return (
    <span className={styles.category}>
      <span className={`${styles.categoryIcon} ${meta.className}`}>
        <Icon name={meta.icon} size={11} />
      </span>
      {category}
    </span>
  );
}

const CITY_CLASS: Record<ExpenseCity, string> = {
  Челябинск: styles.cityGreen,
  Екатеринбург: styles.cityBlue,
  Тюмень: styles.citySlate,
  Москва: styles.cityPurple,
};

export function ExpenseCityBadge({ city }: { city: ExpenseCity | null }) {
  if (!city) {
    return <span className={styles.cityEmpty}>—</span>;
  }
  return (
    <span className={`${styles.city} ${CITY_CLASS[city]}`}>
      {city}
      <Icon name="chevron-down" size={12} className={styles.cityChevron} />
    </span>
  );
}
