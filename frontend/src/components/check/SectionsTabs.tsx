import { Card, Empty, Flex, Segmented, Table, Tag, Tooltip, Typography } from 'antd'
import { useState } from 'react'

import type { AnalysisResult, Contradiction, FavorableTerm, Obligation, Reference } from '../../api/checks.ts'
import { useIsMobile } from '../useIsMobile.ts'
import { clausesText, QUOTE_LABELS, SPECIALIST_LABELS } from './labels.ts'

type Party = 'all' | 'buyer' | 'supplier'

// Стороны различаются подписью, а не цветом: цвет на экранах проверки — только у критичности.
function PartyTag({ party }: { party: string }) {
  return party === 'supplier' ? <Tag>Поставщик</Tag> : <Tag bordered={false}>Покупатель</Tag>
}

function Where({ clauses, reference }: { clauses: string[]; reference: Reference | undefined }) {
  return (
    <>
      {clauses.length > 0 && <Tag>{clausesText(clauses)}</Tag>}
      {reference && reference.pages.length > 0 && <Tag>стр. {reference.pages.join(', ')}</Tag>}
      {reference && reference.unknown_clauses.length > 0 && (
        <Tooltip title="Таких пунктов в разметке договора нет">
          <Tag color="warning">нет пунктов {reference.unknown_clauses.join(', ')}</Tag>
        </Tooltip>
      )}
    </>
  )
}

/** Обязательства и сроки сторон. На телефоне — карточки вместо таблицы. */
export function ObligationsTab({ result }: { result: AnalysisResult }) {
  const mobile = useIsMobile()
  const [party, setParty] = useState<Party>('all')
  const references = result.refs.obligations
  const rows = result.answer.obligations.map((item, index) => ({ ...item, key: index, reference: references[index] }))
  if (mobile) {
    if (rows.length === 0) {
      return <Empty description="Модель не выделила обязательств" />
    }
    const shown = rows.filter((row) => party === 'all' || row.party === party)
    return (
      <Flex vertical gap={8}>
        <Segmented<Party>
          block
          value={party}
          onChange={setParty}
          options={[
            { value: 'all', label: 'Все' },
            { value: 'buyer', label: 'Покупатель' },
            { value: 'supplier', label: 'Поставщик' },
          ]}
        />
        {shown.map((row) => (
          <Card key={row.key} size="small">
            <Flex vertical gap={6}>
              <Flex gap={4} wrap>
                <PartyTag party={row.party} />
                <Where clauses={row.clauses} reference={row.reference} />
              </Flex>
              <Typography.Text>{row.obligation}</Typography.Text>
              <Typography.Text type="secondary">Срок: {row.deadline ?? '—'}</Typography.Text>
            </Flex>
          </Card>
        ))}
      </Flex>
    )
  }
  return (
    <Table<Obligation & { key: number; reference: Reference | undefined }>
      dataSource={rows}
      pagination={false}
      size="small"
      locale={{ emptyText: <Empty description="Модель не выделила обязательств" /> }}
      columns={[
        {
          title: 'Сторона',
          dataIndex: 'party',
          width: 130,
          filters: [
            { text: 'Покупатель', value: 'buyer' },
            { text: 'Поставщик', value: 'supplier' },
          ],
          onFilter: (value, row) => row.party === value,
          render: (value: string) => <PartyTag party={value} />,
        },
        { title: 'Обязательство', dataIndex: 'obligation' },
        { title: 'Срок', dataIndex: 'deadline', width: 260, render: (value: string | null) => value ?? '—' },
        {
          title: 'Где',
          key: 'where',
          width: 180,
          render: (_: unknown, row) => <Where clauses={row.clauses} reference={row.reference} />,
        },
      ]}
    />
  )
}

/** Противоречия в договоре и чего в нём не хватает. */
export function GapsTab({ result }: { result: AnalysisResult }) {
  const { contradictions, missing } = result.answer
  return (
    <>
      <Typography.Title level={4} style={{ marginBottom: 10 }}>
        Противоречия
      </Typography.Title>
      {contradictions.length === 0 && <Typography.Paragraph type="secondary">Противоречий не найдено.</Typography.Paragraph>}
      {contradictions.map((item: Contradiction, index) => (
        <Card key={index} size="small" style={{ marginBottom: 8 }}>
          <Typography.Paragraph style={{ marginBottom: 4 }}>{item.description}</Typography.Paragraph>
          <Where clauses={item.clauses} reference={result.refs.contradictions[index]} />
        </Card>
      ))}
      <Typography.Title level={4} style={{ margin: '20px 0 10px' }}>
        Неопределённости и недостающие материалы
      </Typography.Title>
      {missing.length === 0 && <Typography.Paragraph type="secondary">Всё, на что ссылается договор, есть.</Typography.Paragraph>}
      {missing.map((item, index) => (
        <Card key={index} size="small" style={{ marginBottom: 8 }}>
          <Typography.Text strong>{item.item}</Typography.Text>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {item.reason}
          </Typography.Paragraph>
        </Card>
      ))}
    </>
  )
}

/** Условия, выгодные поставщику. */
export function FavorableTab({ result }: { result: AnalysisResult }) {
  const items = result.answer.favorable
  if (items.length === 0) {
    return <Empty description="Модель не отметила выгодных условий" />
  }
  return (
    <>
      {items.map((item: FavorableTerm, index) => {
        const reference = result.refs.favorable[index]
        const quote = reference?.quote_status ? QUOTE_LABELS[reference.quote_status] : null
        return (
          <Card key={index} size="small" style={{ marginBottom: 8 }}>
            <Typography.Paragraph style={{ marginBottom: 4 }}>{item.description}</Typography.Paragraph>
            {item.quote && (
              <p className="z-quote" style={{ minHeight: 0, margin: '6px 0 8px' }}>
                {item.quote}
              </p>
            )}
            <Where clauses={item.clauses} reference={reference} />
            {quote && <Tag color={quote.color}>{quote.text}</Tag>}
          </Card>
        )
      })}
    </>
  )
}

/** Вопросы специалистам: юристу, финансам, производству, безопасности. */
export function SpecialistsTab({ result }: { result: AnalysisResult }) {
  const mobile = useIsMobile()
  const items = result.answer.specialist_checks
  if (items.length === 0) {
    return <Empty description="Дополнительных проверок не требуется" />
  }
  if (mobile) {
    return (
      <Flex vertical gap={8}>
        {items.map((item, index) => (
          <Card key={index} size="small">
            <Flex vertical gap={6} align="start">
              <Tag>{SPECIALIST_LABELS[item.specialist]}</Tag>
              <Typography.Text>{item.what}</Typography.Text>
            </Flex>
          </Card>
        ))}
      </Flex>
    )
  }
  return (
    <Table
      dataSource={items.map((item, index) => ({ ...item, key: index }))}
      pagination={false}
      size="small"
      columns={[
        {
          title: 'Кому',
          dataIndex: 'specialist',
          width: 200,
          render: (value: keyof typeof SPECIALIST_LABELS) => <Tag>{SPECIALIST_LABELS[value]}</Tag>,
        },
        { title: 'Что проверить', dataIndex: 'what' },
      ]}
    />
  )
}
