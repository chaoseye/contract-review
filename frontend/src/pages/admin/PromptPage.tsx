import { CaretRightOutlined, DiffOutlined, RollbackOutlined, SaveOutlined } from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Input,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'

import {
  activatePromptVersion,
  createPromptVersion,
  getPromptRun,
  getPromptVersion,
  type LlmProfile,
  listProfiles,
  listPromptVersions,
  type PromptRun,
  type PromptVersion,
  type PromptVersionFull,
  startPromptRun,
} from '../../api/admin.ts'
import { type CheckSummary, listChecks } from '../../api/checks.ts'
import { formatDateTime } from '../../components/check/labels.ts'
import { useIsMobile, useIsNarrow } from '../../components/useIsMobile.ts'
import { DiffView } from './DiffView.tsx'
import { errorText } from './format.ts'
import { RunResult } from './RunResult.tsx'
import { SectionTitle } from './SectionTitle.tsx'

const RUN_POLL_MS = 2000
// Прогон черновика возможен на проверке, где документ уже прочитан.
const RUNNABLE = new Set(['extracted', 'analyzing', 'ready', 'failed'])

interface Comparison {
  title: string
  before: string
  after: string
}

/** Промпт администратора: редактор, версии, сравнение, откат, прогон черновика на договоре. */
export function PromptPage() {
  const { message } = App.useApp()
  const mobile = useIsMobile()
  const narrow = useIsNarrow()
  const [versions, setVersions] = useState<PromptVersion[] | null>(null)
  const [active, setActive] = useState<PromptVersionFull | null>(null)
  const [text, setText] = useState('')
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [checks, setChecks] = useState<CheckSummary[]>([])
  const [profiles, setProfiles] = useState<LlmProfile[]>([])
  const [checkId, setCheckId] = useState<string | null>(null)
  const [profileId, setProfileId] = useState<number | null>(null)
  const [run, setRun] = useState<PromptRun | null>(null)
  const [runError, setRunError] = useState<string | null>(null)

  const load = useCallback(async (resetEditor: boolean) => {
    try {
      const list = await listPromptVersions()
      const current = list.find((version) => version.is_active)
      const full = current ? await getPromptVersion(current.id) : null
      setVersions(list)
      setActive(full)
      if (resetEditor && full) setText(full.text)
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Не удалось загрузить версии промпта'))
    }
  }, [])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(true)
    listChecks()
      .then((items) => setChecks(items.filter((check) => RUNNABLE.has(check.status))))
      .catch(() => setChecks([]))
    listProfiles()
      .then(setProfiles)
      .catch(() => setProfiles([]))
  }, [load])

  const running = run?.status === 'running'
  useEffect(() => {
    if (!run || run.status !== 'running') return
    const timer = setInterval(() => {
      getPromptRun(run.id)
        .then(setRun)
        .catch((err: unknown) => {
          setRun(null)
          setRunError(errorText(err, 'Прогон потерян'))
        })
    }, RUN_POLL_MS)
    return () => clearInterval(timer)
  }, [run])

  const changed = active !== null && text.trim() !== active.text.trim()

  const save = async (activate: boolean) => {
    setSaving(true)
    try {
      const version = await createPromptVersion(text, comment, activate)
      message.success(activate ? `Версия ${version.id} сохранена и действует для новых анализов` : `Черновик сохранён как версия ${version.id}`)
      setComment('')
      await load(activate)
    } catch (err) {
      message.error(errorText(err, 'Не удалось сохранить версию'))
    } finally {
      setSaving(false)
    }
  }

  const activate = async (version: PromptVersion) => {
    try {
      await activatePromptVersion(version.id)
      message.success(`Действует версия ${version.id}`)
      await load(!changed)
    } catch (err) {
      message.error(errorText(err, 'Не удалось сменить версию'))
    }
  }

  const compareWithActive = async (version: PromptVersion) => {
    if (!active) return
    try {
      const full = await getPromptVersion(version.id)
      setComparison({ title: `Версия ${version.id} → активная версия ${active.id}`, before: full.text, after: active.text })
    } catch (err) {
      message.error(errorText(err, 'Не удалось загрузить версию'))
    }
  }

  const toEditor = async (version: PromptVersion) => {
    try {
      setText((await getPromptVersion(version.id)).text)
      message.info(`Текст версии ${version.id} в редакторе. Сохраните его как новую версию или сделайте версию активной.`)
    } catch (err) {
      message.error(errorText(err, 'Не удалось загрузить версию'))
    }
  }

  const startRun = async () => {
    if (!checkId) return
    setRunError(null)
    try {
      setRun(await startPromptRun(text, checkId, profileId))
    } catch (err) {
      setRunError(errorText(err, 'Не удалось запустить прогон'))
    }
  }

  // На телефоне дата, автор и комментарий — под номером версии, кнопки — столбиком справа.
  const columns: ColumnsType<PromptVersion> = [
    {
      title: 'Версия',
      dataIndex: 'id',
      width: mobile ? undefined : 120,
      render: (id: number, version) => (
        <Flex vertical gap={2}>
          <Flex gap={6} align="center">
            <Typography.Text strong>{id}</Typography.Text>
            {version.is_active && <Tag color="success">действует</Tag>}
          </Flex>
          {mobile && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {formatDateTime(version.created_at)} · {version.author ?? 'система'}
            </Typography.Text>
          )}
          {mobile && version.comment && <Typography.Text style={{ wordBreak: 'break-word' }}>{version.comment}</Typography.Text>}
          {narrow && !mobile && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {version.author ?? 'система'} · {version.length} симв.
            </Typography.Text>
          )}
        </Flex>
      ),
    },
    { title: 'Дата', dataIndex: 'created_at', width: 140, responsive: ['md'], render: (value: string) => formatDateTime(value) },
    { title: 'Автор', dataIndex: 'author', width: 110, responsive: ['lg'], render: (value: string | null) => value ?? 'система' },
    { title: 'Комментарий', dataIndex: 'comment', responsive: ['md'], render: (value: string) => value || '—' },
    { title: 'Символов', dataIndex: 'length', width: 100, responsive: ['lg'] },
    {
      key: 'actions',
      width: mobile ? 150 : 300,
      render: (_: unknown, version) => (
        <Flex gap={6} wrap vertical={mobile} align={mobile ? 'stretch' : undefined}>
          <Button size="small" onClick={() => void toEditor(version)}>
            В редактор
          </Button>
          {!version.is_active && (
            <>
              <Button size="small" icon={<DiffOutlined />} onClick={() => void compareWithActive(version)}>
                Сравнить
              </Button>
              <Popconfirm
                title={`Сделать версию ${version.id} действующей?`}
                description="Новые анализы пойдут с ней. Уже сделанные анализы не меняются."
                okText="Сделать"
                cancelText="Отмена"
                onConfirm={() => activate(version)}
              >
                <Button size="small" icon={<RollbackOutlined />}>
                  Сделать действующей
                </Button>
              </Popconfirm>
            </>
          )}
        </Flex>
      ),
    },
  ]

  const activeProfile = profiles.find((profile) => profile.is_active)
  return (
    <>
      <SectionTitle sub="Промпт задаёт, что и как ищет ИИ. После него система добавляет свою часть с форматом ответа — её правка промпта не меняет. Новая версия действует для анализов, начатых после сохранения.">
        Промпт
      </SectionTitle>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 16 }} />}

      <Card
        title={active ? `Редактор (действует версия ${active.id})` : 'Редактор'}
        extra={!mobile && changed && <Tag color="warning">есть несохранённые изменения</Tag>}
        style={{ marginBottom: 16 }}
      >
        {active === null && !error ? (
          <Spin />
        ) : (
          <Flex vertical gap={12}>
            {mobile && changed && (
              <span>
                <Tag color="warning">есть несохранённые изменения</Tag>
              </span>
            )}
            <Input.TextArea
              value={text}
              onChange={(event) => setText(event.target.value)}
              // На телефоне редактор ниже, чтобы страница прокручивалась мимо него, и шрифт 16 px —
              // иначе Safari на iPhone увеличивает страницу при вводе.
              autoSize={mobile ? { minRows: 8, maxRows: 16 } : { minRows: 16, maxRows: 40 }}
              style={{ fontFamily: 'Consolas, "Courier New", monospace', fontSize: mobile ? 16 : 13 }}
              spellCheck={false}
            />
            <Input
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              maxLength={500}
              placeholder="Что изменилось в этой версии"
            />
            <Flex gap={8} wrap>
              <Button type="primary" icon={<SaveOutlined />} disabled={!changed} loading={saving} onClick={() => void save(true)}>
                Сохранить и применить
              </Button>
              <Button disabled={!changed} loading={saving} onClick={() => void save(false)}>
                Сохранить черновиком
              </Button>
              <Button
                icon={<DiffOutlined />}
                disabled={!changed}
                onClick={() => active && setComparison({ title: 'Действующая версия → редактор', before: active.text, after: text })}
              >
                Сравнить с действующей
              </Button>
              <Popconfirm
                title="Вернуть текст действующей версии?"
                description="Несохранённые правки пропадут."
                okText="Вернуть"
                cancelText="Отмена"
                disabled={!changed}
                onConfirm={() => active && setText(active.text)}
              >
                <Button disabled={!changed}>Отменить правки</Button>
              </Popconfirm>
            </Flex>
          </Flex>
        )}
      </Card>

      <Card title="Прогон черновика на договоре" style={{ marginBottom: 16 }}>
        <Typography.Paragraph type="secondary">
          Текст из редактора отправляется в модель вместе с договором выбранной проверки. Результат показывается
          здесь и никуда не сохраняется: проверка и её анализ не меняются. Запрос стоит столько же, сколько обычный
          анализ.
        </Typography.Paragraph>
        <Flex gap={8} wrap style={{ marginBottom: 12 }}>
          <Select
            style={mobile ? { width: '100%' } : { minWidth: 320 }}
            placeholder="Проверка с прочитанным договором"
            value={checkId}
            onChange={setCheckId}
            showSearch={{ optionFilterProp: 'label' }}
            options={checks.map((check) => ({
              value: check.id,
              label: `${check.file_name} · ${check.owner_login ?? ''} · ${formatDateTime(check.created_at)}`,
            }))}
            notFoundContent="Нет проверок с прочитанным договором"
          />
          <Select<number | null>
            style={mobile ? { width: '100%' } : { minWidth: 240 }}
            value={profileId}
            onChange={setProfileId}
            options={[
              { value: null, label: activeProfile ? `Действующее: ${activeProfile.name}` : 'Действующее подключение' },
              ...profiles
                .filter((profile) => !profile.is_active)
                .map((profile) => ({ value: profile.id, label: profile.name })),
            ]}
          />
          <Button
            type="primary"
            icon={<CaretRightOutlined />}
            disabled={!checkId || !text.trim()}
            loading={running}
            onClick={() => void startRun()}
          >
            Прогнать
          </Button>
        </Flex>
        {runError && <Alert type="error" showIcon title={runError} style={{ marginBottom: 12 }} />}
        {running && (
          <Flex align="center" gap={12}>
            <Spin />
            <Typography.Text>Модель анализирует договор — обычно это несколько минут.</Typography.Text>
          </Flex>
        )}
        {run && run.status !== 'running' && (
          <Flex vertical gap={8}>
            <Typography.Text type="secondary">
              Сохранённый анализ этой проверки — на её странице: <Link to={`/checks/${run.check_id}`}>открыть проверку</Link>
            </Typography.Text>
            <RunResult run={run} />
          </Flex>
        )}
      </Card>

      <Card title="Версии" styles={{ body: { padding: 0 } }}>
        <Table<PromptVersion> rowKey="id" columns={columns} dataSource={versions ?? []} loading={versions === null && !error} pagination={false} />
      </Card>

      <Modal
        title={comparison?.title}
        open={comparison !== null}
        onCancel={() => setComparison(null)}
        footer={null}
        width={1000}
        destroyOnHidden
      >
        {comparison && <DiffView before={comparison.before} after={comparison.after} />}
      </Modal>
    </>
  )
}
