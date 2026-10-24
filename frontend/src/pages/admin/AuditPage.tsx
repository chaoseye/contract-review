import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Flex, Input, Row, Select, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'

import {
  type AdminUser,
  type AuditFilters,
  type AuditItem,
  type AuditPage as AuditPageData,
  downloadAuditCsv,
  listAudit,
  listUsers,
} from '../../api/admin.ts'
import { formatDateTime } from '../../components/check/labels.ts'
import { errorText, money, tokens } from './format.ts'
import { SectionTitle } from './SectionTitle.tsx'

const PAGE_SIZE = 50

interface FilterForm {
  from: string
  to: string
  userId: number | null
  actions: string[]
  checkId: string
}

/** Начало дня по часовому поясу браузера — в ISO с поясом; to — начало следующего дня. */
function dayStart(value: string, nextDay = false): string | undefined {
  if (!value) return undefined
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day + (nextDay ? 1 : 0)).toISOString()
}

function toQuery(form: FilterForm): AuditFilters {
  return {
    at_from: dayStart(form.from),
    at_to: dayStart(form.to, true),
    user_id: form.userId ?? undefined,
    action: form.actions,
    check_id: form.checkId.trim() || undefined,
  }
}

function yesNo(value: boolean | null): string {
  return value === null ? '' : value ? 'да' : 'нет'
}

/** Журнал: кто, когда и что делал, с подписью или без, модель, токены и стоимость. */
export function AuditPage() {
  const { message } = App.useApp()
  const [filters, setFilters] = useState<FilterForm>({ from: '', to: '', userId: null, actions: [], checkId: '' })
  const [applied, setApplied] = useState<FilterForm>(filters)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<AuditPageData | null>(null)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await listAudit(toQuery(applied), PAGE_SIZE, (page - 1) * PAGE_SIZE))
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Не удалось загрузить журнал'))
    } finally {
      setLoading(false)
    }
  }, [applied, page])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  useEffect(() => {
    listUsers()
      .then(setUsers)
      .catch(() => setUsers([]))
  }, [])

  const actionOptions = useMemo(
    () => Object.entries(data?.actions ?? {}).map(([value, label]) => ({ value, label })),
    [data?.actions],
  )

  const csv = async () => {
    setDownloading(true)
    try {
      await downloadAuditCsv(toQuery(applied))
    } catch (err) {
      message.error(errorText(err, 'Не удалось выгрузить журнал'))
    } finally {
      setDownloading(false)
    }
  }

  const columns: ColumnsType<AuditItem> = [
    { title: 'Когда', dataIndex: 'at', width: 130, render: (value: string) => formatDateTime(value) },
    { title: 'Кто', dataIndex: 'login', width: 110, render: (value: string | null) => value ?? '—' },
    {
      title: 'Действие',
      dataIndex: 'action_label',
      width: 220,
      render: (label: string, item) => (
        <Flex vertical gap={2}>
          <span>{label}</span>
          {item.details && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {item.details}
            </Typography.Text>
          )}
        </Flex>
      ),
    },
    {
      title: 'Договор',
      key: 'contract',
      render: (_: unknown, item) =>
        item.counterparty || item.contract_number ? (
          <Flex vertical gap={2}>
            <span>{item.counterparty ?? '—'}</span>
            {item.contract_number && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                № {item.contract_number}
              </Typography.Text>
            )}
          </Flex>
        ) : item.check_id ? (
          <Link to={`/checks/${item.check_id}`}>{item.check_id.slice(0, 8)}</Link>
        ) : (
          '—'
        ),
    },
    {
      title: 'Подпись / печать',
      key: 'sign',
      width: 120,
      render: (_: unknown, item) =>
        item.with_signature === null && item.with_stamp === null ? '' : `${yesNo(item.with_signature)} / ${yesNo(item.with_stamp)}`,
    },
    { title: 'Модель', dataIndex: 'model', width: 170, render: (value: string | null) => value ?? '' },
    { title: 'Токены', key: 'tokens', width: 120, render: (_: unknown, item) => (item.tokens_in == null ? '' : tokens(item.tokens_in, item.tokens_out)) },
    { title: 'Стоимость', dataIndex: 'cost_rub', width: 110, render: (value: number | null) => (value == null ? '' : money(value, 4)) },
  ]

  const totals = data?.totals
  return (
    <>
      <SectionTitle
        sub="Кто, когда и что делал: загрузки, анализы, скачанные протоколы, изменения настроек."
        extra={
          <Flex gap={8}>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              Обновить
            </Button>
            <Button icon={<DownloadOutlined />} onClick={() => void csv()} loading={downloading}>
              Выгрузить CSV
            </Button>
          </Flex>
        }
      >
        Журнал
      </SectionTitle>
      <Card style={{ marginBottom: 16 }}>
        <Row gutter={[12, 12]} align="bottom">
          <Col xs={12} md={4}>
            <Typography.Text type="secondary">С даты</Typography.Text>
            <Input type="date" value={filters.from} onChange={(event) => setFilters({ ...filters, from: event.target.value })} />
          </Col>
          <Col xs={12} md={4}>
            <Typography.Text type="secondary">По дату</Typography.Text>
            <Input type="date" value={filters.to} onChange={(event) => setFilters({ ...filters, to: event.target.value })} />
          </Col>
          <Col xs={24} md={4}>
            <Typography.Text type="secondary">Пользователь</Typography.Text>
            <Select<number | null>
              style={{ width: '100%' }}
              value={filters.userId}
              onChange={(userId) => setFilters({ ...filters, userId: userId ?? null })}
              allowClear
              placeholder="Все"
              options={users.map((user) => ({ value: user.id, label: user.login }))}
            />
          </Col>
          <Col xs={24} md={6}>
            <Typography.Text type="secondary">Действия</Typography.Text>
            <Select
              mode="multiple"
              style={{ width: '100%' }}
              value={filters.actions}
              onChange={(actions) => setFilters({ ...filters, actions })}
              allowClear
              placeholder="Все"
              options={actionOptions}
              maxTagCount="responsive"
            />
          </Col>
          <Col xs={24} md={3}>
            <Typography.Text type="secondary">Проверка</Typography.Text>
            <Input value={filters.checkId} onChange={(event) => setFilters({ ...filters, checkId: event.target.value })} placeholder="id" />
          </Col>
          <Col xs={24} md={3}>
            <Button
              type="primary"
              block
              onClick={() => {
                setPage(1)
                setApplied(filters)
              }}
            >
              Показать
            </Button>
          </Col>
        </Row>
      </Card>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
      {totals && (
        <Typography.Paragraph>
          Записей: {totals.count}. Токены: {totals.tokens_in} на входе, {totals.tokens_out} на выходе. Стоимость:{' '}
          {money(totals.cost_rub)}.
        </Typography.Paragraph>
      )}
      <Card styles={{ body: { padding: 0 } }}>
        <Table<AuditItem>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={data?.items ?? []}
          loading={loading}
          scroll={{ x: 1000 }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: data?.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
        />
      </Card>
    </>
  )
}
