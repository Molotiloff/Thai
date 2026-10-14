import type {CurrentUser} from '@/types/domain';
import styles from './UserCard.module.css';
export function UserCard({user}:{user:CurrentUser}) {return <form action="/api/thai-logout" method="post"><button className={styles.card} type="submit" aria-label="Выйти"><span className={styles.avatar}>{user.initials}</span><span className={styles.info}><span className={styles.name}>{user.name}</span><span className={styles.role}>{user.roleLabel} · Выйти</span></span></button></form>}
