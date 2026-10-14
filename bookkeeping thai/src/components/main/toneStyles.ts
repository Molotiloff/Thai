import type { MetricTone } from '@/types/mainDashboard';
import styles from './tone.module.css';

/** Общий маппинг тонов главной страницы на классы иконок/значений */
export const TONE_ICON_CLASS: Record<MetricTone, string> = {
  blue: styles.iconBlue,
  green: styles.iconGreen,
  red: styles.iconRed,
  orange: styles.iconOrange,
  purple: styles.iconPurple,
  neutral: styles.iconNeutral,
};

export const TONE_TEXT_CLASS: Record<MetricTone, string> = {
  blue: styles.textBlue,
  green: styles.textGreen,
  red: styles.textRed,
  orange: styles.textOrange,
  purple: styles.textPurple,
  neutral: styles.textNeutral,
};
