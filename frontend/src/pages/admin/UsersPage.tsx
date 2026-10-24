import { KeyOutlined, LockOutlined, PlusOutlined, UnlockOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Flex, Form, Input, Modal, Popconfirm, Select, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'

import { type AdminUser, createUser, listUsers, resetPassword, updateUser } from '../../api/admin.ts'
import { ROLE_LABELS, type UserRole } from '../../api/auth.ts'
import { useAuth } from '../../auth/useAuth.ts'
import { formatDateTime } from '../../components/check/labels.ts'
import { useIsMobile, useIsNarrow } from '../../components/useIsMobile.ts'
import { errorText } from './format.ts'
import { useAdminContext } from './readiness.ts'
import { SectionTitle } from './SectionTitle.tsx'

const ROLE_OPTIONS = (Object.keys(ROLE_LABELS) as UserRole[]).map((role) => ({ value: role, label: ROLE_LABELS[role] }))
const MIN_PASSWORD = 8

/** Случайный пароль из 14 символов без похожих букв: администратор передаёт его пользователю. */
function generatePassword(): string {
  const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  const bytes = crypto.getRandomValues(new Uint8Array(14))
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join('')
}

interface CreateForm {
  login: string
  name: string
  role: UserRole
  password: string
}

function PasswordInput({ value, onChange }: { value?: string; onChange?: (value: string) => void }) {
  return (
    <Flex gap={8}>
      <Input.Password value={value} onChange={(event) => onChange?.(event.target.value)} autoComplete="new-password" />
      <Button onClick={() => onChange?.(generatePassword())}>Сгенерировать</Button>
    </Flex>
  )
}

/** Пользователи: создание, имя и роль, блокировка, сброс пароля. */
export function UsersPage() {
  const { user: me } = useAuth()
  const { message } = App.useApp()
  const { refresh } = useAdminContext()
  const mobile = useIsMobile()
  const narrow = useIsNarrow()
  const [users, setUsers] = useState<AdminUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const [password, setPassword] = useState<AdminUser | null>(null)
  const [saving, setSaving] = useState(false)
  const [createForm] = Form.useForm<CreateForm>()
  const [editForm] = Form.useForm<{ name: string; role: UserRole }>()
  const [passwordForm] = Form.useForm<{ password: string }>()

  const load = useCallback(async () => {
    try {
      setUsers(await listUsers())
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Не удалось загрузить пользователей'))
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const run = async (action: () => Promise<unknown>, done: string) => {
    setSaving(true)
    try {
      await action()
      message.success(done)
      await load()
      // Число менеджеров видно в «Обзоре».
      refresh()
      return true
    } catch (err) {
      message.error(errorText(err, 'Не получилось, попробуйте ещё раз'))
      return false
    } finally {
      setSaving(false)
    }
  }

  const roleTag = (role: UserRole) => <Tag bordered={role === 'admin'}>{ROLE_LABELS[role]}</Tag>
  const stateTag = (active: boolean) => (active ? <Tag color="success">Активен</Tag> : <Tag color="error">Заблокирован</Tag>)

  // На телефоне роль, состояние и последний вход — под логином, кнопки — столбиком справа.
  const columns: ColumnsType<AdminUser> = [
    {
      title: 'Логин',
      dataIndex: 'login',
      render: (login: string, user) => (
        <Flex vertical gap={2}>
          <Typography.Text strong style={{ wordBreak: 'break-word' }}>
            {login}
          </Typography.Text>
          {user.name && <Typography.Text type="secondary">{user.name}</Typography.Text>}
          {mobile && (
            <Flex gap={4} wrap>
              {roleTag(user.role)}
              {stateTag(user.is_active)}
            </Flex>
          )}
          {narrow && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Последний вход: {user.last_seen_at ? formatDateTime(user.last_seen_at) : '—'}
            </Typography.Text>
          )}
        </Flex>
      ),
    },
    { title: 'Роль', dataIndex: 'role', width: 150, responsive: ['md'], render: roleTag },
    { title: 'Состояние', dataIndex: 'is_active', width: 140, responsive: ['md'], render: stateTag },
    {
      title: 'Последний вход',
      dataIndex: 'last_seen_at',
      width: 150,
      responsive: ['lg'],
      render: (value: string | null) => (value ? formatDateTime(value) : '—'),
    },
    {
      key: 'actions',
      width: mobile ? 150 : 330,
      render: (_: unknown, user) => {
        const self = user.id === me?.id
        return (
          <Flex gap={8} wrap vertical={mobile} align={mobile ? 'stretch' : undefined}>
            <Button
              size="small"
              onClick={() => {
                setEditing(user)
                editForm.setFieldsValue({ name: user.name, role: user.role })
              }}
            >
              Изменить
            </Button>
            <Button
              size="small"
              icon={<KeyOutlined />}
              onClick={() => {
                setPassword(user)
                passwordForm.setFieldsValue({ password: generatePassword() })
              }}
            >
              Пароль
            </Button>
            {user.is_active ? (
              <Popconfirm
                title={`Заблокировать ${user.login}?`}
                description="Пользователь сразу выйдет из системы и не сможет войти."
                okText="Заблокировать"
                cancelText="Отмена"
                okButtonProps={{ danger: true }}
                disabled={self}
                onConfirm={() => run(() => updateUser(user.id, { is_active: false }), 'Пользователь заблокирован')}
              >
                <Button size="small" icon={<LockOutlined />} disabled={self} title={self ? 'Себя заблокировать нельзя' : undefined}>
                  Заблокировать
                </Button>
              </Popconfirm>
            ) : (
              <Button
                size="small"
                icon={<UnlockOutlined />}
                onClick={() => void run(() => updateUser(user.id, { is_active: true }), 'Пользователь разблокирован')}
              >
                Разблокировать
              </Button>
            )}
          </Flex>
        )
      },
    },
  ]

  return (
    <>
      <SectionTitle
        sub="Менеджер проверяет договоры. Администратор тоже может, а ещё настраивает систему."
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              createForm.setFieldsValue({ login: '', name: '', role: 'manager', password: generatePassword() })
              setCreating(true)
            }}
          >
            Новый пользователь
          </Button>
        }
      >
        Пользователи
      </SectionTitle>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
      <Card styles={{ body: { padding: 0 } }}>
        <Table<AdminUser> rowKey="id" columns={columns} dataSource={users ?? []} loading={users === null && !error} pagination={false} />
      </Card>

      <Modal
        title="Новый пользователь"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => createForm.submit()}
        okText="Создать"
        cancelText="Отмена"
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form<CreateForm>
          form={createForm}
          layout="vertical"
          requiredMark={false}
          onFinish={async (values) => {
            if (await run(() => createUser({ ...values, name: values.name ?? '' }), `Пользователь ${values.login} создан`)) {
              setCreating(false)
            }
          }}
        >
          <Form.Item label="Логин" name="login" rules={[{ required: true, message: 'Введите логин' }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="Имя" name="name">
            <Input />
          </Form.Item>
          <Form.Item label="Роль" name="role">
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item
            label="Пароль"
            name="password"
            rules={[{ required: true, min: MIN_PASSWORD, message: `Не короче ${MIN_PASSWORD} символов` }]}
            extra="Передайте пароль пользователю лично. После создания его нельзя посмотреть — только задать новый."
          >
            <PasswordInput />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`Пользователь ${editing?.login ?? ''}`}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
        okText="Сохранить"
        cancelText="Отмена"
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={async (values) => {
            if (editing && (await run(() => updateUser(editing.id, values), 'Сохранено'))) {
              setEditing(null)
            }
          }}
        >
          <Form.Item label="Имя" name="name">
            <Input />
          </Form.Item>
          <Form.Item
            label="Роль"
            name="role"
            extra={editing?.id === me?.id ? 'Свою роль администратор изменить не может.' : undefined}
          >
            <Select options={ROLE_OPTIONS} disabled={editing?.id === me?.id} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`Новый пароль для ${password?.login ?? ''}`}
        open={password !== null}
        onCancel={() => setPassword(null)}
        onOk={() => passwordForm.submit()}
        okText="Задать пароль"
        cancelText="Отмена"
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form
          form={passwordForm}
          layout="vertical"
          onFinish={async ({ password: value }) => {
            if (password && (await run(() => resetPassword(password.id, value), 'Пароль изменён'))) {
              setPassword(null)
            }
          }}
        >
          <Form.Item
            label="Пароль"
            name="password"
            rules={[{ required: true, min: MIN_PASSWORD, message: `Не короче ${MIN_PASSWORD} символов` }]}
            extra="Все сессии пользователя завершатся: ему нужно будет войти с новым паролем."
          >
            <PasswordInput />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
