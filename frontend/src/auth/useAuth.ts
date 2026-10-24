import { useContext } from 'react'

import { AuthContext, type AuthState } from './context.ts'

export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (value === null) {
    throw new Error('useAuth используется вне AuthProvider')
  }
  return value
}
