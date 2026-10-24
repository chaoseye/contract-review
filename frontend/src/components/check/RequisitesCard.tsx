import { CheckCircleOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Flex, Table, Tag, Typography } from 'antd'
import { useState } from 'react'

import type { RequisiteStatus, SideReport } from '../../api/checks.ts'
import { useIsMobile } from '../useIsMobile.ts'

const STATUS: Record<RequisiteStatus, { text: string; color: string }> = {
  match: { text: 'совпадает', color: 'success' },
  mismatch: { text: 'расходится', color: 'error' },
  not_found: { text: 'нет в договоре', color: 'default' },
}

type Row = SideReport['checks'][number]

function Details({ report }: { report: SideReport }) {
  const mobile = useIsMobile()
  if (report.checks.length === 0) return null
  // На телефоне четыре колонки не помещаются: каждый реквизит — отдельным блоком.
  if (mobile) {
    return (
      <Flex vertical gap={10} style={{ marginTop: 8 }}>
        {report.checks.map((row) => (
          <Flex key={row.key} vertical gap={2}>
            <Flex justify="space-between" align="start" gap={8}>
              <Typography.Text strong>{row.label}</Typography.Text>
              <Tag color={STATUS[row.status].color} style={{ marginInlineEnd: 0 }}>
                {STATUS[row.status].text}
              </Tag>
            </Flex>
            <Typography.Text type="secondary" style={{ wordBreak: 'break-word' }}>
              В карточке: {row.expected || '—'}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ wordBreak: 'break-word' }}>
              В договоре: {row.found.join('; ') || '—'}
            </Typography.Text>
          </Flex>
        ))}
      </Flex>
    )
  }
  return (
    <Table<Row>
      rowKey="key"
      size="small"
      pagination={false}
      dataSource={report.checks}
      style={{ marginTop: 8 }}
      columns={[
        { title: 'Реквизит', dataIndex: 'label', width: 200 },
        { title: 'В карточке', dataIndex: 'expected' },
        { title: 'В договоре', dataIndex: 'found', render: (found: string[]) => found.join('; ') || '—' },
        {
          title: <span className="z-sr">Итог</span>,
          dataIndex: 'status',
          width: 140,
          render: (status: RequisiteStatus) => <Tag color={STATUS[status].color}>{STATUS[status].text}</Tag>,
        },
      ]}
    />
  )
}

/**
 * Наша сторона в договоре и сверка реквизитов с карточкой организации. Всё в порядке — одна
 * строка; расхождение — карточка с объяснением.
 */
export function RequisitesCard({ report }: { report: SideReport }) {
  const [open, setOpen] = useState(false)
  if (report.status === 'skipped') return null
  const ok = report.warnings.length === 0
  const toggle =
    report.checks.length > 0 ? (
      <Button type="link" size="small" onClick={() => setOpen((value) => !value)} aria-expanded={open} style={{ padding: 0 }}>
        {open ? 'Скрыть сверку' : 'Сверка реквизитов'}
      </Button>
    ) : null
  if (ok) {
    return (
      <div>
        <p className="z-status-line" style={{ margin: 0 }}>
          <CheckCircleOutlined aria-hidden="true" />
          <span>
            Мы в договоре — «{report.role_word ?? 'Поставщик'}», реквизиты совпадают с карточкой организации
            {toggle && <> · {toggle}</>}
          </span>
        </p>
        {open && <Details report={report} />}
      </div>
    )
  }
  return (
    <section className="z-card" style={{ padding: '14px 18px', borderColor: '#E9CF9F', background: '#FFFBF2' }}>
      <Flex gap={10} align="start">
        <WarningOutlined style={{ color: 'var(--z-warning)', fontSize: 18, marginTop: 2 }} aria-hidden="true" />
        <div style={{ minWidth: 0, flex: 1 }}>
          <Typography.Text strong>Наша сторона и реквизиты: проверьте</Typography.Text>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {report.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          {toggle && <div style={{ marginTop: 6 }}>{toggle}</div>}
          {open && <Details report={report} />}
        </div>
      </Flex>
    </section>
  )
}
