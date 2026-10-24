import { ArrowRightOutlined, CheckOutlined, ExclamationOutlined, FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Drawer, Segmented, Select } from 'antd'
import { useMemo, useState } from 'react'

import type { AnalysisAnswer, AnalysisResult, Block, EditableRow, Risk } from '../../api/checks.ts'
import { ContractText } from './ContractText.tsx'
import { CONFIDENCE_LABELS, plural, REFERENCE_LABELS, type ReferenceKey, sortRisks } from './labels.ts'
import { ReferenceDrawer } from './ReferenceDrawer.tsx'
import { RiskCard } from './RiskCard.tsx'
import type { ProtocolController } from './useProtocol.ts'

type Filter = 'all' | 'top' | 'stop' | 'high' | 'medium' | 'low'

interface Props {
  result: AnalysisResult
  blocks: Block[]
  protocol: ProtocolController
  mobile: boolean
  /** Уже 992 px: текст договора — в шторке, а не колонкой рядом. */
  narrow: boolean
  onNext: () => void
}

function Verdict({ answer }: { answer: AnalysisAnswer }) {
  const allowed = answer.approval.allowed
  const confidence = answer.confidence
  return (
    <section className={`z-card z-verdict${allowed ? ' is-ok' : ''}`} aria-label="Вывод анализа">
      <span className="z-verdict-icon" aria-hidden="true">
        {allowed ? <CheckOutlined /> : <ExclamationOutlined />}
      </span>
      <div style={{ minWidth: 0 }}>
        <p className="z-verdict-title">
          {allowed ? 'Можно подписывать без обязательных правок' : 'Подписывать без правок нельзя'}
        </p>
        <p className="z-verdict-text">{answer.summary}</p>
        {answer.approval.reasons.length > 0 && (
          <div className="z-chips">
            {answer.approval.reasons.map((reason) => (
              <span key={reason} className="z-chip">
                {reason}
              </span>
            ))}
          </div>
        )}
        <p className="z-verdict-conf">
          Уверенность анализа: {CONFIDENCE_LABELS[confidence.level]}
          {confidence.reasons.length > 0 ? ` — ${confidence.reasons.join('; ')}` : ''}
        </p>
      </div>
    </section>
  )
}

function riskBlocks(risk: Risk, found: string[] | undefined): string[] {
  return found?.length ? found : risk.block_ids
}

/**
 * Шаг «Риски»: вывод, риски по важности и решение по каждому — в протокол или нет, с нашей
 * редакцией. Рядом — текст договора, на узком экране — в шторке. Остальное из анализа — «Справка».
 */
export function RisksStep({ result, blocks, protocol, mobile, narrow, onNext }: Props) {
  const { answer, risk_checks: checks } = result
  const sorted = useMemo(() => sortRisks(answer.risks, answer.top_risks), [answer.risks, answer.top_risks])
  const [filter, setFilter] = useState<Filter>('all')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(sorted.slice(0, 1).map((risk) => risk.id)))
  const [active, setActive] = useState<string | null>(sorted[0]?.id ?? null)
  const [focusKey, setFocusKey] = useState(0)
  const [textOpen, setTextOpen] = useState(false)
  const [reference, setReference] = useState<ReferenceKey | null>(null)

  const state = protocol.state
  const rowsByRisk = useMemo(() => {
    const map = new Map<string, EditableRow>()
    for (const row of state?.rows ?? []) if (row.risk_id) map.set(row.risk_id, row)
    return map
  }, [state])
  const positions = useMemo(() => {
    const map = new Map<string, number>()
    let number = 0
    for (const row of state?.rows ?? []) if (row.include) map.set(row.id, ++number)
    return map
  }, [state])
  const marked = useMemo(
    () => new Set(answer.risks.flatMap((risk) => riskBlocks(risk, checks[risk.id]?.found_block_ids))),
    [answer.risks, checks],
  )
  const activeRisk = sorted.find((risk) => risk.id === active)
  const highlighted = activeRisk ? riskBlocks(activeRisk, checks[activeRisk.id]?.found_block_ids) : []

  const top = new Set(answer.top_risks)
  const count = (predicate: (risk: Risk) => boolean) => answer.risks.filter(predicate).length
  const matches: Record<Filter, (risk: Risk) => boolean> = {
    all: () => true,
    top: (risk) => top.has(risk.id),
    stop: (risk) => risk.stop_factor,
    high: (risk) => risk.severity === 'high',
    medium: (risk) => risk.severity === 'medium',
    low: (risk) => risk.severity === 'low',
  }
  const filterOptions = (
    [
      { value: 'all', label: `Все ${answer.risks.length}` },
      { value: 'top', label: `Главные ${count(matches.top)}` },
      { value: 'stop', label: `Стоп-факторы ${count(matches.stop)}` },
      { value: 'high', label: `Высокая ${count(matches.high)}` },
      { value: 'medium', label: `Средняя ${count(matches.medium)}` },
      { value: 'low', label: `Низкая ${count(matches.low)}` },
    ] as { value: Filter; label: string }[]
  ).filter((option) => option.value === 'all' || !option.label.endsWith(' 0'))
  const shown = sorted.filter(matches[filter])

  const select = (risk: Risk) => {
    setActive(risk.id)
    setFocusKey((key) => key + 1)
  }
  const toggle = (risk: Risk) => {
    const opening = !expanded.has(risk.id)
    setExpanded((current) => {
      const next = new Set(current)
      if (opening) next.add(risk.id)
      else next.delete(risk.id)
      return next
    })
    // Открытый риск сразу виден в тексте рядом; на узком экране шторка открывается только по кнопке.
    if (opening) select(risk)
  }
  const show = (risk: Risk) => {
    select(risk)
    if (narrow) setTextOpen(true)
  }
  const allOpen = shown.length > 0 && shown.every((risk) => expanded.has(risk.id))

  const references: { key: ReferenceKey; count: number }[] = [
    { key: 'obligations', count: answer.obligations.length },
    { key: 'gaps', count: answer.contradictions.length + answer.missing.length },
    { key: 'favorable', count: answer.favorable.length },
    { key: 'specialists', count: answer.specialist_checks.length },
  ]

  const text = (
    <ContractText
      blocks={blocks}
      highlighted={highlighted}
      marked={marked}
      focusKey={focusKey}
      maxHeight={narrow ? 'calc(85vh - 90px)' : 'calc(100vh - 110px)'}
    />
  )

  return (
    <>
      <div className={narrow ? 'z-work z-work-single' : 'z-work'}>
        <div className="z-col">
          <Verdict answer={answer} />
          {result.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              title="Проверьте перед отправкой протокола"
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {result.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          )}
          {protocol.loadError && <Alert type="error" showIcon title={protocol.loadError} />}
          <div className="z-tools">
            {mobile ? (
              <Select<Filter>
                value={filter}
                onChange={setFilter}
                options={filterOptions}
                style={{ width: '100%' }}
                aria-label="Какие риски показать"
              />
            ) : (
              <Segmented<Filter> value={filter} onChange={setFilter} options={filterOptions} aria-label="Какие риски показать" />
            )}
            {!mobile && (
              <Button
                type="link"
                onClick={() => setExpanded(allOpen ? new Set() : new Set(shown.map((risk) => risk.id)))}
                style={{ paddingInline: 0 }}
              >
                {allOpen ? 'Свернуть все' : 'Развернуть все'}
              </Button>
            )}
            {narrow && !mobile && (
              <Button icon={<FileTextOutlined />} onClick={() => setTextOpen(true)}>
                Текст договора
              </Button>
            )}
          </div>
          {shown.map((risk) => {
            const row = rowsByRisk.get(risk.id)
            return (
              <RiskCard
                key={risk.id}
                risk={risk}
                check={checks[risk.id]}
                row={row}
                protocolReady={state !== null}
                position={row ? (positions.get(row.id) ?? null) : null}
                expanded={expanded.has(risk.id)}
                active={active === risk.id}
                readOnly={protocol.readOnly}
                mobile={mobile}
                onToggle={() => toggle(risk)}
                onShow={() => show(risk)}
                onInclude={(include) => row && protocol.setRow(row.id, { include })}
                onWording={(text) => row && protocol.setRow(row.id, { supplier_text: text })}
              />
            )
          })}
          {shown.length === 0 && <p className="z-analysis-note">Таких рисков нет.</p>}
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: '12px 0 0' }}>Справка по договору</h2>
          <div className="z-ref">
            {references.map((item) => (
              <button key={item.key} type="button" className="z-ref-tile" onClick={() => setReference(item.key)}>
                <b>{item.count}</b>
                <span>{REFERENCE_LABELS[item.key]}</span>
              </button>
            ))}
          </div>
          {!mobile && (
            <div>
              <Button type="primary" size="large" onClick={onNext}>
                Дальше: протокол <ArrowRightOutlined />
              </Button>
            </div>
          )}
        </div>
        {!narrow && (
          <aside aria-label="Текст договора">
            <section className="z-card z-contract">
              <div className="z-contract-head">
                <b>Текст договора</b>
                <span>{plural(blocks.length, ['абзац', 'абзаца', 'абзацев'])}</span>
              </div>
              {text}
            </section>
          </aside>
        )}
      </div>
      {narrow && (
        // Шторка пересоздаётся при каждом открытии: текст сразу прокручен к выбранному пункту.
        <Drawer
          title="Текст договора"
          placement="bottom"
          size="85vh"
          open={textOpen}
          onClose={() => setTextOpen(false)}
          destroyOnHidden
          styles={{ body: { padding: 8 } }}
        >
          {text}
        </Drawer>
      )}
      <ReferenceDrawer open={reference} result={result} mobile={mobile} onClose={() => setReference(null)} />
      {mobile && (
        <nav className="z-bottom-bar" aria-label="Действия">
          <Button
            icon={<FileTextOutlined />}
            onClick={() => {
              setTextOpen(true)
            }}
          >
            Текст договора
          </Button>
          <Button type="primary" onClick={onNext}>
            К протоколу <ArrowRightOutlined />
          </Button>
        </nav>
      )}
    </>
  )
}
