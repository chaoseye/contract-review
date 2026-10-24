import { type ReactNode, useCallback, useEffect, useMemo, useState } from 'react'

import { fetchMe, login as apiLogin, logout as apiLogout, type User } from '../api/auth.ts'
import { AuthContext, type AuthState } from './context.ts'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchMe()
      .then((current) => {
        if (!cancelled) setUser(current)
      })
      .catch(() => {
        // Сервер недоступен: показываем вход, повторная попытка будет при входе.
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (loginName: string, password: string) => {
    setUser(await apiLogin(loginName, password))
  }, [])

  const logout = useCallback(async () => {
    try {
      await apiLogout()
    } finally {
      setUser(null)
    }
  }, [])

  const value = useMemo<AuthState>(() => ({ user, loading, login, logout }), [user, loading, login, logout])

  return <AuthContext value={value}>{children}</AuthContext>
}
