// Подписи и цвета для статусов, критичности и пометок проверки цитат; заголовки и время для экранов проверки.

import type { CheckStatus, CheckSummary, Procurement, QuoteStatus, Risk, Severity, Specialist } from '../../api/checks.ts'

export const STATUS_LABELS: Record<CheckStatus, { text: string; color: string }> = {
  uploaded: { text: 'Загружен', color: 'default' },
  extracting: { text: 'Чтение документа', color: 'processing' },
  extracted: { text: 'Прочитан', color: 'default' },
  analyzing: { text: 'Анализ', color: 'processing' },
  ready: { text: 'Готово', color: 'success' },
  failed: { text: 'Ошибка', color: 'error' },
}

export const PROCUREMENT_OPTIONS: { value: Procurement; label: string }[] = [
  { value: 'unknown', label: 'Не знаю' },
  { value: 'commercial', label: 'Коммерческая сделка' },
  { value: '223fz', label: 'Закупка по 223-ФЗ' },
  { value: '44fz', label: 'Госконтракт по 44-ФЗ' },
]

export const PROCUREMENT_LABELS: Record<Procurement, string> = Object.fromEntries(
  PROCUREMENT_OPTIONS.map((option) => [option.value, option.label]),
) as Record<Procurement, string>

// Цвет — только у критичности: красный, янтарный и нейтральный у низкой.
export const SEVERITY_LABELS: Record<Severity, { text: string; color: string; order: number }> = {
  high: { text: 'Высокая', color: 'error', order: 0 },
  medium: { text: 'Средняя', color: 'warning', order: 1 },
  low: { text: 'Низкая', color: 'default', order: 2 },
}

export const CONFIDENCE_LABELS: Record<Severity, string> = {
  high: 'высокая',
  medium: 'средняя',
  low: 'низкая',
}

export const QUOTE_LABELS: Record<QuoteStatus, { text: string; color: string; hint: string }> = {
  exact: { text: 'Цитата найдена', color: 'success', hint: 'Цитата дословно есть в договоре.' },
  fuzzy: {
    text: 'Цитата неточная',
    color: 'warning',
    hint: 'Похожий текст в договоре есть, но не дословно. Сверьте цитату перед отправкой протокола.',
  },
  not_found: {
    text: 'Цитата не найдена',
    color: 'error',
    hint: 'Такого текста в договоре нет. Проверьте пункт сами, прежде чем включать его в протокол.',
  },
  empty: { text: 'Без цитаты', color: 'default', hint: 'Модель не привела цитату из договора.' },
  new_clause: { text: 'Новый пункт', color: 'default', hint: 'Такого пункта в договоре нет, предлагается добавить.' },
}

/** Справка по договору — то, что раньше было отдельными вкладками. */
export type ReferenceKey = 'obligations' | 'gaps' | 'favorable' | 'specialists'

export const REFERENCE_LABELS: Record<ReferenceKey, string> = {
  obligations: 'Обязательства и сроки',
  gaps: 'Противоречия и недостающее',
  favorable: 'Выгодные условия',
  specialists: 'Проверить специалистам',
}

export const SPECIALIST_LABELS: Record<Specialist, string> = {
  lawyer: 'Юрист',
  finance: 'Финансовый директор',
  production: 'Производство',
  security: 'Безопасность',
}

const MONTHS_GENITIVE = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

export function formatDateTime(value: string): string {
  return new Date(value).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })
}

/** «12:30» — сегодня, иначе «21.10, 12:30». */
export function formatWhen(value: string, now: Date = new Date()): string {
  const moment = new Date(value)
  const time = moment.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  return moment.toDateString() === now.toDateString()
    ? time
    : `${moment.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })}, ${time}`
}

/** Дата договора «2026-09-01» — «01.09.2026», как в протоколе. */
export function shortDate(value: string | null): string {
  if (!value) return ''
  const [year, month, day] = value.split('-')
  return `${day}.${month}.${year}`
}

/** Дата протокола прописью месяца — «22 октября 2026 г.», как в документе. */
export function longDate(value: string | null): string {
  if (!value) return ''
  const [year, month, day] = value.split('-').map(Number)
  return `${day}\u00a0${MONTHS_GENITIVE[month - 1]} ${year}\u00a0г.`
}

/** «удалится через 23 ч 10 мин» — до expires_at. */
export function timeLeft(expiresAt: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.round((new Date(expiresAt).getTime() - now) / 60_000))
  if (minutes < 1) {
    return 'удаляется'
  }
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return hours > 0 ? `через ${hours} ч ${rest} мин` : `через ${rest} мин`
}

/** До удаления меньше часа — пора скачать протокол. */
export function expiresSoon(expiresAt: string, now: number = Date.now()): boolean {
  return new Date(expiresAt).getTime() - now < 60 * 60_000
}

/** «45 с», «2 мин», «1 мин 20 с». */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total} с`
  const minutes = Math.floor(total / 60)
  const rest = total % 60
  return rest > 0 && minutes < 10 ? `${minutes} мин ${rest} с` : `${minutes} мин`
}

/** «1 риск», «3 риска», «8 рисков». */
export function plural(count: number, forms: [string, string, string]): string {
  const tens = count % 100
  const ones = count % 10
  const form = tens >= 11 && tens <= 14 ? forms[2] : ones === 1 ? forms[0] : ones >= 2 && ones <= 4 ? forms[1] : forms[2]
  return `${count} ${form}`
}

/** Какие файлы принимает загрузка договора. */
export const UPLOAD_ACCEPT = '.docx,.docm,.dotx,.doc,.rtf,.pdf'

export function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} КБ`
  }
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`
}

export function clausesText(clauses: string[]): string {
  if (clauses.length === 0) {
    return ''
  }
  return clauses.length === 1 ? `п. ${clauses[0]}` : `пп. ${clauses.join(', ')}`
}

/** «Договор поставки № 15/2026» — когда анализ узнал договор, иначе — имя файла. */
export function checkTitle(check: Pick<CheckSummary, 'contract_kind' | 'contract_number' | 'file_name'>): {
  title: string
  fromContract: boolean
} {
  if (!check.contract_number) return { title: check.file_name, fromContract: false }
  const kind = check.contract_kind ? ` ${check.contract_kind}` : ''
  return { title: `Договор${kind} №\u00a0${check.contract_number}`, fromContract: true }
}

/**
 * Что сейчас происходит — для менеджера: без названия подключения ИИ («Анализ договора: Codex» —
 * «Анализ договора»). Администратору подключение видно.
 */
export function progressText(progress: string | null, isAdmin: boolean): string | null {
  if (!progress) return null
  if (!isAdmin && progress.startsWith('Анализ договора:')) return 'Анализ договора'
  return progress
}

/**
 * Порядок рисков: стоп-факторы, затем пятёрка главных в порядке модели, затем по критичности. Так первые
 * пять обычно и есть пять главных.
 */
export function sortRisks(risks: Risk[], top: string[]): Risk[] {
  const rank = (risk: Risk) => {
    const index = top.indexOf(risk.id)
    return index === -1 ? top.length : index
  }
  return [...risks].sort(
    (a, b) =>
      Number(b.stop_factor) - Number(a.stop_factor) ||
      rank(a) - rank(b) ||
      SEVERITY_LABELS[a.severity].order - SEVERITY_LABELS[b.severity].order,
  )
}
