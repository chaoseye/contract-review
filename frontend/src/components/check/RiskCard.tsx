import { AimOutlined, CheckOutlined, DownOutlined, MinusOutlined, UpOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Flex, Input, Segmented } from 'antd'
import { useId } from 'react'

import type { EditableRow, Risk, RiskCheck } from '../../api/checks.ts'
import { clausesText, QUOTE_LABELS, SEVERITY_LABELS } from './labels.ts'

interface Props {
  risk: Risk
  check: RiskCheck | undefined
  /** Строка протокола этого риска; undefined — протокол ещё грузится или строки нет. */
  row: EditableRow | undefined
  protocolReady: boolean
  /** Номер строки среди включённых в протокол. */
  position: number | null
  expanded: boolean
  active: boolean
  readOnly: boolean
  mobile: boolean
  onToggle: () => void
  onShow: () => void
  onInclude: (include: boolean) => void
  onWording: (text: string) => void
}

function blockIds(risk: Risk, check: RiskCheck | undefined): string[] {
  return check?.found_block_ids.length ? check.found_block_ids : risk.block_ids
}

/**
 * Риск и решение по нему в одном месте: объяснение, текст договора, наша редакция для протокола и
 * «В протокол / Не включать». Свёрнутая карточка — одна строка с решением.
 */
export function RiskCard(props: Props) {
  const { risk, check, row, protocolReady, position, expanded, active, readOnly, mobile } = props
  const fieldId = useId()
  const severity = SEVERITY_LABELS[risk.severity]
  const included = row?.include ?? false
  const where = risk.is_new_clause ? `новый пункт ${risk.clauses.join(', ')}`.trim() : clausesText(risk.clauses)
  const pages = check?.pages.length ? `стр. ${check.pages.join(', ')}` : ''
  const canShow = blockIds(risk, check).length > 0

  if (!expanded) {
    return (
      <article className={`z-risk${active ? ' is-active' : ''}`}>
        <button type="button" className="z-risk-row" onClick={props.onToggle} aria-expanded={false}>
          <span className={`z-decision-icon ${included ? 'is-in' : 'is-out'}`} aria-hidden="true">
            {included ? <CheckOutlined /> : <MinusOutlined />}
          </span>
          <span className="z-risk-name">
            <b>{risk.title}</b>
            <span>
              {[where, severity.text.toLowerCase(), risk.stop_factor ? 'стоп-фактор' : null].filter(Boolean).join(' · ')}
            </span>
          </span>
          {row && (
            <span className={`z-risk-state${included ? '' : ' is-out'}`}>{included ? 'В протоколе' : 'Не включено'}</span>
          )}
          <DownOutlined aria-hidden="true" style={{ color: 'var(--z-text-2)', fontSize: 12 }} />
        </button>
      </article>
    )
  }

  const quote = check ? QUOTE_LABELS[check.quote_status] : null
  // Пометка о цитате — у каждого риска; у нового пункта её заменяет метка «новый пункт».
  const quoteNote = quote && check?.quote_status !== 'new_clause' ? quote.text.toLowerCase() : null
  const quoteWarning =
    check && (check.quote_status === 'fuzzy' || check.quote_status === 'not_found' || check.quote_status === 'empty') && quote
      ? quote.hint
      : null
  const buyerText = row?.buyer_text || risk.quote || (risk.is_new_clause ? 'Пункт отсутствует.' : '—')
  const wording = row ? row.supplier_text : (risk.proposed_wording ?? '')

  return (
    <article className={`z-risk is-open${active ? ' is-active' : ''}`}>
      <div className="z-risk-top">
        <div className="z-pills">
          {risk.stop_factor && <span className="z-pill is-stop">Стоп-фактор</span>}
          <span className={`z-pill is-${risk.severity}`}>{severity.text}</span>
          {risk.is_new_clause && <span className="z-pill is-neutral">новый пункт</span>}
          {(risk.category || quoteNote) && (
            <span className="z-pill-note">{[risk.category, quoteNote].filter(Boolean).join(' · ')}</span>
          )}
        </div>
        <Flex gap={4} align="center">
          {canShow && (
            <Button type="link" icon={<AimOutlined />} onClick={props.onShow} style={{ paddingInline: 4 }}>
              {[where, pages].filter(Boolean).join(', ')} в тексте
            </Button>
          )}
          <Button type="text" icon={<UpOutlined />} onClick={props.onToggle} aria-label="Свернуть риск" aria-expanded />
        </Flex>
      </div>
      <h3 className="z-risk-title">{risk.title}</h3>
      {quoteWarning && (
        <p className="z-warn-line">
          <WarningOutlined aria-hidden="true" />
          {quoteWarning}
        </p>
      )}
      {check && check.unknown_clauses.length > 0 && (
        <p className="z-warn-line">
          <WarningOutlined aria-hidden="true" />В договоре нет пунктов {check.unknown_clauses.join(', ')}.
        </p>
      )}
      <p className="z-risk-text">{risk.explanation}</p>
      {risk.recommendation && (
        <div>
          <span className="z-label">Чего добиться</span>
          <p className="z-risk-text">{risk.recommendation}</p>
        </div>
      )}
      <div className="z-pair">
        <div>
          <span className="z-label">В договоре</span>
          <p className="z-quote">{buyerText}</p>
        </div>
        <div>
          <label className="z-label" htmlFor={fieldId}>
            Наша редакция для протокола
          </label>
          <Input.TextArea
            id={fieldId}
            className="z-wording"
            value={wording}
            onChange={(event) => props.onWording(event.target.value)}
            autoSize={{ minRows: 4, maxRows: 14 }}
            maxLength={20000}
            disabled={readOnly || !row}
            status={included && !wording.trim() ? 'warning' : undefined}
          />
        </div>
      </div>
      {included && !wording.trim() && (
        <p className="z-warn-line">
          <WarningOutlined aria-hidden="true" />
          Напишите нашу редакцию: без неё строка уйдёт в протокол пустой.
        </p>
      )}
      <div className="z-decision">
        <Segmented
          className="z-choice"
          block={mobile}
          value={included ? 'in' : 'out'}
          disabled={readOnly || !row}
          onChange={(value) => props.onInclude(value === 'in')}
          options={[
            {
              value: 'in',
              label: (
                <span>
                  <CheckOutlined aria-hidden="true" /> В протокол
                </span>
              ),
            },
            { value: 'out', label: 'Не включать' },
          ]}
          aria-label="Решение по риску"
        />
        <span className="z-decision-note">
          {!protocolReady
            ? 'Загружаю протокол…'
            : !row
              ? 'Строки для этого риска нет: добавьте свой пункт на шаге «Протокол»'
              : included
                ? `Строка ${position} протокола`
                : 'Не войдёт в протокол'}
        </span>
      </div>
    </article>
  )
}
