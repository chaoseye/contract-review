import { ArrowLeftOutlined, ArrowRightOutlined, DeleteOutlined, MoreOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Collapse, Descriptions, Dropdown, Result } from 'antd'
import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { analyzeCheck, type CheckDetail, deleteCheck, getCheck, isInProgress } from '../api/checks.ts'
import { ApiError } from '../api/client.ts'
import { useAuth } from '../auth/useAuth.ts'
import { AnalysisStep } from '../components/check/AnalysisStep.tsx'
import '../components/check/check.css'
import { CheckSteps, type StepInfo } from '../components/check/CheckSteps.tsx'
import {
  checkTitle,
  expiresSoon,
  formatDateTime,
  formatDuration,
  formatWhen,
  plural,
  PROCUREMENT_LABELS,
  timeLeft,
} from '../components/check/labels.ts'
import { ProtocolStep } from '../components/check/ProtocolStep.tsx'
import { RequisitesCard } from '../components/check/RequisitesCard.tsx'
import { RisksStep } from '../components/check/RisksStep.tsx'
import { useProtocol } from '../components/check/useProtocol.ts'
import { PageSkeleton } from '../components/PageSkeleton.tsx'
import { useIsMobile, useIsNarrow } from '../components/useIsMobile.ts'

const POLL_MS = 2000

type Step = 'risks' | 'protocol'

function money(value: number): string {
  return value.toFixed(2).replace('.', ',')
}

interface ReadyProps {
  check: CheckDetail
  step: Step
  readOnly: boolean
  mobile: boolean
  narrow: boolean
  /** Строки под шагами: реквизиты и замечания чтения. */
  extras: ReactNode
  onStep: (step: Step) => void
  onDownloaded: () => void
}

/**
 * Готовая проверка: шаги «Риски» и «Протокол» с общим протоколом. Пересоздаётся после нового анализа
 * (ключ — время его окончания): черновик протокола собирается заново.
 */
function ReadyCheck({ check, step, readOnly, mobile, narrow, extras, onStep, onDownloaded }: ReadyProps) {
  const protocol = useProtocol(check.id, readOnly)
  const result = check.result
  if (!result) return null
  const risks = result.answer.risks
  const rows = protocol.state?.rows ?? []
  const inProtocol = rows.filter((row) => row.include && row.risk_id).length
  const own = rows.filter((row) => row.include && !row.risk_id).length
  const duration = check.analysis?.duration_seconds
  const downloaded = check.protocol_downloaded_at
  const steps: StepInfo[] = [
    { key: 'analysis', title: 'Анализ', detail: duration ? `готово за ${formatDuration(duration)}` : 'готово', state: 'done' },
    {
      key: 'risks',
      title: 'Риски',
      detail: protocol.state
        ? `${inProtocol} из ${risks.length} в протоколе${own ? `, своих ${own}` : ''}`
        : plural(risks.length, ['риск', 'риска', 'рисков']),
      state: step === 'risks' ? 'current' : 'done',
      onOpen: () => onStep('risks'),
    },
    {
      key: 'protocol',
      title: 'Протокол',
      detail: downloaded
        ? `${check.protocol_format === 'pdf' ? 'PDF' : 'Word'} скачан в ${formatWhen(downloaded)}`
        : 'проверить и скачать',
      state: step === 'protocol' ? 'current' : downloaded ? 'done' : 'todo',
      onOpen: () => onStep('protocol'),
    },
  ]
  const action =
    step === 'risks' ? (
      <Button type="primary" onClick={() => onStep('protocol')}>
        К протоколу <ArrowRightOutlined />
      </Button>
    ) : (
      <Button icon={<ArrowLeftOutlined />} onClick={() => onStep('risks')}>
        К рискам
      </Button>
    )
  return (
    <>
      <CheckSteps steps={steps} action={action} mobile={mobile} />
      {extras}
      {step === 'risks' ? (
        <RisksStep
          result={result}
          blocks={check.blocks ?? []}
          protocol={protocol}
          mobile={mobile}
          narrow={narrow}
          onNext={() => onStep('protocol')}
        />
      ) : (
        <ProtocolStep
          protocol={protocol}
          risks={risks}
          mobile={mobile}
          narrow={narrow}
          onBack={() => onStep('risks')}
          onDownloaded={onDownloaded}
        />
      )}
    </>
  )
}

/** Проверка по шагам «Анализ → Риски → Протокол». */
export function CheckPage() {
  const { id = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const { user } = useAuth()
  const { message, modal } = App.useApp()
  const navigate = useNavigate()
  const mobile = useIsMobile()
  const narrow = useIsNarrow()
  const [check, setCheck] = useState<CheckDetail | null>(null)
  const [error, setError] = useState<{ status: number; text: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setCheck(await getCheck(id))
      setError(null)
    } catch (err) {
      setError(
        err instanceof ApiError ? { status: err.status, text: err.message } : { status: 0, text: 'Не удалось загрузить проверку' },
      )
    }
  }, [id])

  useEffect(() => {
    // Загрузка при открытии страницы и при переходе к другой проверке.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
  }, [load])

  const polling = check !== null && isInProgress(check)
  useEffect(() => {
    if (!polling) return
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [polling, load])

  if (error && !check) {
    return (
      <Result
        status={error.status === 404 ? '404' : 'error'}
        title={error.status === 404 ? 'Проверка не найдена' : 'Не удалось открыть проверку'}
        subTitle={error.status === 404 ? 'Её удалили вручную или по сроку хранения.' : error.text}
        extra={
          <Link to="/">
            <Button type="primary">К списку проверок</Button>
          </Link>
        }
      />
    )
  }
  if (!check) {
    return <PageSkeleton />
  }

  const isAdmin = user?.role === 'admin'
  const ownCheck = !isAdmin || check.owner_login === null || check.owner_login === user?.login
  const ready = check.status === 'ready' && check.result !== null
  const step: Step = params.get('step') === 'protocol' ? 'protocol' : 'risks'
  const canAnalyze = ownCheck && !!check.blocks?.length && !isInProgress(check)
  const { title, fromContract } = checkTitle(check)
  const meta = check.analysis

  const go = (next: Step) => {
    setParams(next === 'protocol' ? { step: 'protocol' } : {})
    window.scrollTo({ top: 0 })
  }

  const analyze = async () => {
    setBusy(true)
    try {
      await analyzeCheck(check.id)
      message.success('Анализ запущен')
      setParams({})
      await load()
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : 'Не удалось запустить анализ')
    } finally {
      setBusy(false)
    }
  }

  // Повтор заменяет результат и правки протокола и снова тратит лимит или деньги — переспросить.
  const confirmAnalyze = () => {
    if (!ready) {
      void analyze()
      return
    }
    modal.confirm({
      title: 'Проанализировать договор заново?',
      content: 'Текущий результат и правки протокола будут заменены. Анализ снова расходует лимит подписки или деньги на ключе.',
      okText: 'Запустить',
      cancelText: 'Отмена',
      onOk: analyze,
    })
  }

  const confirmDelete = () =>
    modal.confirm({
      title: 'Удалить проверку сейчас?',
      content: 'Файл, результат анализа и правки протокола удалятся без возможности восстановления.',
      okText: 'Удалить',
      okButtonProps: { danger: true },
      cancelText: 'Отмена',
      onOk: async () => {
        try {
          await deleteCheck(check.id)
          message.success('Проверка удалена')
          navigate('/')
        } catch (err) {
          message.error(err instanceof ApiError ? err.message : 'Не удалось удалить проверку')
        }
      },
    })

  const metaLine = [
    fromContract ? check.buyer_name : null,
    fromContract ? `файл «${check.file_name}»` : null,
    check.procurement !== 'unknown' ? PROCUREMENT_LABELS[check.procurement] : null,
    isAdmin && check.owner_login ? `менеджер ${check.owner_login}` : null,
    `загружен ${formatDateTime(check.created_at)}`,
  ].filter(Boolean)

  const extras = (
    <>
      {check.requisites && <RequisitesCard report={check.requisites} />}
      {/* Скан, скрытый текст, повторы номеров — видно сразу: от этого зависит, верить ли цитатам. */}
      {check.warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="При чтении документа"
          description={
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {check.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      )}
    </>
  )

  const analysisSteps: StepInfo[] = [
    {
      key: 'analysis',
      title: 'Анализ',
      detail: isInProgress(check)
        ? check.status === 'analyzing'
          ? 'идёт анализ'
          : 'читаю договор'
        : check.status === 'failed'
          ? 'не выполнен'
          : 'не запущен',
      state: isInProgress(check) ? 'current' : check.status === 'failed' ? 'error' : 'warn',
    },
    { key: 'risks', title: 'Риски', detail: 'после анализа', state: 'todo' },
    { key: 'protocol', title: 'Протокол', detail: 'после рисков', state: 'todo' },
  ]

  return (
    <div className="z-check">
      <Link to="/" className="z-back">
        <ArrowLeftOutlined aria-hidden="true" />
        {isAdmin ? 'Проверки' : 'Мои проверки'}
      </Link>
      <div className="z-check-head">
        <div>
          <h1 className="z-page-title">{title}</h1>
          <p className="z-check-meta">
            {metaLine.join(' · ')} ·{' '}
            <span style={expiresSoon(check.expires_at) ? { color: 'var(--z-warning)', fontWeight: 600 } : undefined}>
              удалится {timeLeft(check.expires_at)}
            </span>
          </p>
        </div>
        <Dropdown
          trigger={['click']}
          placement="bottomRight"
          menu={{
            items: [
              ...(canAnalyze
                ? [
                    {
                      key: 'analyze',
                      icon: ready ? <ReloadOutlined /> : <PlayCircleOutlined />,
                      label: ready ? 'Повторить анализ' : 'Запустить анализ',
                    },
                  ]
                : []),
              { key: 'delete', icon: <DeleteOutlined />, label: 'Удалить проверку', danger: true },
            ],
            onClick: ({ key }) => {
              if (key === 'analyze') confirmAnalyze()
              else if (key === 'delete') confirmDelete()
            },
          }}
        >
          <Button icon={<MoreOutlined />} aria-label="Ещё действия с проверкой" loading={busy} />
        </Dropdown>
      </div>

      {ready ? (
        <ReadyCheck
          key={`${check.id}:${meta?.finished_at ?? ''}`}
          check={check}
          step={step}
          readOnly={!ownCheck}
          mobile={mobile}
          narrow={narrow}
          extras={extras}
          onStep={go}
          onDownloaded={() => void load()}
        />
      ) : (
        <>
          <CheckSteps steps={analysisSteps} mobile={mobile} />
          {extras}
          <AnalysisStep check={check} isAdmin={isAdmin} ownCheck={ownCheck} busy={busy} onAnalyze={() => void analyze()} />
        </>
      )}

      {/* Модель, токены и стоимость — администратору: менеджеру это не нужно. */}
      {isAdmin && meta && !meta.skipped && meta.model && (
        <Collapse
          size="small"
          ghost
          items={[
            {
              key: 'details',
              label: 'Подробности анализа',
              children: (
                <Descriptions
                  size="small"
                  column={{ xs: 1, md: 2, xl: 4 }}
                  items={[
                    { key: 'model', label: 'Модель', children: `${meta.model}${meta.profile ? ` (${meta.profile})` : ''}` },
                    {
                      key: 'tokens',
                      label: 'Токены',
                      children: meta.input_tokens != null ? `${meta.input_tokens} / ${meta.output_tokens ?? '—'}` : '—',
                    },
                    {
                      key: 'cost',
                      label: 'Стоимость',
                      children: meta.cost_rub != null ? `${meta.cost_estimated ? '≈ ' : ''}${money(meta.cost_rub)} ₽` : '—',
                    },
                    {
                      key: 'time',
                      label: 'Время',
                      children: meta.duration_seconds != null ? formatDuration(meta.duration_seconds) : '—',
                    },
                  ]}
                />
              ),
            },
          ]}
        />
      )}
    </div>
  )
}
