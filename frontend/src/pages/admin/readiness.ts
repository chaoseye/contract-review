// Готовность системы к работе — для «Обзора» и отметок в меню админки.

import { useCallback, useEffect, useState } from 'react'
import { useOutletContext } from 'react-router'

import {
  type AdminUser,
  type CodexState,
  getCodex,
  getOrganization,
  listProfiles,
  listUsers,
  type LlmProfile,
  type OrganizationOut,
} from '../../api/admin.ts'

export type ReadyLevel = 'ok' | 'todo' | 'info'
export type ReadyKey = 'llm' | 'organization' | 'users' | 'assets'

export interface ReadyItem {
  key: ReadyKey
  title: string
  detail: string
  level: ReadyLevel
  /** Раздел, где это настраивается. */
  section: 'llm' | 'organization' | 'users'
}

interface Sources {
  organization: OrganizationOut | null
  profiles: LlmProfile[] | null
  codex: CodexState | null
  users: AdminUser[] | null
}

function items({ organization, profiles, codex, users }: Sources): ReadyItem[] {
  const result: ReadyItem[] = []
  if (profiles) {
    const active = profiles.find((profile) => profile.is_active)
    if (!active) {
      result.push({
        key: 'llm',
        title: 'Подключение ИИ',
        detail: 'Нет действующего подключения — договоры не анализируются',
        level: 'todo',
        section: 'llm',
      })
    } else if (active.kind === 'codex_cli' && codex && !codex.logged_in) {
      result.push({
        key: 'llm',
        title: 'Подключение ИИ',
        detail: `Действует «${active.name}», но вход в Codex не выполнен — анализ не пойдёт`,
        level: 'todo',
        section: 'llm',
      })
    } else {
      result.push({ key: 'llm', title: 'Подключение ИИ', detail: `Действует «${active.name}»`, level: 'ok', section: 'llm' })
    }
  }
  if (organization) {
    result.push(
      organization.ready_for_protocol
        ? { key: 'organization', title: 'Организация', detail: 'Полное наименование заполнено', level: 'ok', section: 'organization' }
        : {
            key: 'organization',
            title: 'Организация',
            detail: 'Не заполнено полное наименование — протокол не соберётся',
            level: 'todo',
            section: 'organization',
          },
    )
    const { signature, stamp } = organization.assets
    result.push({
      key: 'assets',
      title: 'Подпись и печать',
      detail:
        signature && stamp
          ? 'Загружены — менеджер ставит их галочками при скачивании'
          : 'Не загружены — протокол выйдет без них, подписать можно вручную',
      level: signature && stamp ? 'ok' : 'info',
      section: 'organization',
    })
  }
  if (users) {
    const managers = users.filter((user) => user.role === 'manager' && user.is_active).length
    result.push(
      managers > 0
        ? { key: 'users', title: 'Менеджеры', detail: `Активных: ${managers}`, level: 'ok', section: 'users' }
        : {
            key: 'users',
            title: 'Менеджеры',
            detail: 'Менеджеров нет — договоры загружает только администратор',
            level: 'info',
            section: 'users',
          },
    )
  }
  return result
}

export interface AdminContext {
  ready: ReadyItem[]
  loading: boolean
  /** Перечитать после изменения в разделе: отметки в меню и «Обзор» обновятся. */
  refresh: () => void
}

/** Загрузка для AdminLayout: организация, подключения, вход в Codex, пользователи. */
export function useReadiness(): AdminContext {
  const [sources, setSources] = useState<Sources>({ organization: null, profiles: null, codex: null, users: null })
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(() => {
    // Каждая часть — сама по себе: ошибка одной не прячет остальные.
    void Promise.allSettled([getOrganization(), listProfiles(), getCodex(), listUsers()]).then(
      ([organization, profiles, codex, users]) => {
        setSources({
          organization: organization.status === 'fulfilled' ? organization.value : null,
          profiles: profiles.status === 'fulfilled' ? profiles.value : null,
          codex: codex.status === 'fulfilled' ? codex.value : null,
          users: users.status === 'fulfilled' ? users.value : null,
        })
        setLoading(false)
      },
    )
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { ready: items(sources), loading, refresh }
}

/** Для разделов админки: готовность и обновление после правок. */
export function useAdminContext(): AdminContext {
  return useOutletContext<AdminContext>()
}
