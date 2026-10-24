import { lazy, type ReactNode, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router'

import { AppLayout } from './components/AppLayout.tsx'
import { PageSkeleton } from './components/PageSkeleton.tsx'
import { RequireAdmin } from './components/RequireAdmin.tsx'
import { RequireAuth } from './components/RequireAuth.tsx'
import { ChecksPage } from './pages/ChecksPage.tsx'
import { LoginPage } from './pages/LoginPage.tsx'
import { NotFoundPage } from './pages/NotFoundPage.tsx'

// Страница проверки (риски, протокол) — отдельный модуль: список проверок открывается быстрее.
const CheckPage = lazy(() => import('./pages/CheckPage.tsx').then((module) => ({ default: module.CheckPage })))
// Админка нужна только администратору — тоже отдельными модулями.
const AdminLayout = lazy(() => import('./pages/admin/AdminLayout.tsx').then((module) => ({ default: module.AdminLayout })))
const OverviewPage = lazy(() => import('./pages/admin/OverviewPage.tsx').then((module) => ({ default: module.OverviewPage })))
const UsersPage = lazy(() => import('./pages/admin/UsersPage.tsx').then((module) => ({ default: module.UsersPage })))
const PromptPage = lazy(() => import('./pages/admin/PromptPage.tsx').then((module) => ({ default: module.PromptPage })))
const LlmPage = lazy(() => import('./pages/admin/LlmPage.tsx').then((module) => ({ default: module.LlmPage })))
const OrganizationPage = lazy(() =>
  import('./pages/admin/OrganizationPage.tsx').then((module) => ({ default: module.OrganizationPage })),
)
const TemplatePage = lazy(() => import('./pages/admin/TemplatePage.tsx').then((module) => ({ default: module.TemplatePage })))
const SettingsPage = lazy(() => import('./pages/admin/SettingsPage.tsx').then((module) => ({ default: module.SettingsPage })))
const AuditPage = lazy(() => import('./pages/admin/AuditPage.tsx').then((module) => ({ default: module.AuditPage })))

// Пока грузится модуль — скелет страницы, а не спиннер посреди пустого экрана.
function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageSkeleton />}>{children}</Suspense>
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route index element={<ChecksPage />} />
          <Route
            path="checks/:id"
            element={
              <Lazy>
                <CheckPage />
              </Lazy>
            }
          />
          <Route element={<RequireAdmin />}>
            <Route
              path="admin"
              element={
                <Lazy>
                  <AdminLayout />
                </Lazy>
              }
            >
              <Route index element={<Navigate to="overview" replace />} />
              <Route path="overview" element={<Lazy><OverviewPage /></Lazy>} />
              <Route path="users" element={<Lazy><UsersPage /></Lazy>} />
              <Route path="prompt" element={<Lazy><PromptPage /></Lazy>} />
              <Route path="llm" element={<Lazy><LlmPage /></Lazy>} />
              <Route path="organization" element={<Lazy><OrganizationPage /></Lazy>} />
              <Route path="template" element={<Lazy><TemplatePage /></Lazy>} />
              <Route path="settings" element={<Lazy><SettingsPage /></Lazy>} />
              <Route path="audit" element={<Lazy><AuditPage /></Lazy>} />
            </Route>
          </Route>
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  )
}
