import { App as AntApp, ConfigProvider } from 'antd'
import ruRU from 'antd/locale/ru_RU'
import { useMemo } from 'react'
import { BrowserRouter } from 'react-router'

import { App } from './App.tsx'
import { AuthProvider } from './auth/AuthProvider.tsx'
import { useIsMobile, useReducedMotion } from './components/useIsMobile.ts'
import { makeTheme } from './theme.ts'

/** Тема, маршрутизатор и вход — вокруг приложения. */
export function Root() {
  const mobile = useIsMobile()
  const reducedMotion = useReducedMotion()
  const theme = useMemo(() => makeTheme(mobile, reducedMotion), [mobile, reducedMotion])
  return (
    // «Волна» после нажатия кнопки — украшение, которое повторяется десятки раз в день: без неё.
    <ConfigProvider locale={ruRU} theme={theme} wave={{ disabled: true }}>
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  )
}
