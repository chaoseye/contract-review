import { StopOutlined } from '@ant-design/icons'
import { Alert, Descriptions, Flex, List, Tag, Tooltip, Typography } from 'antd'

import type { PromptRun } from '../../api/admin.ts'
import { clausesText, QUOTE_LABELS, SEVERITY_LABELS } from '../../components/check/labels.ts'
import { money, tokens } from './format.ts'

/** Итог прогона черновика: вывод, риски с пометками о цитатах, токены и стоимость. Нигде не сохраняется. */
export function RunResult({ run }: { run: PromptRun }) {
  if (run.status === 'failed') {
    return <Alert type="error" showIcon title={run.error ?? 'Прогон не выполнен'} description={run.detail ?? undefined} />
  }
  const result = run.result
  if (!result) return null
  const { answer, risk_checks: checks } = result
  const risks = [...answer.risks].sort(
    (a, b) =>
      Number(b.stop_factor) - Number(a.stop_factor) || SEVERITY_LABELS[a.severity].order - SEVERITY_LABELS[b.severity].order,
  )
  const usage = run.usage
  return (
    <Flex vertical gap={12}>
      <Descriptions
        size="small"
        column={{ xs: 1, md: 4 }}
        items={[
          { key: 'model', label: 'Модель', children: `${usage.model ?? '—'} (${run.profile})` },
          { key: 'tokens', label: 'Токены', children: tokens(usage.input_tokens, usage.output_tokens) },
          { key: 'cost', label: 'Стоимость', children: `${usage.cost_estimated ? '≈ ' : ''}${money(usage.cost_rub, 4)}` },
          { key: 'time', label: 'Время', children: usage.duration_seconds != null ? `${usage.duration_seconds} с` : '—' },
        ]}
      />
      <Alert
        type={answer.approval.allowed ? 'success' : 'error'}
        showIcon
        title={answer.approval.allowed ? 'Согласовать можно без обязательных правок' : 'Согласовывать без правок нельзя'}
        description={answer.approval.reasons.length > 0 ? answer.approval.reasons.join('; ') : undefined}
      />
      <Typography.Paragraph style={{ marginBottom: 0 }}>{answer.summary}</Typography.Paragraph>
      {result.warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="Замечания проверки ответа"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      )}
      <List
        size="small"
        bordered
        header={<Typography.Text strong>Риски ({risks.length})</Typography.Text>}
        dataSource={risks}
        renderItem={(risk) => {
          const quote = checks[risk.id] ? QUOTE_LABELS[checks[risk.id].quote_status] : null
          return (
            <List.Item>
              <Flex vertical gap={4} style={{ width: '100%' }}>
                <Typography.Text strong>
                  {risk.id}. {risk.title}
                </Typography.Text>
                <Flex gap={4} wrap>
                  <Tag color={SEVERITY_LABELS[risk.severity].color}>{SEVERITY_LABELS[risk.severity].text}</Tag>
                  {risk.stop_factor && (
                    <Tag color="error" icon={<StopOutlined />}>
                      Стоп-фактор
                    </Tag>
                  )}
                  {risk.clauses.length > 0 && <Tag>{clausesText(risk.clauses)}</Tag>}
                  {quote && (
                    <Tooltip title={quote.hint}>
                      <Tag color={quote.color}>{quote.text}</Tag>
                    </Tooltip>
                  )}
                  {risk.proposed_wording && <Tag>есть редакция</Tag>}
                </Flex>
                <Typography.Text type="secondary">{risk.explanation}</Typography.Text>
              </Flex>
            </List.Item>
          )
        }}
      />
    </Flex>
  )
}
