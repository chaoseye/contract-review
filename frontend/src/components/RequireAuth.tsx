import { Navigate, Outlet, useLocation } from 'react-router'

import { useAuth } from '../auth/useAuth.ts'
import { FullScreenSpin } from './FullScreenSpin.tsx'

/** Пускает дальше только после входа; иначе отправляет на /login и запоминает, куда шли. */
export function RequireAuth() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <FullScreenSpin />
  }
  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return <Outlet />
}
