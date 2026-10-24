import { DownOutlined, FileSearchOutlined, LogoutOutlined, SettingOutlined } from '@ant-design/icons'
import { App, Dropdown, Flex, Layout, Typography } from 'antd'
import { useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router'

import { ROLE_LABELS } from '../api/auth.ts'
import { useAuth } from '../auth/useAuth.ts'
import './app.css'
import { useIsMobile } from './useIsMobile.ts'

/** Две буквы для кружка в шапке: «Анна Иванова» — «АИ», без имени — начало логина. */
function initials(name: string, login: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  const letters = words.length >= 2 ? words[0][0] + words[1][0] : (words[0] ?? login).slice(0, 2)
  return letters.toUpperCase()
}

/**
 * Шапка: тёмная полоса со знаком текстом, разделы — вкладками, текущий подчёркнут;
 * меню пользователя с выходом.
 */
export function AppLayout() {
  const { user, logout } = useAuth()
  const { message } = App.useApp()
  const [leaving, setLeaving] = useState(false)
  const location = useLocation()
  const navigate = useNavigate()
  const mobile = useIsMobile()
  const inAdmin = location.pathname.startsWith('/admin')

  const onLogout = async () => {
    setLeaving(true)
    try {
      await logout()
    } catch {
      message.error('Не удалось выйти, попробуйте ещё раз')
      setLeaving(false)
    }
  }

  const sections = [
    { to: '/', label: 'Проверки', active: !inAdmin },
    ...(user?.role === 'admin' ? [{ to: '/admin', label: 'Администрирование', active: inAdmin }] : []),
  ]

  // На телефоне разделы — в том же меню: в строку шапки они не помещаются.
  const userMenu = user && (
    <Dropdown
      trigger={['click']}
      placement="bottomRight"
      menu={{
        items: [
          {
            key: 'me',
            disabled: true,
            label: (
              <Flex vertical gap={2}>
                <Typography.Text strong>{user.name || user.login}</Typography.Text>
                <Typography.Text type="secondary">{ROLE_LABELS[user.role]}</Typography.Text>
              </Flex>
            ),
          },
          { type: 'divider' },
          ...(user.role === 'admin' && mobile
            ? [
                inAdmin
                  ? { key: 'checks', icon: <FileSearchOutlined />, label: 'Проверки' }
                  : { key: 'admin', icon: <SettingOutlined />, label: 'Администрирование' },
              ]
            : []),
          { key: 'logout', icon: <LogoutOutlined />, label: 'Выйти', danger: true, disabled: leaving },
        ],
        onClick: ({ key }) => {
          if (key === 'checks') navigate('/')
          else if (key === 'admin') navigate('/admin')
          else if (key === 'logout') void onLogout()
        },
      }}
    >
      <button type="button" className="z-user" aria-label={`Меню: ${user.name || user.login}`}>
        <span className="z-ava" aria-hidden="true">
          {initials(user.name, user.login)}
        </span>
        {!mobile && <span className="z-user-name">{user.name || user.login}</span>}
        {!mobile && <DownOutlined className="z-user-caret" />}
      </button>
    </Dropdown>
  )

  return (
    <Layout className="z-shell">
      <Layout.Header className={mobile ? 'z-hdr z-hdr-m' : 'z-hdr'}>
        <div className="z-hdr-left">
          <Link to="/" className="z-brand" aria-label="Проверка договоров — к списку проверок">
            Проверка договоров
          </Link>
          {user && !mobile && (
            <nav className="z-nav" aria-label="Разделы">
              {sections.map((section) => (
                <Link
                  key={section.to}
                  to={section.to}
                  className={section.active ? 'z-nav-item is-active' : 'z-nav-item'}
                  aria-current={section.active ? 'page' : undefined}
                >
                  {section.label}
                </Link>
              ))}
            </nav>
          )}
        </div>
        {userMenu}
      </Layout.Header>
      <Layout.Content className={mobile ? 'z-content z-content-m' : 'z-content'}>
        <Outlet />
      </Layout.Content>
    </Layout>
  )
}
