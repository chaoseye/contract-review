import type { ReactNode } from 'react'

/** Заголовок раздела админки, строка пояснения и кнопки справа. */
export function SectionTitle({ children, extra, sub }: { children: ReactNode; extra?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="z-section-head">
      <div style={{ minWidth: 0 }}>
        <h1 className="z-page-title">{children}</h1>
        {sub && <p className="z-page-sub">{sub}</p>}
      </div>
      {extra}
    </div>
  )
}
