import { Alert, Button, Card, Flex, Form, Input, Typography, theme } from 'antd'
import { useState } from 'react'
import { Navigate, useLocation } from 'react-router'

import { ApiError } from '../api/client.ts'
import { useAuth } from '../auth/useAuth.ts'
import { FullScreenSpin } from '../components/FullScreenSpin.tsx'

interface LoginForm {
  login: string
  password: string
}

export function LoginPage() {
  const { user, loading, login } = useAuth()
  const location = useLocation()
  const { token } = theme.useToken()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (loading) {
    return <FullScreenSpin />
  }
  if (user !== null) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from && from !== '/login' ? from : '/'} replace />
  }

  const onFinish = async (values: LoginForm) => {
    setError(null)
    setSubmitting(true)
    try {
      await login(values.login, values.password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось войти, попробуйте ещё раз')
      setSubmitting(false)
    }
  }

  return (
    <Flex justify="center" align="center" style={{ minHeight: '100vh', padding: 16, background: token.colorBgLayout }}>
      <Card style={{ width: '100%', maxWidth: 380 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          Проверка договоров
        </Typography.Title>
        <Typography.Paragraph type="secondary">Анализ договоров с ИИ и протоколы разногласий</Typography.Paragraph>
        {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
        <Form<LoginForm> layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item label="Логин" name="login" rules={[{ required: true, whitespace: true, message: 'Введите логин' }]}>
            <Input autoComplete="username" autoFocus />
          </Form.Item>
          <Form.Item label="Пароль" name="password" rules={[{ required: true, message: 'Введите пароль' }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            Войти
          </Button>
        </Form>
      </Card>
    </Flex>
  )
}
