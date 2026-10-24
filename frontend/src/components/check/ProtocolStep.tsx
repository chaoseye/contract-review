import {
  ArrowDownOutlined,
  ArrowLeftOutlined,
  ArrowUpOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  PlusOutlined,
  UndoOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Alert, App, Button, Checkbox, Collapse, Flex, Input, InputNumber, Modal, Popconfirm, Select, Spin, Switch } from 'antd'
import { type ReactNode, useId, useState } from 'react'

import type { EditableRow, ProtocolMeta, Risk } from '../../api/checks.ts'
import { ApiError } from '../../api/client.ts'
import { formatWhen, longDate, SEVERITY_LABELS, shortDate } from './labels.ts'
import type { ProtocolController } from './useProtocol.ts'

interface Props {
  protocol: ProtocolController
  risks: Risk[]
  mobile: boolean
  narrow: boolean
  onBack: () => void
  /** Протокол скачан: страница обновит шаг «Протокол». */
  onDownloaded: () => void
}

function newRow(): EditableRow {
  return {
    id: crypto.randomUUID().replaceAll('-', '').slice(0, 12),
    risk_id: null,
    include: true,
    clause: '',
    buyer_text: 'Пункт отсутствует.',
    supplier_text: '',
  }
}

function Field({ label, children, wide, hint }: { label: string; children: (id: string) => ReactNode; wide?: boolean; hint?: string }) {
  const id = useId()
  return (
    <div className={wide ? 'z-field z-field-wide' : 'z-field'}>
      <label className="z-label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint && <p className="z-hint">{hint}</p>}
    </div>
  )
}

function SaveLine({ protocol }: { protocol: ProtocolController }) {
  if (protocol.readOnly) return null
  if (protocol.status === 'error') {
    return (
      <p className="z-save is-error" role="alert">
        <WarningOutlined aria-hidden="true" />
        {protocol.saveError}
        <Button type="link" size="small" onClick={() => void protocol.flush()} style={{ padding: 0 }}>
          Повторить
        </Button>
      </p>
    )
  }
  if (protocol.status === 'saving' || protocol.status === 'pending') {
    return (
      <p className="z-save" aria-live="polite">
        <LoadingOutlined aria-hidden="true" />
        Сохраняю правки…
      </p>
    )
  }
  return (
    <p className="z-save is-saved" aria-live="polite">
      <CheckCircleOutlined aria-hidden="true" />
      {protocol.savedAt ? `Правки сохранены в ${formatWhen(protocol.savedAt.toISOString())}` : 'Правки сохраняются сами'}
    </p>
  )
}

/**
 * Шаг «Протокол»: протокол так, как его увидит покупатель, — строки, данные и вступление; правка
 * строк по нажатию. Справа — выпуск: подпись, печать, Word и PDF; ниже — данные протокола.
 */
export function ProtocolStep({ protocol, risks, mobile, narrow, onBack, onDownloaded }: Props) {
  const { message } = App.useApp()
  const [selected, setSelected] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [showOut, setShowOut] = useState(false)
  const [signature, setSignature] = useState(false)
  const [stamp, setStamp] = useState(false)
  const [busy, setBusy] = useState<'docx' | 'pdf' | 'reset' | null>(null)

  const { loaded, state, readOnly } = protocol
  if (protocol.loadError) {
    return <Alert type="error" showIcon title={protocol.loadError} />
  }
  if (!loaded || !state) {
    return (
      <Flex justify="center" style={{ padding: 48 }}>
        <Spin />
      </Flex>
    )
  }

  const meta = state.meta
  const risksById = new Map(risks.map((risk) => [risk.id, risk]))
  const included = state.rows.filter((row) => row.include)
  const excluded = state.rows.filter((row) => !row.include)
  const organization = loaded.organization
  const canDownload = !readOnly && organization.ready && included.length > 0
  const selectedRow = included.find((row) => row.id === selected)
  const editingRow = state.rows.find((row) => row.id === editing)
  const setMeta = (patch: Partial<ProtocolMeta>) => protocol.setMeta(patch)
  const rowTitle = (row: EditableRow) => (row.risk_id ? risksById.get(row.risk_id)?.title : undefined) ?? 'Свой пункт'

  // Порядок меняется среди строк протокола: невключённые остаются на своих местах.
  const move = (row: EditableRow, delta: -1 | 1) => {
    const position = included.findIndex((item) => item.id === row.id)
    const neighbour = included[position + delta]
    if (!neighbour) return
    const rows = [...state.rows]
    const from = rows.findIndex((item) => item.id === row.id)
    const to = rows.findIndex((item) => item.id === neighbour.id)
    ;[rows[from], rows[to]] = [rows[to], rows[from]]
    protocol.setRows(rows)
  }

  const download = async (format: 'docx' | 'pdf') => {
    setBusy(format)
    try {
      await protocol.download(format, signature, stamp)
      onDownloaded()
    } catch (error) {
      message.error(error instanceof ApiError ? error.message : 'Не получилось, попробуйте ещё раз')
    } finally {
      setBusy(null)
    }
  }

  const reset = async () => {
    setBusy('reset')
    try {
      await protocol.reset()
      setSelected(null)
      message.success('Протокол собран заново из результата анализа')
    } catch (error) {
      message.error(error instanceof ApiError ? error.message : 'Не получилось, попробуйте ещё раз')
    } finally {
      setBusy(null)
    }
  }

  const addRow = () => {
    const row = newRow()
    protocol.setRows([...state.rows, row])
    setSelected(row.id)
    setEditing(row.id)
  }

  const subtitle = `к Договору${meta.contract_kind.trim() ? ` ${meta.contract_kind.trim()}` : ''} №\u00a0${
    meta.contract_number.trim() || '____'
  }${meta.contract_date ? ` от ${shortDate(meta.contract_date)}` : ''}`

  const table = mobile ? (
    <Flex vertical gap={8}>
      {included.map((row, index) => (
        <button key={row.id} type="button" className="z-prow" onClick={() => !readOnly && setEditing(row.id)}>
          <b>
            {index + 1}. {row.clause || 'Пункт не указан'} · {rowTitle(row)}
          </b>
          <span className="is-buyer">{row.buyer_text}</span>
          <span>{row.supplier_text || '—'}</span>
        </button>
      ))}
      {included.length === 0 && <p className="z-analysis-note">В протоколе пока нет строк.</p>}
    </Flex>
  ) : (
    <table className="z-ptable">
      <colgroup>
        {meta.include_agreed_column ? (
          <>
            <col style={{ width: '4%' }} />
            <col style={{ width: '9%' }} />
            <col style={{ width: '24%' }} />
            <col style={{ width: '35%' }} />
            <col style={{ width: '28%' }} />
          </>
        ) : (
          <>
            <col style={{ width: '5%' }} />
            <col style={{ width: '10%' }} />
            <col style={{ width: '37%' }} />
            <col style={{ width: '48%' }} />
          </>
        )}
      </colgroup>
      <thead>
        <tr>
          <th>№</th>
          <th>Пункт Договора</th>
          <th>Редакция Покупателя</th>
          <th>Редакция Поставщика</th>
          {meta.include_agreed_column && <th>Согласованная редакция</th>}
        </tr>
      </thead>
      <tbody>
        {included.map((row, index) => (
          <tr
            key={row.id}
            className={row.id === selected ? 'is-selected' : undefined}
            onClick={() => setSelected(row.id)}
            onDoubleClick={() => !readOnly && setEditing(row.id)}
          >
            <td className="is-c">
              <button type="button" className="z-rownum" onClick={() => setSelected(row.id)} aria-label={`Строка ${index + 1}: ${rowTitle(row)}`}>
                {index + 1}
              </button>
            </td>
            <td className="is-c">{row.clause}</td>
            <td>{row.buyer_text}</td>
            <td>{row.supplier_text}</td>
            {meta.include_agreed_column && <td />}
          </tr>
        ))}
        {included.length === 0 && (
          <tr>
            <td className="is-empty" colSpan={meta.include_agreed_column ? 5 : 4}>
              В протоколе пока нет строк: включите риски на шаге «Риски» или добавьте свой пункт.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )

  const position = selectedRow ? included.indexOf(selectedRow) : -1
  const preview = (
    <section className="z-card z-preview" aria-label="Протокол, как его увидит покупатель">
      <div className="z-preview-head">
        <b>Протокол — как его увидит покупатель</b>
        {!readOnly && <span>{mobile ? 'Нажмите на строку, чтобы изменить' : 'Выберите строку, чтобы изменить её или порядок'}</span>}
      </div>
      <div className="z-sheet">
        <p className="z-sheet-title">{meta.title.trim() || 'ПРОТОКОЛ РАЗНОГЛАСИЙ'}</p>
        <p className="z-sheet-sub">{subtitle}</p>
        <div className="z-sheet-place">
          <span>{meta.city}</span>
          <span>{longDate(meta.protocol_date)}</span>
        </div>
        <p className="z-sheet-preamble">{loaded.preamble_preview}</p>
        {table}
        <p className="z-sheet-rest">Дальше — заключительные положения и подписи, как в шаблоне протокола.</p>
      </div>
      {!readOnly && !mobile && selectedRow && (
        <div className="z-rowbar" role="toolbar" aria-label={`Строка ${position + 1}`}>
          <span>
            Строка {position + 1} · {rowTitle(selectedRow)}
          </span>
          <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(selectedRow.id)}>
            Изменить
          </Button>
          <Button size="small" icon={<ArrowUpOutlined />} disabled={position === 0} onClick={() => move(selectedRow, -1)} aria-label="Выше" />
          <Button
            size="small"
            icon={<ArrowDownOutlined />}
            disabled={position === included.length - 1}
            onClick={() => move(selectedRow, 1)}
            aria-label="Ниже"
          />
          <Button
            size="small"
            icon={<MinusCircleOutlined />}
            onClick={() => {
              protocol.setRow(selectedRow.id, { include: false })
              setSelected(null)
            }}
          >
            Убрать из протокола
          </Button>
        </div>
      )}
      {!readOnly && (
        <div className="z-proto-below">
          <Button icon={<PlusOutlined />} onClick={addRow}>
            Добавить свой пункт
          </Button>
          {excluded.length > 0 && (
            <Button type="link" onClick={() => setShowOut((value) => !value)} aria-expanded={showOut} style={{ paddingInline: 0 }}>
              {showOut ? 'Скрыть' : 'Показать'} не вошедшие: {excluded.length}
            </Button>
          )}
        </div>
      )}
      {showOut && excluded.length > 0 && (
        <div className="z-out-list">
          {excluded.map((row) => {
            const risk = row.risk_id ? risksById.get(row.risk_id) : undefined
            return (
              <div key={row.id} className="z-out-item">
                <span>
                  {rowTitle(row)}
                  <small>
                    {[row.clause, risk ? SEVERITY_LABELS[risk.severity].text.toLowerCase() : null].filter(Boolean).join(' · ')}
                  </small>
                </span>
                <Button size="small" onClick={() => protocol.setRow(row.id, { include: true })}>
                  Вернуть
                </Button>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )

  const downloadBox = (
    <>
      <p className="z-panel-title">Скачать протокол</p>
      {readOnly && <Alert type="info" showIcon title="Протокол правит и выпускает менеджер, который загрузил договор." />}
      {!organization.ready && (
        <Alert
          type="warning"
          showIcon
          title="Не заполнено полное наименование нашей организации"
          description="Без него протокол не собрать. Обратитесь к администратору."
        />
      )}
      {!readOnly && included.length === 0 && (
        <Alert type="warning" showIcon title="В протоколе нет строк — скачивать нечего." />
      )}
      <div className="z-switch-row">
        <span>
          Подпись
          <small>{organization.signature ? 'из раздела «Организация»' : 'не загружена администратором'}</small>
        </span>
        <Switch checked={signature} onChange={setSignature} disabled={!organization.signature || readOnly} aria-label="Подпись" />
      </div>
      <div className="z-switch-row">
        <span>
          Печать
          <small>{organization.stamp ? 'из раздела «Организация»' : 'не загружена администратором'}</small>
        </span>
        <Switch checked={stamp} onChange={setStamp} disabled={!organization.stamp || readOnly} aria-label="Печать" />
      </div>
      {!mobile && (
        <>
          <Button type="primary" size="large" block icon={<FileWordOutlined />} loading={busy === 'docx'} disabled={!canDownload} onClick={() => void download('docx')}>
            Скачать Word
          </Button>
          <Button size="large" block icon={<FilePdfOutlined />} loading={busy === 'pdf'} disabled={!canDownload} onClick={() => void download('pdf')}>
            Скачать PDF
          </Button>
        </>
      )}
      <SaveLine protocol={protocol} />
      <p className="z-hint" style={{ textAlign: 'center', marginTop: -6 }}>
        Документ собирается при скачивании и в системе не хранится.
      </p>
    </>
  )

  const dataFields = (
    <>
      <div className="z-fields">
        <Field label="Номер договора">
          {(id) => <Input id={id} value={meta.contract_number} maxLength={100} disabled={readOnly} onChange={(event) => setMeta({ contract_number: event.target.value })} />}
        </Field>
        <Field label="Дата договора">
          {(id) => (
            <Input id={id} type="date" value={meta.contract_date ?? ''} disabled={readOnly} onChange={(event) => setMeta({ contract_date: event.target.value || null })} />
          )}
        </Field>
        <Field label="Дата протокола">
          {(id) => (
            <Input id={id} type="date" value={meta.protocol_date ?? ''} disabled={readOnly} onChange={(event) => setMeta({ protocol_date: event.target.value || null })} />
          )}
        </Field>
        <Field label="Город">
          {(id) => <Input id={id} value={meta.city} maxLength={100} disabled={readOnly} onChange={(event) => setMeta({ city: event.target.value })} />}
        </Field>
        <Field label="Покупатель — полное наименование" wide>
          {(id) => (
            <Input.TextArea
              id={id}
              autoSize={{ minRows: 2, maxRows: 4 }}
              value={meta.buyer_full_name}
              maxLength={500}
              disabled={readOnly}
              status={meta.buyer_full_name.trim() ? undefined : 'warning'}
              onChange={(event) => setMeta({ buyer_full_name: event.target.value })}
            />
          )}
        </Field>
        <Field label="В лице">
          {(id) => (
            <Input
              id={id}
              value={meta.buyer_representative}
              maxLength={300}
              placeholder="генерального директора Иванова И.И."
              disabled={readOnly}
              onChange={(event) => setMeta({ buyer_representative: event.target.value })}
            />
          )}
        </Field>
        <Field label="На основании">
          {(id) => (
            <Input id={id} value={meta.buyer_basis} maxLength={300} placeholder="Устава" disabled={readOnly} onChange={(event) => setMeta({ buyer_basis: event.target.value })} />
          )}
        </Field>
        <Field label="Приложения к протоколу" wide>
          {(id) => (
            <Select
              id={id}
              mode="tags"
              value={meta.appendices}
              maxCount={20}
              onChange={(appendices: string[]) => setMeta({ appendices })}
              placeholder="Например: Спецификация № 1"
              disabled={readOnly}
              style={{ width: '100%' }}
              open={false}
              suffixIcon={null}
            />
          )}
        </Field>
      </div>
      <Collapse
        ghost
        size="small"
        items={[
          {
            key: 'design',
            label: 'Оформление, подписи и вступление',
            children: (
              <div className="z-fields">
                <Field label="Заголовок" wide>
                  {(id) => <Input id={id} value={meta.title} maxLength={200} disabled={readOnly} onChange={(event) => setMeta({ title: event.target.value })} />}
                </Field>
                <Field label="Вид договора" hint="в родительном падеже: «поставки»">
                  {(id) => (
                    <Input id={id} value={meta.contract_kind} maxLength={100} disabled={readOnly} onChange={(event) => setMeta({ contract_kind: event.target.value })} />
                  )}
                </Field>
                <Field label="Шрифт таблицы, pt">
                  {(id) => (
                    <InputNumber
                      id={id}
                      min={8}
                      max={12}
                      step={0.5}
                      value={meta.table_font_size}
                      disabled={readOnly}
                      onChange={(value) => setMeta({ table_font_size: value ?? 10 })}
                      style={{ width: '100%' }}
                    />
                  )}
                </Field>
                <Field label="Покупатель в блоке подписей" hint="пусто — полное наименование">
                  {(id) => (
                    <Input id={id} value={meta.buyer_short_name} maxLength={300} disabled={readOnly} onChange={(event) => setMeta({ buyer_short_name: event.target.value })} />
                  )}
                </Field>
                <Field label="Подпись покупателя" hint="расшифровка; пусто — линия">
                  {(id) => (
                    <Input
                      id={id}
                      value={meta.buyer_signer}
                      maxLength={200}
                      placeholder="И.И. Иванов"
                      disabled={readOnly}
                      onChange={(event) => setMeta({ buyer_signer: event.target.value })}
                    />
                  )}
                </Field>
                <div className="z-field z-field-wide">
                  <Flex vertical gap={8}>
                    <Checkbox checked={meta.include_agreed_column} disabled={readOnly} onChange={(event) => setMeta({ include_agreed_column: event.target.checked })}>
                      Колонка «Согласованная редакция»
                    </Checkbox>
                    <Checkbox checked={meta.supplier_first} disabled={readOnly} onChange={(event) => setMeta({ supplier_first: event.target.checked })}>
                      В преамбуле договора первым назван поставщик
                    </Checkbox>
                  </Flex>
                </div>
                <Field label="Вступление" wide hint="Пусто — по шаблону, как в документе слева.">
                  {(id) => (
                    <Input.TextArea
                      id={id}
                      autoSize={{ minRows: 2, maxRows: 8 }}
                      value={meta.preamble}
                      maxLength={5000}
                      placeholder={loaded.preamble_preview}
                      disabled={readOnly}
                      onChange={(event) => setMeta({ preamble: event.target.value })}
                    />
                  )}
                </Field>
              </div>
            ),
          },
        ]}
      />
      {!readOnly && (
        <Popconfirm
          title="Сбросить правки протокола?"
          description="Строки и данные вернутся к результату анализа. Ваши правки пропадут."
          okText="Сбросить"
          cancelText="Отмена"
          okButtonProps={{ danger: true }}
          onConfirm={() => void reset()}
        >
          <Button type="text" icon={<UndoOutlined />} loading={busy === 'reset'} style={{ alignSelf: 'flex-start', color: 'var(--z-text-2)' }}>
            Сбросить правки
          </Button>
        </Popconfirm>
      )}
    </>
  )

  const editor = (
    <Modal
      title={editingRow ? (editingRow.risk_id ? rowTitle(editingRow) : 'Свой пункт') : ''}
      open={editingRow !== undefined}
      onCancel={() => setEditing(null)}
      width={mobile ? undefined : 760}
      style={mobile ? { top: 12 } : undefined}
      footer={
        <Flex justify="space-between" gap={8}>
          {editingRow && !editingRow.risk_id ? (
            <Button
              danger
              type="text"
              icon={<DeleteOutlined />}
              onClick={() => {
                protocol.setRows(state.rows.filter((row) => row.id !== editingRow.id))
                setEditing(null)
                setSelected(null)
              }}
            >
              Удалить пункт
            </Button>
          ) : (
            <span />
          )}
          <Button type="primary" onClick={() => setEditing(null)}>
            Готово
          </Button>
        </Flex>
      }
      destroyOnHidden
    >
      {editingRow && (
        <div className="z-fields">
          <Field label="Пункт договора" wide>
            {(id) => (
              <Input id={id} value={editingRow.clause} maxLength={200} placeholder="п. 9.2" onChange={(event) => protocol.setRow(editingRow.id, { clause: event.target.value })} />
            )}
          </Field>
          <Field label="Редакция Покупателя" hint="Дословно из договора или «Пункт отсутствует.»">
            {(id) => (
              <Input.TextArea
                id={id}
                className="z-wording"
                autoSize={{ minRows: 5, maxRows: 16 }}
                maxLength={20000}
                value={editingRow.buyer_text}
                onChange={(event) => protocol.setRow(editingRow.id, { buyer_text: event.target.value })}
              />
            )}
          </Field>
          <Field label="Редакция Поставщика — наша">
            {(id) => (
              <Input.TextArea
                id={id}
                className="z-wording"
                autoSize={{ minRows: 5, maxRows: 16 }}
                maxLength={20000}
                value={editingRow.supplier_text}
                status={editingRow.supplier_text.trim() ? undefined : 'warning'}
                onChange={(event) => protocol.setRow(editingRow.id, { supplier_text: event.target.value })}
              />
            )}
          </Field>
          <p className="z-hint z-field-wide">Правки сохраняются сами.</p>
        </div>
      )}
    </Modal>
  )

  if (narrow) {
    return (
      <>
        <div className="z-check" style={{ gap: 12 }}>
          {!mobile && (
            <section className="z-card z-panel" style={{ position: 'static' }}>
              {downloadBox}
            </section>
          )}
          {preview}
          <section className="z-card" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <p className="z-panel-title">Данные протокола</p>
            {dataFields}
          </section>
          {mobile && (
            <section className="z-card z-panel" style={{ position: 'static' }}>
              {downloadBox}
            </section>
          )}
        </div>
        {editor}
        {mobile && (
          <nav className="z-bottom-bar" aria-label="Скачать протокол" style={{ gridTemplateColumns: '48px 1fr 1fr' }}>
            <Button icon={<ArrowLeftOutlined />} onClick={onBack} aria-label="К рискам" />
            <Button type="primary" icon={<FileWordOutlined />} loading={busy === 'docx'} disabled={!canDownload} onClick={() => void download('docx')}>
              Word
            </Button>
            <Button icon={<FilePdfOutlined />} loading={busy === 'pdf'} disabled={!canDownload} onClick={() => void download('pdf')}>
              PDF
            </Button>
          </nav>
        )}
      </>
    )
  }

  return (
    <>
      <div className="z-proto">
        {preview}
        <aside className="z-card z-panel" aria-label="Выпуск протокола">
          {downloadBox}
          <div className="z-divider" />
          <p className="z-panel-title" style={{ fontSize: 15 }}>
            Данные протокола
          </p>
          {dataFields}
        </aside>
      </div>
      {editor}
    </>
  )
}
