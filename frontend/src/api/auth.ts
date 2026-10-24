import { ApiError, apiRequest } from './client.ts'

export type UserRole = 'admin' | 'manager'

export interface User {
  id: number
  login: string
  name: string
  role: UserRole
}

export const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Администратор',
  manager: 'Менеджер',
}

export function login(loginName: string, password: string): Promise<User> {
  return apiRequest<User>('/auth/login', { method: 'POST', body: { login: loginName, password } })
}

export function logout(): Promise<void> {
  return apiRequest<void>('/auth/logout', { method: 'POST' })
}

/** Текущий пользователь или null, если вход не выполнен. */
export async function fetchMe(): Promise<User | null> {
  try {
    return await apiRequest<User>('/me')
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null
    }
    throw error
  }
}
