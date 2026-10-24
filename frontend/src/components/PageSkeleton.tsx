/** Пока грузится раздел — серые плашки в размер страницы вместо спиннера посреди пустого экрана. */
export function PageSkeleton() {
  return (
    <div className="z-skeleton" aria-busy="true" aria-label="Загрузка">
      <span style={{ width: 220, height: 30 }} />
      <span style={{ width: '45%', height: 18 }} />
      <span style={{ height: 64 }} />
      <span style={{ height: 180 }} />
      <span style={{ height: 120 }} />
    </div>
  )
}
