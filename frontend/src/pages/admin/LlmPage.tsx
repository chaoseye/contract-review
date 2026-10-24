import {
  ApiOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  DeleteOutlined,
  EditOutlined,
  MoreOutlined,
  PlusOutlined,
  StarOutlined,
} from '@ant-design/icons'
import {
  Alert,
  App,
  AutoComplete,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Dropdown,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { type CSSProperties, type ReactNode, useCallback, useEffect, useRef, useState } from 'react'

import {
  activateProfile,
  type CodexState,
  codexCancel,
  codexLogin,
  codexLogout,
  type ConnectionCheck,
  createProfile,
  type Effort,
  deleteProfile,
  getCodex,
  type LlmKind,
  type LlmPreset,
  type LlmProfile,
  listModels,
  listPresets,
  listProfiles,
  type ModelInfo,
  type ProfileParams,
  type ResponseFormat,
  testProfile,
  updateProfile,
} from '../../api/admin.ts'
import { useIsMobile, useIsNarrow } from '../../components/useIsMobile.ts'
import { errorText, money, pricePerMillion, tokens } from './format.ts'
import { useAdminContext } from './readiness.ts'
import { SectionTitle } from './SectionTitle.tsx'

const CODEX_POLL_MS = 3000
// Лимит времени нового подключения Codex — как в README: `llm-add Codex --kind codex_cli --timeout 900`.
const CODEX_TIMEOUT_SECONDS = 900
// Подпись кнопки в шаге переносится, если не помещается в строку, — на телефоне.
// В одну строку кнопка той же высоты, что обычная: 4 + 22 + 4 и рамка.
const WRAP_BUTTON_STYLE: CSSProperties = {
  maxWidth: '100%',
  height: 'auto',
  paddingBlock: 4,
  lineHeight: '22px',
  whiteSpace: 'normal',
}
// Что владелец аккаунта ChatGPT делает до входа: без входа по коду устройства Codex не войдёт.
const PREPARATION = [
  'Аккаунт — менеджера или компании: давать доступ к подписке другим людям условия OpenAI не разрешают. Поэтому Codex подходит, когда системой пользуется один менеджер.',
  'В настройках ChatGPT выключите «Improve the model for everyone».',
  'В разделе «Безопасность» (Security) включите вход по коду устройства для Codex. В аккаунте компании на тарифе Business его включает администратор рабочего пространства.',
  'OpenAI не обслуживает Россию: сервер системы должен стоять в другой стране.',
]
const KIND_LABELS: Record<LlmKind, string> = {
  openai_compat: 'Совместимый с OpenAI API',
  anthropic: 'API Anthropic (Claude)',
  codex_cli: 'Codex CLI (подписка ChatGPT)',
}
const ANTHROPIC_URL = 'https://api.anthropic.com'
const FORMAT_OPTIONS: { value: ResponseFormat; label: string }[] = [
  { value: 'auto', label: 'Автоматически (схема, если модель умеет)' },
  { value: 'json_schema', label: 'Строгая схема (structured outputs)' },
  { value: 'json_object', label: 'JSON-режим' },
  { value: 'none', label: 'Только инструкция' },
]
// У Anthropic нет JSON-режима.
const ANTHROPIC_FORMAT_OPTIONS = FORMAT_OPTIONS.filter((option) => option.value !== 'json_object')
const EFFORT_OPTIONS: { value: Effort; label: string }[] = [
  { value: 'low', label: 'low — быстрее и дешевле' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high — для анализа договоров' },
  { value: 'xhigh', label: 'xhigh' },
  { value: 'max', label: 'max — глубже всего, дороже всего' },
]

interface ProfileForm {
  preset?: string
  name: string
  kind: LlmKind
  base_url: string
  model: string
  api_key: string
  clear_key: boolean
  timeout_seconds: number
  max_retries: number
  response_format: ResponseFormat
  temperature: number | null
  max_output_tokens: number | null
  russia_only: boolean
  effort: Effort | null
  extra_body: string
  codex_config: string
  activate: boolean
}

const DEFAULTS: Omit<ProfileForm, 'name'> = {
  kind: 'openai_compat',
  base_url: '',
  model: '',
  api_key: '',
  clear_key: false,
  timeout_seconds: 300,
  max_retries: 2,
  response_format: 'auto',
  temperature: null,
  max_output_tokens: null,
  russia_only: false,
  effort: null,
  extra_body: '',
  codex_config: '',
  activate: false,
}

/** Серверы по стране выбирает только RouterAI: у других адресов «Только серверы в РФ» нет. */
function isRouterAi(baseUrl: string | undefined): boolean {
  try {
    const host = new URL(baseUrl ?? '').hostname.toLowerCase()
    return host === 'routerai.ru' || host.endsWith('.routerai.ru')
  } catch {
    return false
  }
}

/** Название, которого ещё нет в списке: «Codex», «Codex 2»… Названия подключений не повторяются. */
function freeName(base: string, profiles: LlmProfile[]): string {
  const taken = new Set(profiles.map((profile) => profile.name))
  let name = base
  for (let index = 2; taken.has(name); index += 1) name = `${base} ${index}`
  return name
}

function formFromProfile(profile: LlmProfile): ProfileForm {
  const params = profile.params
  return {
    ...DEFAULTS,
    name: profile.name,
    kind: profile.kind,
    base_url: profile.base_url,
    model: profile.model,
    timeout_seconds: params.timeout_seconds,
    max_retries: params.max_retries,
    response_format: params.response_format,
    temperature: params.temperature,
    max_output_tokens: params.max_output_tokens,
    russia_only: params.russia_only,
    effort: params.effort,
    extra_body: Object.keys(params.extra_body).length > 0 ? JSON.stringify(params.extra_body, null, 2) : '',
    codex_config: params.codex_config.join('\n'),
  }
}

function paramsFromForm(values: ProfileForm): Partial<ProfileParams> {
  let extra: Record<string, unknown> = {}
  if (values.extra_body?.trim()) {
    const parsed: unknown = JSON.parse(values.extra_body)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('Дополнительные поля запроса — JSON-объект, например {"reasoning": {"effort": "high"}}')
    }
    extra = parsed as Record<string, unknown>
  }
  return {
    timeout_seconds: values.timeout_seconds,
    max_retries: values.max_retries,
    response_format: values.response_format,
    temperature: values.temperature ?? null,
    max_output_tokens: values.max_output_tokens ?? null,
    // Переключатель скрыт, когда адрес сменили с RouterAI на другой: его прежнее значение не отправляется.
    russia_only: values.kind === 'openai_compat' && isRouterAi(values.base_url) && values.russia_only,
    effort: values.kind === 'anthropic' ? (values.effort ?? null) : null,
    extra_body: extra,
    codex_config: (values.codex_config ?? '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean),
  }
}

function supportLabel(info: ModelInfo): string {
  const supported = info.supported_parameters
  if (supported === null) return '?'
  if (supported.includes('structured_outputs')) return 'схема'
  if (supported.includes('response_format')) return 'JSON'
  return '—'
}

function CheckReport({ report }: { report: ConnectionCheck }) {
  if (!report.ok) {
    return <Alert type="error" showIcon title={report.message} description={report.detail ? `Ответ провайдера: ${report.detail}` : undefined} />
  }
  const info = report.model_info
  return (
    <Flex vertical gap={8}>
      <Alert type="success" showIcon title="Связь есть" />
      <Descriptions
        size="small"
        column={1}
        items={[
          { key: 'model', label: 'Модель', children: report.model ?? '—' },
          ...(info
            ? [
                { key: 'context', label: 'Контекст', children: info.context_length ? `${info.context_length} токенов` : '—' },
                ...(info.max_output_tokens
                  ? [{ key: 'output', label: 'Ответ до', children: `${info.max_output_tokens} токенов` }]
                  : []),
                // Цен в списке моделей нет у OpenAI, Anthropic и локальных серверов.
                ...(info.currency
                  ? [
                      {
                        key: 'price',
                        label: 'Цена за 1 млн токенов',
                        children: `вход ${pricePerMillion(info.price_input, info.currency)}, выход ${pricePerMillion(info.price_output, info.currency)}${
                          info.countries?.includes('ru') ? ' (провайдеры с серверами в РФ)' : ''
                        }`,
                      },
                    ]
                  : []),
              ]
            : []),
          { key: 'format', label: 'Формат ответа', children: report.response_format ?? '—' },
          { key: 'time', label: 'Ответ за', children: report.duration_seconds != null ? `${report.duration_seconds} с` : '—' },
          { key: 'tokens', label: 'Токены', children: tokens(report.input_tokens, report.output_tokens) },
          { key: 'cost', label: 'Стоимость', children: `${report.cost_estimated ? '≈ ' : ''}${money(report.cost_rub, 4)}` },
        ]}
      />
      {report.notes.map((note) => (
        <Alert key={note} type="warning" showIcon title={note} />
      ))}
    </Flex>
  )
}

/** Подключение Codex, о котором говорят шаги: действующее, иначе первое в списке. */
function codexProfileOf(profiles: LlmProfile[]): LlmProfile | null {
  return (
    profiles.find((profile) => profile.kind === 'codex_cli' && profile.is_active) ??
    profiles.find((profile) => profile.kind === 'codex_cli') ??
    null
  )
}

/** Идёт ли анализ и через что — метка в шапке карточки Codex. */
function analysisState(loggedIn: boolean, active: LlmProfile | null): { tone: 'ok' | 'warn' | 'neutral'; text: string } {
  if (!active) return { tone: 'warn', text: 'Анализ пока не идёт' }
  if (active.kind !== 'codex_cli') return { tone: 'neutral', text: `Анализ идёт через «${active.name}»` }
  return loggedIn ? { tone: 'ok', text: 'Анализ идёт через Codex' } : { tone: 'warn', text: 'Анализ не идёт: нет входа' }
}

// open — шаг можно делать, но торопить не нужно; todo — шаг станет доступен после предыдущих.
type VStepState = 'done' | 'current' | 'open' | 'todo'

function VStep({ index, state, title, children }: { index: number; state: VStepState; title: string; children?: ReactNode }) {
  return (
    <li className={`z-vstep is-${state}`} aria-current={state === 'current' ? 'step' : undefined}>
      <span className="z-vstep-num" aria-hidden="true">
        {state === 'done' ? <CheckOutlined /> : index}
      </span>
      <div style={{ minWidth: 0 }}>
        <p className="z-vstep-title">
          <span className="z-sr">{state === 'done' ? `Шаг ${index}, выполнен: ` : `Шаг ${index}: `}</span>
          {title}
        </p>
        {children}
      </div>
    </li>
  )
}

interface CodexStepsProps {
  state: CodexState
  profiles: LlmProfile[]
  busy: boolean
  testing: boolean
  /** Результат «Проверить связь» для подключения Codex — в этом открытии страницы. */
  report: ConnectionCheck | null
  /** Подключение Codex создано или сделано действующим только что: следующий шаг — проверка связи. */
  fresh: boolean
  onLogin: () => void
  onCancel: () => void
  onLogout: () => Promise<void>
  onAdd: () => void
  onActivate: (profile: LlmProfile) => void
  onCheck: (profile: LlmProfile) => void
}

/**
 * Codex по шагам: подготовить аккаунт, войти, создать подключение, проверить связь. Вход только
 * сохраняет аккаунт ChatGPT на сервере, а анализ идёт через действующее подключение: без третьего шага
 * менеджер видит «подключение ИИ не настроено».
 */
function CodexSteps(props: CodexStepsProps) {
  const { state, profiles, busy, testing, report, fresh } = props
  const [showPreparation, setShowPreparation] = useState(false)
  const active = profiles.find((profile) => profile.is_active) ?? null
  const codex = codexProfileOf(profiles)
  const login = state.login
  const waiting = login?.state === 'waiting'
  const loggedIn = state.logged_in
  const status = analysisState(loggedIn, active)

  const preparation = (
    <ul className="z-vstep-list">
      {PREPARATION.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  )

  let loginStep: ReactNode
  if (loggedIn) {
    loginStep = (
      <>
        <p className="z-vstep-text">
          Вход выполнен, аккаунт сохранён на этом сервере.{' '}
          <Popconfirm
            title="Выйти из аккаунта ChatGPT в Codex?"
            description={codex?.is_active ? 'Анализ через Codex остановится до нового входа.' : undefined}
            okText="Выйти"
            cancelText="Отмена"
            onConfirm={props.onLogout}
          >
            <Button type="link" size="small" loading={busy} style={{ padding: 0 }}>
              Выйти из аккаунта
            </Button>
          </Popconfirm>
        </p>
        {state.message && <p className="z-hint">Codex: {state.message}</p>}
      </>
    )
  } else if (waiting) {
    loginStep = (
      <div className="z-vstep-box">
        {login.url && login.code ? (
          <>
            <p>
              Откройте{' '}
              <a href={login.url} target="_blank" rel="noreferrer">
                {login.url}
              </a>
              , войдите в аккаунт ChatGPT и введите код:
            </p>
            <Typography.Text className="z-code" copyable>
              {login.code}
            </Typography.Text>
            <p className="z-hint">Страница обновится сама, когда вход пройдёт.</p>
          </>
        ) : (
          <pre className="z-vstep-pre">{login.lines.join('\n') || 'Codex запускается…'}</pre>
        )}
        <div>
          <Button loading={busy} onClick={props.onCancel}>
            Отменить вход
          </Button>
        </div>
      </div>
    )
  } else {
    const failed = login?.state === 'failed'
    const lines = failed ? login.lines.slice(-3) : []
    loginStep = (
      <div className="z-vstep-box">
        <p>
          {failed ? (
            <>
              <b>Вход не выполнен.</b> Проверьте настройки из шага 1 и попробуйте ещё раз.
            </>
          ) : (
            'Нажмите кнопку — появятся ссылка и код. Откройте ссылку, войдите в аккаунт ChatGPT и введите код. Вход сохранится на этом сервере.'
          )}
        </p>
        {lines.length > 0 && <pre className="z-vstep-pre">{lines.join('\n')}</pre>}
        <div>
          <Button type="primary" loading={busy} onClick={props.onLogin}>
            Войти по коду
          </Button>
        </div>
      </div>
    )
  }

  let connectionState: VStepState
  let connectionStep: ReactNode
  if (codex?.is_active) {
    connectionState = 'done'
    connectionStep = <p className="z-vstep-text">Действует подключение «{codex.name}».</p>
  } else if (!loggedIn) {
    connectionState = 'todo'
    connectionStep = (
      <p className="z-vstep-text">
        {codex
          ? `Подключение «${codex.name}» уже есть — после входа сделайте его действующим.`
          : 'После входа: подключение, через которое договоры пойдут в Codex.'}
      </p>
    )
  } else if (!codex) {
    connectionState = 'current'
    connectionStep = (
      <div className="z-vstep-box">
        <p>
          Вход — это только доступ к аккаунту. Договоры пойдут в Codex, когда появится подключение. Форма откроется
          заполненной{active ? '.' : ', подключение станет действующим.'}
        </p>
        <div>
          <Button type="primary" icon={<PlusOutlined />} onClick={props.onAdd} style={WRAP_BUTTON_STYLE}>
            Создать подключение Codex
          </Button>
        </div>
      </div>
    )
  } else if (!active) {
    connectionState = 'current'
    connectionStep = (
      <div className="z-vstep-box">
        <p>Подключение «{codex.name}» есть, но не действующее — договоры не анализируются.</p>
        <div>
          <Button type="primary" icon={<StarOutlined />} onClick={() => props.onActivate(codex)} style={WRAP_BUTTON_STYLE}>
            Сделать действующим
          </Button>
        </div>
      </div>
    )
  } else {
    // Действует другое подключение: Codex — по желанию, без жёлтой отметки.
    connectionState = 'open'
    connectionStep = (
      <>
        <p className="z-vstep-text">
          Сейчас анализ идёт через «{active.name}». Чтобы он шёл через Codex, сделайте действующим «{codex.name}».
        </p>
        <div className="z-vstep-actions">
          <Button icon={<StarOutlined />} onClick={() => props.onActivate(codex)} style={WRAP_BUTTON_STYLE}>
            Сделать действующим
          </Button>
        </div>
      </>
    )
  }

  const canCheck = codex !== null && loggedIn
  const checkState: VStepState = !canCheck
    ? 'todo'
    : report?.ok
      ? 'done'
      : report || (fresh && codex.is_active)
        ? 'current'
        : 'open'

  return (
    <section className="z-card z-codex" aria-labelledby="codex-title">
      <div className="z-codex-head">
        <div>
          <h2 id="codex-title">Анализ через Codex по подписке ChatGPT</h2>
          <p>Через серверы OpenAI в США. Подходит, когда системой пользуется один менеджер.</p>
        </div>
        <span className={`z-state is-${status.tone}`}>{status.text}</span>
      </div>
      <ol className="z-vsteps">
        <VStep index={1} state={loggedIn ? 'done' : 'open'} title="Подготовьте аккаунт ChatGPT">
          {loggedIn ? (
            <>
              <p className="z-vstep-text">
                «Improve the model for everyone» в ChatGPT должно оставаться выключенным.{' '}
                <Button
                  type="link"
                  size="small"
                  onClick={() => setShowPreparation((value) => !value)}
                  aria-expanded={showPreparation}
                  style={{ padding: 0 }}
                >
                  {showPreparation ? 'Скрыть условия' : 'Все условия'}
                </Button>
              </p>
              {showPreparation && preparation}
            </>
          ) : (
            preparation
          )}
        </VStep>
        <VStep index={2} state={loggedIn ? 'done' : 'current'} title="Войдите по коду">
          {loginStep}
        </VStep>
        <VStep index={3} state={connectionState} title="Создайте подключение">
          {connectionStep}
        </VStep>
        <VStep index={4} state={checkState} title="Проверьте связь">
          <p className="z-vstep-text">Короткий запрос к модели: убедимся, что она отвечает.</p>
          {canCheck && (
            <div className="z-vstep-actions">
              <Button
                type={checkState === 'current' && !report ? 'primary' : 'default'}
                icon={<CheckCircleOutlined />}
                loading={testing}
                onClick={() => props.onCheck(codex)}
              >
                {report ? 'Проверить ещё раз' : 'Проверить связь'}
              </Button>
            </div>
          )}
          {canCheck && report && (
            <div className="z-vstep-report">
              {/* Строка «Logged in using ChatGPT» уже есть во втором шаге: предупреждением её не повторяем. */}
              <CheckReport report={{ ...report, notes: report.notes.filter((note) => note !== state.message) }} />
            </div>
          )}
        </VStep>
      </ol>
    </section>
  )
}

/** Codex не установлен или не отвечает: вход по подписке недоступен, подключения по ключу работают. */
function CodexUnavailable({ state }: { state: CodexState }) {
  return (
    <section className="z-card z-codex" aria-labelledby="codex-title">
      <div className="z-codex-head">
        <div>
          <h2 id="codex-title">Анализ через Codex по подписке ChatGPT</h2>
          <p>
            Codex на этом сервере недоступен, поэтому вход по подписке ChatGPT не работает. Подключения по ключу API
            работают и без него.
          </p>
        </div>
      </div>
      {state.message && <p className="z-hint">{state.message}</p>}
    </section>
  )
}

/** Подключения ИИ: профили, действующий профиль, проверка связи, модели с ценами, вход в Codex. */
export function LlmPage() {
  const { message, modal } = App.useApp()
  const { refresh } = useAdminContext()
  const mobile = useIsMobile()
  const narrow = useIsNarrow()
  const [profiles, setProfiles] = useState<LlmProfile[] | null>(null)
  const [presets, setPresets] = useState<LlmPreset[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<LlmProfile | 'new' | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<number | null>(null)
  const [report, setReport] = useState<{ profile: string; report: ConnectionCheck } | null>(null)
  const [codex, setCodex] = useState<CodexState | null>(null)
  const [codexBusy, setCodexBusy] = useState(false)
  const [codexReport, setCodexReport] = useState<{ id: number; report: ConnectionCheck } | null>(null)
  const [fresh, setFresh] = useState<number | null>(null)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [modelsTotal, setModelsTotal] = useState(0)
  const [loadingModels, setLoadingModels] = useState(false)
  const [form] = Form.useForm<ProfileForm>()
  const kind = Form.useWatch('kind', form)
  const baseUrl = Form.useWatch('base_url', form)
  const loggedIn = useRef<boolean | null>(null)

  const load = useCallback(async () => {
    try {
      setProfiles(await listProfiles())
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Не удалось загрузить подключения'))
    }
  }, [])

  // Вход в Codex появился или пропал — «Обзор» и отметка в меню тоже меняются.
  const applyCodex = useCallback(
    (next: CodexState) => {
      setCodex(next)
      if (loggedIn.current !== null && loggedIn.current !== next.logged_in) refresh()
      loggedIn.current = next.logged_in
    },
    [refresh],
  )

  const loadCodex = useCallback(async () => {
    try {
      applyCodex(await getCodex())
    } catch (err) {
      message.error(errorText(err, 'Не удалось узнать состояние Codex'))
    }
  }, [applyCodex, message])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load()
    void loadCodex()
    listPresets()
      .then(setPresets)
      .catch(() => setPresets([]))
  }, [load, loadCodex])

  const waiting = codex?.login?.state === 'waiting'
  useEffect(() => {
    if (!waiting) return
    const timer = setInterval(() => void loadCodex(), CODEX_POLL_MS)
    return () => clearInterval(timer)
  }, [waiting, loadCodex])

  const codexAct = async (action: () => Promise<CodexState>) => {
    setCodexBusy(true)
    try {
      applyCodex(await action())
    } catch (err) {
      message.error(errorText(err, 'Не получилось'))
    } finally {
      setCodexBusy(false)
    }
  }

  const open = (profile: LlmProfile | 'new', initial?: Partial<ProfileForm>) => {
    setModels([])
    setModelsTotal(0)
    form.resetFields()
    form.setFieldsValue(profile === 'new' ? { ...DEFAULTS, name: '', ...initial } : formFromProfile(profile))
    setEditing(profile)
  }

  // Подключение Codex из шага 3. Первое подключение сервер и так делает действующим, а если действующего
  // нет, галочка уже стоит.
  const openCodex = () =>
    open('new', {
      kind: 'codex_cli',
      name: freeName('Codex', profiles ?? []),
      timeout_seconds: CODEX_TIMEOUT_SECONDS,
      activate: !profiles?.some((profile) => profile.is_active),
    })

  const applyPreset = (id: string) => {
    const preset = presets.find((item) => item.id === id)
    if (!preset) return
    form.setFieldsValue({
      kind: preset.kind,
      base_url: preset.base_url,
      russia_only: preset.russia_only,
      effort: preset.effort,
      name: form.getFieldValue('name') || preset.title,
      ...(preset.kind === 'anthropic' && form.getFieldValue('response_format') === 'json_object'
        ? { response_format: 'auto' as const }
        : {}),
    })
  }

  // Тип выбран вручную: у Anthropic есть адрес по умолчанию и нет JSON-режима и «Только серверы в РФ».
  const changeKind = (value: LlmKind) => {
    if (value !== 'anthropic') return
    form.setFieldsValue({
      base_url: form.getFieldValue('base_url') || ANTHROPIC_URL,
      effort: form.getFieldValue('effort') ?? 'high',
      russia_only: false,
      ...(form.getFieldValue('response_format') === 'json_object' ? { response_format: 'auto' as const } : {}),
    })
  }

  const loadModels = async () => {
    const values = form.getFieldsValue()
    setLoadingModels(true)
    try {
      const result = await listModels({
        kind: values.kind,
        base_url: values.base_url ?? '',
        api_key: values.api_key?.trim() || null,
        profile_id: editing !== null && editing !== 'new' ? editing.id : null,
      })
      setModels(result.models)
      setModelsTotal(result.total)
      if (result.total === 0) message.info('Провайдер не вернул ни одной текстовой модели')
    } catch (err) {
      message.error(errorText(err, 'Не удалось получить список моделей'))
    } finally {
      setLoadingModels(false)
    }
  }

  const submit = async (values: ProfileForm) => {
    let params: Partial<ProfileParams>
    try {
      params = paramsFromForm(values)
    } catch (err) {
      message.error(err instanceof SyntaxError ? 'Дополнительные поля запроса — это не JSON' : String((err as Error).message))
      return
    }
    setSaving(true)
    try {
      if (editing === 'new') {
        const created = await createProfile({
          name: values.name,
          kind: values.kind,
          base_url: values.kind !== 'codex_cli' ? (values.base_url ?? '') : '',
          model: values.model ?? '',
          api_key: values.kind !== 'codex_cli' && values.api_key?.trim() ? values.api_key.trim() : null,
          params,
          activate: values.activate,
        })
        if (created.kind === 'codex_cli') setFresh(created.id)
        message.success('Подключение добавлено')
      } else if (editing) {
        const apiKey = values.clear_key ? '' : values.api_key?.trim() ? values.api_key.trim() : undefined
        await updateProfile(editing.id, {
          name: values.name,
          base_url: editing.kind !== 'codex_cli' ? (values.base_url ?? '') : undefined,
          model: values.model ?? '',
          ...(apiKey !== undefined ? { api_key: apiKey } : {}),
          params,
        })
        message.success('Подключение сохранено')
      }
      setEditing(null)
      await load()
      refresh()
    } catch (err) {
      message.error(errorText(err, 'Не удалось сохранить подключение'))
    } finally {
      setSaving(false)
    }
  }

  const check = async (profile: LlmProfile) => {
    setTesting(profile.id)
    try {
      setReport({ profile: profile.name, report: await testProfile(profile.id) })
      await load()
    } catch (err) {
      message.error(errorText(err, 'Проверка не выполнена'))
    } finally {
      setTesting(null)
    }
  }

  // Проверка из шага 4: результат — тут же, под кнопкой, а не в отдельном окне.
  const checkCodex = async (profile: LlmProfile) => {
    setTesting(profile.id)
    try {
      setCodexReport({ id: profile.id, report: await testProfile(profile.id) })
      await load()
    } catch (err) {
      message.error(errorText(err, 'Проверка не выполнена'))
    } finally {
      setTesting(null)
    }
  }

  const run = async (action: () => Promise<unknown>, done: string) => {
    try {
      await action()
      message.success(done)
      await load()
      refresh()
    } catch (err) {
      message.error(errorText(err, 'Не получилось'))
    }
  }

  const activate = (profile: LlmProfile) =>
    void run(async () => {
      await activateProfile(profile.id)
      if (profile.kind === 'codex_cli') setFresh(profile.id)
    }, `Действует «${profile.name}»`)

  const remove = (profile: LlmProfile) =>
    modal.confirm({
      title: `Удалить подключение «${profile.name}»?`,
      content: profile.is_active
        ? 'Это действующее подключение: новые проверки не будут анализироваться, пока не выбрано другое.'
        : undefined,
      okText: 'Удалить',
      okButtonProps: { danger: true },
      cancelText: 'Отмена',
      onOk: () => run(() => deleteProfile(profile.id), 'Подключение удалено'),
    })

  const keyText = (profile: LlmProfile) =>
    profile.kind === 'codex_cli' ? '—' : profile.has_key ? `…${profile.key_hint || '****'}` : <Tag>без ключа</Tag>
  const priceText = (profile: LlmProfile) =>
    profile.model_info?.currency
      ? `${pricePerMillion(profile.model_info.price_input, profile.model_info.currency)} / ${pricePerMillion(
          profile.model_info.price_output,
          profile.model_info.currency,
        )}`
      : '—'

  // На телефоне модель, ключ и цена — под названием, кнопки — столбиком справа.
  const columns: ColumnsType<LlmProfile> = [
    {
      title: 'Подключение',
      dataIndex: 'name',
      render: (name: string, profile) => (
        <Flex vertical gap={2}>
          <Flex gap={6} align="center" wrap>
            <Typography.Text strong style={{ wordBreak: 'break-word' }}>
              {name}
            </Typography.Text>
            {profile.is_active && <Tag color="success">действует</Tag>}
            {profile.params.russia_only && <Tag>только РФ</Tag>}
            {profile.kind === 'anthropic' && <Tag>Anthropic</Tag>}
            {profile.params.effort && <Tag>effort {profile.params.effort}</Tag>}
          </Flex>
          <Typography.Text type="secondary" style={{ fontSize: 12, wordBreak: 'break-all' }}>
            {profile.kind === 'codex_cli' ? 'Codex CLI' : profile.base_url}
          </Typography.Text>
          {mobile && (
            <Typography.Text style={{ fontSize: 12, wordBreak: 'break-word' }}>
              Модель: {profile.model || 'по умолчанию'} · ключ {keyText(profile)}
            </Typography.Text>
          )}
          {narrow && profile.model_info?.currency && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Цена за 1 млн: {priceText(profile)}
            </Typography.Text>
          )}
        </Flex>
      ),
    },
    {
      title: 'Модель',
      dataIndex: 'model',
      responsive: ['md'],
      render: (model: string) => <span style={{ wordBreak: 'break-word' }}>{model || 'по умолчанию'}</span>,
    },
    { title: 'Ключ', key: 'key', width: 120, responsive: ['md'], render: (_: unknown, profile) => keyText(profile) },
    { title: 'Цена за 1 млн', key: 'price', width: 170, responsive: ['lg'], render: (_: unknown, profile) => priceText(profile) },
    {
      key: 'actions',
      width: mobile ? 150 : 360,
      render: (_: unknown, profile) => (
        <Flex gap={6} wrap vertical={mobile} align={mobile ? 'stretch' : 'center'}>
          <Button size="small" icon={<CheckCircleOutlined />} loading={testing === profile.id} onClick={() => void check(profile)}>
            Проверить связь
          </Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => open(profile)}>
            Изменить
          </Button>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                ...(!profile.is_active ? [{ key: 'activate', icon: <StarOutlined />, label: 'Сделать действующим' }] : []),
                { key: 'delete', icon: <DeleteOutlined />, label: 'Удалить', danger: true },
              ],
              onClick: ({ key }) => (key === 'activate' ? activate(profile) : remove(profile)),
            }}
          >
            <Button size="small" type={mobile ? 'default' : 'text'} icon={<MoreOutlined />} aria-label={`Ещё: ${profile.name}`}>
              {mobile && 'Ещё'}
            </Button>
          </Dropdown>
        </Flex>
      ),
    },
  ]

  const active = profiles?.find((profile) => profile.is_active) ?? null
  const codexProfile = profiles ? codexProfileOf(profiles) : null
  // Шаги Codex — первыми, когда анализ идёт или должен пойти через Codex. Если действует другое подключение,
  // они ниже списка: это запасной путь.
  const codexFirst = codex?.installed === true && (!active || active.kind === 'codex_cli')
  const codexBlock =
    codex === null || profiles === null ? null : codex.installed ? (
      <CodexSteps
        state={codex}
        profiles={profiles}
        busy={codexBusy}
        testing={codexProfile !== null && testing === codexProfile.id}
        report={codexProfile && codexReport?.id === codexProfile.id ? codexReport.report : null}
        fresh={codexProfile !== null && fresh === codexProfile.id}
        onLogin={() => void codexAct(codexLogin)}
        onCancel={() => void codexAct(codexCancel)}
        onLogout={() => codexAct(codexLogout)}
        onAdd={openCodex}
        onActivate={activate}
        onCheck={(profile) => void checkCodex(profile)}
      />
    ) : (
      <CodexUnavailable state={codex} />
    )

  const newButton = (
    <Button icon={<PlusOutlined />} block={mobile} onClick={() => open('new')}>
      Новое подключение
    </Button>
  )
  const isNew = editing === 'new'
  const editedProfile = editing !== null && editing !== 'new' ? editing : null
  return (
    <>
      <SectionTitle sub="Через действующее подключение договоры уходят на анализ.">Подключения ИИ</SectionTitle>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}
      {profiles !== null && !active && (
        <Alert
          type="warning"
          showIcon
          title="Нет действующего подключения — договоры читаются, но не анализируются"
          description={`${
            codex?.installed ? 'Настройте Codex по шагам ниже или добавьте подключение по ключу API.' : 'Добавьте подключение кнопкой «Новое подключение».'
          } Проверки, загруженные до этого, менеджер запустит сам — кнопкой «Запустить анализ».`}
          style={{ marginBottom: 16 }}
        />
      )}
      <Flex vertical gap={16}>
        {codexFirst && codexBlock}
        <Card
          title="Все подключения"
          // На телефоне кнопка рядом с заголовком его обрезает: там она под списком.
          extra={!mobile && newButton}
          styles={{ body: { padding: 0 } }}
        >
          <Table<LlmProfile>
            rowKey="id"
            columns={columns}
            dataSource={profiles ?? []}
            loading={profiles === null && !error}
            pagination={false}
            locale={{ emptyText: 'Подключений пока нет' }}
          />
          {mobile && <div style={{ padding: 16 }}>{newButton}</div>}
        </Card>
        {!codexFirst && codexBlock}
      </Flex>

      <Modal
        title={report ? `Проверка связи: ${report.profile}` : ''}
        open={report !== null}
        onCancel={() => setReport(null)}
        footer={null}
        destroyOnHidden
      >
        {report && <CheckReport report={report.report} />}
      </Modal>

      <Modal
        title={isNew ? 'Новое подключение' : `Подключение «${editedProfile?.name ?? ''}»`}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => form.submit()}
        okText="Сохранить"
        cancelText="Отмена"
        confirmLoading={saving}
        width={760}
        destroyOnHidden
      >
        <Form<ProfileForm> form={form} layout="vertical" requiredMark={false} onFinish={submit}>
          {isNew && (
            <Row gutter={12}>
              <Col xs={24} sm={12}>
                <Form.Item label="Готовый адрес" name="preset">
                  <Select
                    allowClear
                    placeholder="RouterAI, OpenRouter, OpenAI, Anthropic, Ollama"
                    options={presets.map((preset) => ({ value: preset.id, label: preset.title }))}
                    onChange={(value?: string) => value && applyPreset(value)}
                  />
                </Form.Item>
              </Col>
              <Col xs={24} sm={12}>
                <Form.Item label="Тип" name="kind">
                  <Select
                    options={(Object.keys(KIND_LABELS) as LlmKind[]).map((value) => ({ value, label: KIND_LABELS[value] }))}
                    onChange={changeKind}
                  />
                </Form.Item>
              </Col>
            </Row>
          )}
          {!isNew && (
            // Тип у сохранённого подключения не меняется, но форме он нужен, чтобы показать свои поля.
            <Form.Item name="kind" hidden>
              <Input />
            </Form.Item>
          )}
          <Form.Item label="Название" name="name" rules={[{ required: true, message: 'Введите название' }]}>
            <Input maxLength={100} />
          </Form.Item>
          {kind !== 'codex_cli' && (
            <>
              <Form.Item
                label="Адрес API"
                name="base_url"
                rules={kind === 'openai_compat' ? [{ required: true, message: 'Укажите адрес API' }] : []}
                extra={
                  kind === 'anthropic'
                    ? 'Пусто — API Anthropic. Anthropic не обслуживает Россию: нужен сервер в стране, где он работает.'
                    : undefined
                }
              >
                <Input placeholder={kind === 'anthropic' ? ANTHROPIC_URL : 'https://routerai.ru/api/v1'} />
              </Form.Item>
              <Form.Item
                label="Ключ API"
                name="api_key"
                extra={
                  editedProfile?.has_key
                    ? `Сохранён ключ …${editedProfile.key_hint || '****'}. Оставьте поле пустым, чтобы не менять.`
                    : kind === 'anthropic'
                      ? 'Ключ из консоли Anthropic. Хранится зашифрованным и обратно не показывается.'
                      : 'Ключ хранится зашифрованным и обратно не показывается. Для Ollama не нужен.'
                }
              >
                <Input.Password autoComplete="new-password" />
              </Form.Item>
              {editedProfile?.has_key && (
                <Form.Item name="clear_key" valuePropName="checked">
                  <Checkbox>Удалить сохранённый ключ</Checkbox>
                </Form.Item>
              )}
            </>
          )}
          <Form.Item
            label="Модель"
            extra={
              kind === 'codex_cli'
                ? 'Пусто — модель по умолчанию в Codex.'
                : modelsTotal > models.length
                  ? `Показаны ${models.length} из ${modelsTotal}: начните вводить название.`
                  : kind === 'anthropic'
                    ? 'Модели, доступные ключу. Цен в API Anthropic нет: стоимость анализа не считается.'
                    : 'Цены — за 1 млн токенов (вход / выход). «схема» — модель поддерживает строгую схему ответа.'
            }
          >
            <Flex gap={8} vertical={mobile}>
              <Form.Item name="model" noStyle rules={kind !== 'codex_cli' ? [{ required: true, message: 'Укажите модель' }] : []}>
                <AutoComplete
                  style={{ flex: 1 }}
                  placeholder={kind === 'anthropic' ? 'claude-opus-5-5' : 'vendor/model'}
                  options={models.map((info) => ({
                    value: info.id,
                    label: (
                      <Flex justify="space-between" gap={8} wrap>
                        <span>{info.id}</span>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {pricePerMillion(info.price_input, info.currency)} / {pricePerMillion(info.price_output, info.currency)} · {supportLabel(info)}
                        </Typography.Text>
                      </Flex>
                    ),
                  }))}
                  filterOption={(input, option) => String(option?.value ?? '').toLowerCase().includes(input.toLowerCase())}
                />
              </Form.Item>
              {kind !== 'codex_cli' && (
                <Button icon={<ApiOutlined />} loading={loadingModels} onClick={() => void loadModels()}>
                  Список моделей
                </Button>
              )}
            </Flex>
          </Form.Item>
          <Row gutter={12}>
            <Col xs={24} sm={8}>
              <Form.Item label="Лимит времени, с" name="timeout_seconds">
                <InputNumber min={10} max={3600} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              <Form.Item label="Повторы при сбоях" name="max_retries">
                <InputNumber min={0} max={5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              {kind === 'anthropic' ? (
                <Form.Item label="Глубина рассуждений" name="effort" extra="Пусто — по умолчанию модели">
                  <Select allowClear options={EFFORT_OPTIONS} placeholder="по умолчанию" />
                </Form.Item>
              ) : (
                <Form.Item label="Температура" name="temperature" extra="Пусто — по умолчанию модели">
                  <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
                </Form.Item>
              )}
            </Col>
          </Row>
          {kind !== 'codex_cli' && (
            <>
              <Row gutter={12}>
                <Col xs={24} sm={12}>
                  <Form.Item label="Формат ответа" name="response_format">
                    <Select options={kind === 'anthropic' ? ANTHROPIC_FORMAT_OPTIONS : FORMAT_OPTIONS} />
                  </Form.Item>
                </Col>
                <Col xs={24} sm={12}>
                  <Form.Item
                    label="Максимум токенов ответа"
                    name="max_output_tokens"
                    extra={kind === 'anthropic' ? 'Пусто — 64 000 или предел модели, если он меньше' : 'Пусто — без ограничения'}
                  >
                    <InputNumber min={256} max={200000} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              {kind === 'openai_compat' && isRouterAi(baseUrl) && (
                <Form.Item
                  label="Только серверы в РФ"
                  name="russia_only"
                  valuePropName="checked"
                  extra="Запросы идут лишь провайдерам RouterAI с серверами в России."
                >
                  <Switch />
                </Form.Item>
              )}
              <Form.Item label="Дополнительные поля запроса (JSON)" name="extra_body">
                <Input.TextArea
                  rows={3}
                  placeholder={kind === 'anthropic' ? '{"inference_geo": "us"}' : '{"reasoning": {"effort": "high"}}'}
                  style={{ fontFamily: 'monospace' }}
                />
              </Form.Item>
            </>
          )}
          {kind === 'codex_cli' && (
            <Form.Item label="Настройки Codex (--config), по одной на строке" name="codex_config">
              <Input.TextArea rows={3} placeholder='model_reasoning_effort="high"' style={{ fontFamily: 'monospace' }} />
            </Form.Item>
          )}
          {isNew && (
            <Form.Item name="activate" valuePropName="checked">
              <Checkbox>Сделать действующим</Checkbox>
            </Form.Item>
          )}
        </Form>
      </Modal>
    </>
  )
}
