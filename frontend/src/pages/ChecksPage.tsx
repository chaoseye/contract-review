import { LoadingOutlined, MoreOutlined, PlayCircleOutlined, UploadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Dropdown, Upload } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { analyzeCheck, type CheckSummary, deleteCheck, getLimits, isInProgress, type Limits, listChecks } from '../api/checks.ts'
import { ApiError } from '../api/client.ts'
import { useAuth } from '../auth/useAuth.ts'
import {
  checkTitle,
  expiresSoon,
  formatSize,
  formatWhen,
  plural,
  progressText,
  timeLeft,
  UPLOAD_ACCEPT,
} from '../components/check/labels.ts'
import { UploadModal } from '../components/UploadModal.tsx'
import { useIsMobile } from '../components/useIsMobile.ts'
import './checks.css'

const POLL_MS = 3000

type Bar = 'ok' | 'cur' | 'warn' | 'err' | ''

/** Где проверка на пути «Анализ → Риски → Протокол»: три полосы и подпись. */
function stage(check: CheckSummary, isAdmin: boolean): { bars: [Bar, Bar, Bar]; title: string; detail: string | null } {
  switch (check.status) {
    case 'uploaded':
    case 'extracting':
      return { bars: ['cur', '', ''], title: 'Чтение договора', detail: progressText(check.progress, isAdmin) }
    case 'analyzing':
      return { bars: ['cur', '', ''], title: 'Анализ', detail: progressText(check.progress, isAdmin) }
    case 'extracted':
      return { bars: ['warn', '', ''], title: 'Анализ не запущен', detail: 'договор прочитан' }
    case 'failed':
      return { bars: ['err', '', ''], title: 'Не получилось', detail: check.error }
    case 'ready':
      if (check.protocol_downloaded_at) {
        const format = check.protocol_format === 'pdf' ? 'PDF' : 'Word'
        return { bars: ['ok', 'ok', 'ok'], title: 'Протокол скачан', detail: `${format}, ${formatWhen(check.protocol_downloaded_at)}` }
      }
      return {
        bars: ['ok', 'cur', ''],
        title: 'Риски',
        detail:
          check.risk_count !== null
            ? [
                plural(check.risk_count, ['риск', 'риска', 'рисков']),
                check.stop_count ? plural(check.stop_count, ['стоп-фактор', 'стоп-фактора', 'стоп-факторов']) : null,
              ]
                .filter(Boolean)
                .join(', ')
            : null,
      }
  }
}

/** «Мои проверки»: загрузка договора прямо на странице и проверки в работе. */
export function ChecksPage() {
  const { user } = useAuth()
  const { message, modal } = App.useApp()
  const navigate = useNavigate()
  const mobile = useIsMobile()
  const [checks, setChecks] = useState<CheckSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [limits, setLimits] = useState<Limits | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [starting, setStarting] = useState<string | null>(null)
  const isAdmin = user?.role === 'admin'

  const load = useCallback(async () => {
    try {
      setChecks(await listChecks())
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось загрузить список проверок')
    }
  }, [])

  useEffect(() => {
    // Первая загрузка списка и лимитов при открытии страницы.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    getLimits()
      .then(setLimits)
      .catch(() => setLimits(null))
  }, [load])

  const busy = checks?.some(isInProgress) ?? false
  useEffect(() => {
    if (!busy) return
    const timer = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(timer)
  }, [busy, load])

  const remove = (check: CheckSummary) =>
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
          await load()
        } catch (err) {
          message.error(err instanceof ApiError ? err.message : 'Не удалось удалить проверку')
        }
      },
    })

  const analyze = async (check: CheckSummary) => {
    setStarting(check.id)
    try {
      await analyzeCheck(check.id)
      message.success('Анализ запущен')
      await load()
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : 'Не удалось запустить анализ')
    } finally {
      setStarting(null)
    }
  }

  const ttl = limits ? `${limits.check_ttl_hours} ч` : '24 ч'
  return (
    <div className="z-list">
      <div>
        <h1 className="z-page-title">{isAdmin ? 'Проверки' : 'Мои проверки'}</h1>
        <p className="z-page-sub">
          Загрузите договор покупателя — система найдёт риски и соберёт протокол разногласий.
        </p>
      </div>
      <Upload.Dragger
        className="z-drop"
        accept={UPLOAD_ACCEPT}
        multiple={false}
        showUploadList={false}
        beforeUpload={(chosen) => {
          setFile(chosen)
          setUploading(true)
          return false
        }}
      >
        <div className="z-drop-body">
          <span className="z-drop-icon" aria-hidden="true">
            <UploadOutlined />
          </span>
          <span className="z-drop-text">
            <b>{mobile ? 'Загрузить договор' : 'Перетащите договор сюда'}</b>
            <span>
              {mobile ? 'Word, RTF или PDF, в том числе скан' : 'или нажмите, чтобы выбрать файл · Word, RTF или PDF, в том числе скан'}
              {limits ? `, до ${limits.max_upload_mb} МБ` : ''}
            </span>
          </span>
          {!mobile && <span className="z-drop-note">Дальше спросим режим закупки и что учесть в сделке</span>}
        </div>
      </Upload.Dragger>
      {error && <Alert type="error" showIcon title={error} />}
      {checks === null && !error && (
        <div className="z-skeleton" style={{ padding: 0 }} aria-busy="true" aria-label="Загрузка списка">
          <span style={{ height: 76 }} />
          <span style={{ height: 76 }} />
        </div>
      )}
      {checks !== null && checks.length === 0 && (
        <p className="z-empty">
          Проверок пока нет. Договор и проверка хранятся {ttl} после последнего действия, потом удаляются.
        </p>
      )}
      {checks !== null && checks.length > 0 && (
        <>
          <h2 className="z-list-sub">
            В работе <span>{checks.length}</span>
          </h2>
          <div className="z-rows">
            {checks.map((check) => {
              const { title, fromContract } = checkTitle(check)
              const info = stage(check, isAdmin)
              const own = !isAdmin || check.owner_login === user?.login
              const sub = [
                fromContract ? check.buyer_name : null,
                fromContract ? check.file_name : `${check.file_kind.toUpperCase()}, ${formatSize(check.file_size)}`,
                isAdmin && check.owner_login ? `менеджер ${check.owner_login}` : null,
              ]
                .filter(Boolean)
                .join(' · ')
              return (
                <article key={check.id} className="z-row">
                  <div className="z-row-main">
                    <Link className="z-row-title" to={`/checks/${check.id}`}>
                      {title}
                    </Link>
                    <span className="z-row-sub">{sub}</span>
                  </div>
                  <div className="z-row-stage">
                    <span className="z-bars" aria-hidden="true">
                      {info.bars.map((bar, index) => (
                        <span key={index} className={bar ? `is-${bar}` : undefined} />
                      ))}
                    </span>
                    <span className={`z-stage-text${info.bars[0] === 'warn' ? ' is-warn' : info.bars[0] === 'err' ? ' is-err' : ''}`}>
                      {isInProgress(check) && <LoadingOutlined aria-hidden="true" style={{ marginRight: 6 }} />}
                      <b>{info.title}</b>
                      {info.detail ? ` · ${info.detail}` : ''}
                    </span>
                  </div>
                  <div className="z-row-actions">
                    <span className={expiresSoon(check.expires_at) ? 'z-left is-soon' : 'z-left'}>
                      удалится {timeLeft(check.expires_at)}
                    </span>
                    {check.status === 'extracted' && own ? (
                      <Button type="primary" icon={<PlayCircleOutlined />} loading={starting === check.id} onClick={() => void analyze(check)}>
                        Запустить анализ
                      </Button>
                    ) : (
                      <Button onClick={() => navigate(`/checks/${check.id}`)}>Открыть</Button>
                    )}
                    <Dropdown
                      trigger={['click']}
                      placement="bottomRight"
                      menu={{
                        items: [{ key: 'delete', label: 'Удалить проверку', danger: true }],
                        onClick: () => remove(check),
                      }}
                    >
                      <Button type="text" icon={<MoreOutlined />} aria-label={`Ещё: ${title}`} />
                    </Dropdown>
                  </div>
                </article>
              )
            })}
          </div>
        </>
      )}
      <UploadModal
        open={uploading}
        file={file}
        limits={limits}
        onFile={setFile}
        onClose={() => {
          setUploading(false)
          setFile(null)
        }}
        onUploaded={(check) => {
          setUploading(false)
          setFile(null)
          navigate(`/checks/${check.id}`)
        }}
      />
    </div>
  )
}
