import { createContext } from 'react'

import type { User } from '../api/auth.ts'

export interface AuthState {
  user: User | null
  /** Идёт первая проверка сессии при открытии страницы. */
  loading: boolean
  login: (loginName: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthState | null>(null)
