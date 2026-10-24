import { Button, Result } from 'antd'
import { Link, Outlet } from 'react-router'

import { useAuth } from '../auth/useAuth.ts'

/** Админка — только для администратора. Права всё равно проверяет сервер на каждом запросе. */
export function RequireAdmin() {
  const { user } = useAuth()
  if (user?.role !== 'admin') {
    return (
      <Result
        status="403"
        title="Раздел доступен только администратору"
        extra={
          <Link to="/">
            <Button type="primary">К проверкам</Button>
          </Link>
        }
      />
    )
  }
  return <Outlet />
}
