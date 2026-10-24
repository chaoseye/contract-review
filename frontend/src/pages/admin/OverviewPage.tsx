import { CheckOutlined, ExclamationOutlined, InfoOutlined, RightOutlined } from '@ant-design/icons'
import { Link } from 'react-router'

import { useAdminContext } from './readiness.ts'
import { SectionTitle } from './SectionTitle.tsx'

const SECTION_NAMES = { llm: 'Подключения ИИ', organization: 'Организация', users: 'Пользователи' } as const

/**
 * «Обзор»: готова ли система к работе менеджера — подключение ИИ, организация, подпись и печать,
 * менеджеры. Каждый пункт ведёт в свой раздел. Так не теряется шаг вроде «вошли в Codex, но подключения
 * нет».
 */
export function OverviewPage() {
  const { ready, loading } = useAdminContext()
  const todo = ready.filter((item) => item.level === 'todo').length
  return (
    <>
      <SectionTitle
        sub={
          loading
            ? 'Проверяю настройки…'
            : todo > 0
              ? 'Чтобы менеджер проверял договоры и выпускал протоколы, осталось настроить отмеченное.'
              : 'Всё нужное настроено: менеджер может проверять договоры и выпускать протоколы.'
        }
      >
        Обзор
      </SectionTitle>
      <div className="z-ready-list">
        {ready.map((item) => (
          <Link key={item.key} to={`/admin/${item.section}`} className={`z-ready is-${item.level}`}>
            <span className="z-ready-icon" aria-hidden="true">
              {item.level === 'ok' ? <CheckOutlined /> : item.level === 'todo' ? <ExclamationOutlined /> : <InfoOutlined />}
            </span>
            <span>
              <b>{item.title}</b>
              <span className="z-ready-detail">{item.detail}</span>
            </span>
            <span className="z-ready-go">
              <span className="z-ready-go-text">{SECTION_NAMES[item.section]}</span> <RightOutlined aria-hidden="true" />
            </span>
          </Link>
        ))}
      </div>
    </>
  )
}
