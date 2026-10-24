import { Grid } from 'antd'
import { useSyncExternalStore } from 'react'

/**
 * Узкий экран — телефон: уже 768 px, брейкпоинта md у Ant Design.
 * Ширину Ant Design замеряет до отрисовки, поэтому первая картинка уже в нужной раскладке.
 */
export function useIsMobile(): boolean {
  return Grid.useBreakpoint().md === false
}

/** Уже 992 px (брейкпоинт lg): телефон или планшет. Второстепенные колонки таблиц тогда переезжают в первую. */
export function useIsNarrow(): boolean {
  return Grid.useBreakpoint().lg === false
}

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)'

function subscribeReducedMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED_MOTION)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

/** В системе включено «уменьшить движение»: анимации выключаются. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  )
}
