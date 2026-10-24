import { CheckOutlined, CloseOutlined, ExclamationOutlined, LoadingOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import { type ReactNode, useEffect, useState } from 'react'
import { Link } from 'react-router'

import { type CheckDetail, isInProgress } from '../../api/checks.ts'
import { formatDuration, plural, progressText } from './labels.ts'

type StageState = 'done' | 'current' | 'todo' | 'warn' | 'error'

function Stage({ state, title, children }: { state: StageState; title: string; children: ReactNode }) {
  const icon = {
    done: <CheckOutlined />,
    current: <LoadingOutlined />,
    todo: null,
    warn: <ExclamationOutlined />,
    error: <CloseOutlined />,
  }[state]
  return (
    <div className={`z-stage is-${state}`}>
      <span className="z-stage-icon" aria-hidden="true">
        {icon}
      </span>
      <div>
        <b>{title}</b>
        <span>{children}</span>
      </div>
    </div>
  )
}

/** Секунды с начала анализа; обновляются раз в секунду, пока анализ идёт. */
function useElapsed(startedAt: string | undefined): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!startedAt) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt])
  return startedAt ? Math.max(0, (now - new Date(startedAt).getTime()) / 1000) : null
}

function readSummary(check: CheckDetail): string {
  const blocks = plural(check.blocks?.length ?? 0, ['абзац', 'абзаца', 'абзацев'])
  const pages = check.page_count ? `, ${check.page_count} стр.` : ''
  return `${check.ocr_used ? 'Скан распознан' : 'Прочитан'}: ${blocks}${pages}`
}

interface Props {
  check: CheckDetail
  isAdmin: boolean
  ownCheck: boolean
  busy: boolean
  onAnalyze: () => void
}

/** Шаг «Анализ»: чтение и анализ по этапам, а если не вышло — почему и что сделать. */
export function AnalysisStep({ check, isAdmin, ownCheck, busy, onAnalyze }: Props) {
  const reading = check.status === 'uploaded' || check.status === 'extracting'
  const elapsed = useElapsed(check.status === 'analyzing' ? check.analysis?.started_at : undefined)
  const canAnalyze = ownCheck && !!check.blocks?.length

  if (isInProgress(check)) {
    const progress = progressText(check.progress, isAdmin)
    return (
      <section className="z-card z-analysis" aria-live="polite" aria-label="Ход анализа">
        <Stage state={reading ? 'current' : 'done'} title="Чтение договора">
          {reading ? (progress ?? 'Ожидает обработки') : readSummary(check)}
        </Stage>
        <Stage state={reading ? 'todo' : 'current'} title="Анализ рисков">
          {reading
            ? 'Начнётся сразу после чтения'
            : [progress ?? 'Анализ договора', elapsed !== null ? `идёт ${formatDuration(elapsed)}` : null]
                .filter(Boolean)
                .join(' · ')}
        </Stage>
        <p className="z-analysis-note">
          Обычно анализ занимает несколько минут. Страницу можно закрыть: результат сохранится.
        </p>
      </section>
    )
  }

  if (check.status === 'failed' && !check.blocks?.length) {
    return (
      <section className="z-card z-analysis" aria-label="Договор не прочитан">
        <Stage state="error" title="Договор не прочитан">
          {check.error ?? 'Неизвестная ошибка.'}
        </Stage>
        <div>
          <Link to="/">
            <Button>К списку — загрузить другой файл</Button>
          </Link>
        </div>
      </section>
    )
  }

  const skipped = check.analysis?.skipped
  const failed = check.status === 'failed'
  return (
    <section className="z-card z-analysis" aria-label={failed ? 'Анализ не выполнен' : 'Анализ не запущен'}>
      <Stage state="done" title="Чтение договора">
        {readSummary(check)}
      </Stage>
      <Stage state={failed ? 'error' : 'warn'} title={failed ? 'Анализ не выполнен' : 'Анализ не запущен'}>
        {failed
          ? (check.error ?? 'Неизвестная ошибка.')
          : skipped
            ? 'Не было действующего подключения ИИ.'
            : 'Договор прочитан, анализ ещё не запускался.'}
      </Stage>
      {canAnalyze ? (
        <>
          {!failed && skipped && (
            <p className="z-analysis-note">
              Когда администратор настроит подключение, нажмите «Запустить анализ». Сам анализ не запустится.
            </p>
          )}
          <div>
            <Button type="primary" icon={<PlayCircleOutlined />} loading={busy} onClick={onAnalyze}>
              {failed ? 'Запустить анализ снова' : 'Запустить анализ'}
            </Button>
          </div>
        </>
      ) : (
        <p className="z-analysis-note">Анализ запускает менеджер, который загрузил договор.</p>
      )}
    </section>
  )
}
