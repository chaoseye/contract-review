import {
  ApiOutlined,
  BankOutlined,
  DashboardOutlined,
  EditOutlined,
  FileTextOutlined,
  HistoryOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Flex, Menu, Select } from 'antd'
import type { ReactNode } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router'

import { useIsMobile } from '../../components/useIsMobile.ts'
import './admin.css'
import { type ReadyKey, useReadiness } from './readiness.ts'

interface Section {
  key: string
  icon: ReactNode
  label: string
  /** Чья готовность отмечается точкой у раздела. */
  ready?: ReadyKey[]
}

const OVERVIEW: Section = { key: 'overview', icon: <DashboardOutlined />, label: 'Обзор' }
// Сначала то, без чего анализ и протокол не работают, потом — тонкая настройка.
const GROUPS: { title: string; sections: Section[] }[] = [
  {
    title: 'Чтобы анализ работал',
    sections: [
      { key: 'llm', icon: <ApiOutlined />, label: 'Подключения ИИ', ready: ['llm'] },
      { key: 'organization', icon: <BankOutlined />, label: 'Организация', ready: ['organization'] },
      { key: 'users', icon: <TeamOutlined />, label: 'Пользователи' },
    ],
  },
  {
    title: 'Тонкая настройка',
    sections: [
      { key: 'prompt', icon: <EditOutlined />, label: 'Промпт' },
      { key: 'template', icon: <FileTextOutlined />, label: 'Шаблон протокола' },
      { key: 'settings', icon: <SettingOutlined />, label: 'Настройки и перенос' },
      { key: 'audit', icon: <HistoryOutlined />, label: 'Журнал' },
    ],
  },
]

/**
 * Админка: «Обзор» готовности и разделы слева, содержимое справа (ARCHITECTURE.md, раздел 7.2).
 * На телефоне разделы — выпадающий список над содержимым.
 */
export function AdminLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const mobile = useIsMobile()
  const context = useReadiness()
  const current = location.pathname.split('/')[2] || 'overview'
  const okCount = context.ready.filter((item) => item.level === 'ok').length
  const needs = new Set(context.ready.filter((item) => item.level === 'todo').map((item) => item.key))

  const label = (section: Section) => {
    const attention = section.ready?.some((key) => needs.has(key))
    return (
      <span className="z-nav-label">
        {section.label}
        {section.key === 'overview' && context.ready.length > 0 && (
          <span className="z-nav-count">
            {okCount} из {context.ready.length}
          </span>
        )}
        {attention && <span className="z-nav-dot" role="img" aria-label="требует внимания" />}
      </span>
    )
  }

  if (mobile) {
    return (
      <>
        <Select
          value={current}
          onChange={(key: string) => navigate(`/admin/${key}`)}
          options={[
            { value: OVERVIEW.key, label: label(OVERVIEW) },
            ...GROUPS.map((group) => ({
              label: group.title,
              title: group.title,
              options: group.sections.map((section) => ({
                value: section.key,
                label: (
                  <Flex gap={8} align="center">
                    {section.icon}
                    {label(section)}
                  </Flex>
                ),
              })),
            })),
          ]}
          style={{ width: '100%', marginBottom: 16 }}
          size="large"
          aria-label="Раздел администрирования"
        />
        <Outlet context={context} />
      </>
    )
  }

  return (
    <div className="z-admin">
      <nav className="z-admin-nav" aria-label="Разделы администрирования">
        <Menu
          mode="inline"
          selectedKeys={[current]}
          items={[
            { key: OVERVIEW.key, icon: OVERVIEW.icon, label: label(OVERVIEW) },
            ...GROUPS.map((group) => ({
              type: 'group' as const,
              key: group.title,
              label: group.title,
              children: group.sections.map((section) => ({ key: section.key, icon: section.icon, label: label(section) })),
            })),
          ]}
          onClick={({ key }) => navigate(`/admin/${key}`)}
        />
      </nav>
      <div className="z-admin-main">
        <Outlet context={context} />
      </div>
    </div>
  )
}
