import { useCallback, useEffect, useRef, useState } from 'react'

import {
  downloadProtocol,
  type EditableRow,
  getProtocol,
  type ProtocolMeta,
  type ProtocolOut,
  type ProtocolState,
  resetProtocol,
  saveProtocol,
} from '../../api/checks.ts'
import { ApiError } from '../../api/client.ts'

// Правки сохраняются сами через паузу в наборе: кнопки «Сохранить» нет, и уход со страницы
// правки не теряет.
const SAVE_DELAY_MS = 800

export type SaveStatus = 'saved' | 'pending' | 'saving' | 'error'

export interface ProtocolController {
  /** Ответ сервера: вступление по шаблону, готовность организации. */
  loaded: ProtocolOut | null
  /** Текущее состояние с правками, которые ещё могут сохраняться. */
  state: ProtocolState | null
  loadError: string | null
  status: SaveStatus
  saveError: string | null
  savedAt: Date | null
  readOnly: boolean
  setRow: (id: string, patch: Partial<EditableRow>) => void
  setMeta: (patch: Partial<ProtocolMeta>) => void
  setRows: (rows: EditableRow[]) => void
  /** Сохранить сразу, не дожидаясь паузы. false — сохранить не удалось. */
  flush: () => Promise<boolean>
  reset: () => Promise<void>
  download: (format: 'docx' | 'pdf', signature: boolean, stamp: boolean) => Promise<void>
}

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

/**
 * Протокол готовой проверки: черновик из анализа, правки и автосохранение. Компонент, который его
 * использует, пересоздаётся с ключом из id проверки и времени анализа: после нового анализа — новый черновик.
 */
export function useProtocol(checkId: string, readOnly: boolean): ProtocolController {
  const [loaded, setLoaded] = useState<ProtocolOut | null>(null)
  const [state, setState] = useState<ProtocolState | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [status, setStatus] = useState<SaveStatus>('saved')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<Date | null>(null)

  const stateRef = useRef<ProtocolState | null>(null)
  const version = useRef(0)
  const savedVersion = useRef(0)
  const timer = useRef<number | undefined>(undefined)
  const inFlight = useRef<Promise<boolean> | null>(null)

  useEffect(() => {
    let cancelled = false
    getProtocol(checkId)
      .then((out) => {
        if (cancelled) return
        stateRef.current = out.state
        setLoaded(out)
        setState(out.state)
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(message(error, 'Не удалось загрузить протокол'))
      })
    return () => {
      cancelled = true
    }
  }, [checkId])

  /** Отправить правки, пока сохранённое не догонит набранное. */
  const save = useCallback(async (): Promise<boolean> => {
    if (readOnly) return true
    for (;;) {
      if (inFlight.current) {
        if (!(await inFlight.current)) return false
        continue
      }
      const body = stateRef.current
      const sending = version.current
      if (!body || sending === savedVersion.current) return true
      setStatus('saving')
      const request = saveProtocol(checkId, body).then(
        (out) => {
          savedVersion.current = sending
          // Своё состояние ответом не заменяется: за время запроса менеджер мог продолжить набор.
          setLoaded((current) => ({ ...out, state: current?.state ?? out.state }))
          setSaveError(null)
          setSavedAt(new Date())
          setStatus(version.current === sending ? 'saved' : 'pending')
          return true
        },
        (error: unknown) => {
          setSaveError(message(error, 'Правки не сохранились. Проверьте связь и повторите.'))
          setStatus('error')
          return false
        },
      )
      inFlight.current = request
      const ok = await request
      inFlight.current = null
      if (!ok) return false
    }
  }, [checkId, readOnly])

  const change = useCallback(
    (next: ProtocolState) => {
      if (readOnly) return
      stateRef.current = next
      version.current += 1
      setState(next)
      setStatus('pending')
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => void save(), SAVE_DELAY_MS)
    },
    [readOnly, save],
  )

  const setRow = useCallback(
    (id: string, patch: Partial<EditableRow>) => {
      const current = stateRef.current
      if (current) change({ ...current, rows: current.rows.map((row) => (row.id === id ? { ...row, ...patch } : row)) })
    },
    [change],
  )

  const setMeta = useCallback(
    (patch: Partial<ProtocolMeta>) => {
      const current = stateRef.current
      if (current) change({ ...current, meta: { ...current.meta, ...patch } })
    },
    [change],
  )

  const setRows = useCallback(
    (rows: EditableRow[]) => {
      const current = stateRef.current
      if (current) change({ ...current, rows })
    },
    [change],
  )

  const flush = useCallback(() => {
    window.clearTimeout(timer.current)
    return save()
  }, [save])

  const reset = useCallback(async () => {
    window.clearTimeout(timer.current)
    if (inFlight.current) await inFlight.current
    const out = await resetProtocol(checkId)
    stateRef.current = out.state
    version.current += 1
    savedVersion.current = version.current
    setLoaded(out)
    setState(out.state)
    setStatus('saved')
    setSaveError(null)
    setSavedAt(null)
  }, [checkId])

  const download = useCallback(
    async (format: 'docx' | 'pdf', signature: boolean, stamp: boolean) => {
      if (!(await flush())) throw new ApiError(0, 'Правки не сохранились, протокол не скачан. Повторите.')
      await downloadProtocol(checkId, format, signature, stamp)
    },
    [checkId, flush],
  )

  // Ушли со страницы проверки, пока правки ждали паузы, — сохранить сразу.
  useEffect(
    () => () => {
      window.clearTimeout(timer.current)
      if (version.current !== savedVersion.current) void save()
    },
    [save],
  )

  // Закрывают вкладку, а правки не сохранены — браузер переспросит.
  const unsaved = status !== 'saved'
  useEffect(() => {
    if (!unsaved || readOnly) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved, readOnly])

  return { loaded, state, loadError, status, saveError, savedAt, readOnly, setRow, setMeta, setRows, flush, reset, download }
}
