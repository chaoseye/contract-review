import { DownloadOutlined, SaveOutlined, UploadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Descriptions, Flex, Form, InputNumber, Modal, Row, Typography, Upload } from 'antd'
import { useEffect, useState } from 'react'

import {
  exportSettings,
  getLimitsSettings,
  type ImportReport,
  importSettings,
  type Limits,
  type LimitsOut,
  saveLimits,
} from '../../api/admin.ts'
import { PageSkeleton } from '../../components/PageSkeleton.tsx'
import { errorText } from './format.ts'
import { useAdminContext } from './readiness.ts'
import { SectionTitle } from './SectionTitle.tsx'

function ImportResult({ report }: { report: ImportReport }) {
  const list = (items: string[]) => (items.length > 0 ? items.join(', ') : '—')
  return (
    <Flex vertical gap={12}>
      <Descriptions
        size="small"
        column={1}
        bordered
        items={[
          { key: 'prompts', label: 'Версий промпта добавлено', children: report.prompt_versions_added },
          { key: 'added', label: 'Подключения добавлены', children: list(report.profiles_added) },
          { key: 'updated', label: 'Подключения обновлены', children: list(report.profiles_updated) },
          { key: 'active', label: 'Действующее подключение', children: report.profile_active ?? '—' },
          {
            key: 'other',
            label: 'Заменены',
            children:
              [report.organization && 'данные организации', report.protocol_template && 'шаблон протокола', report.limits && 'лимиты']
                .filter(Boolean)
                .join(', ') || '—',
          },
        ]}
      />
      {report.profiles_need_key.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="Введите ключи API"
          description={`Ключи в файл настроек не входят. Откройте «Подключения ИИ» и введите ключ: ${report.profiles_need_key.join(', ')}.`}
        />
      )}
      {report.notes.map((note) => (
        <Alert key={note} type="info" showIcon title={note} />
      ))}
    </Flex>
  )
}

/** Срок жизни проверки, лимит файла, перенос настроек одним файлом. */
export function SettingsPage() {
  const { message, modal } = App.useApp()
  const { refresh } = useAdminContext()
  const [limits, setLimits] = useState<LimitsOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [report, setReport] = useState<ImportReport | null>(null)

  useEffect(() => {
    getLimitsSettings()
      .then(setLimits)
      .catch((err: unknown) => setError(errorText(err, 'Не удалось загрузить настройки')))
  }, [])

  const save = async (values: Limits) => {
    setSaving(true)
    try {
      setLimits(await saveLimits(values))
      message.success('Настройки сохранены')
    } catch (err) {
      message.error(errorText(err, 'Не удалось сохранить'))
    } finally {
      setSaving(false)
    }
  }

  const download = async () => {
    setExporting(true)
    try {
      await exportSettings()
    } catch (err) {
      message.error(errorText(err, 'Не удалось выгрузить настройки'))
    } finally {
      setExporting(false)
    }
  }

  const upload = (file: File) => {
    modal.confirm({
      title: `Загрузить настройки из «${file.name}»?`,
      content:
        'Версии промпта и подключения из файла добавятся. Данные организации с подписью и печатью, шаблон протокола и лимиты заменятся значениями из файла. Пользователи и ключи API не меняются.',
      okText: 'Загрузить',
      cancelText: 'Отмена',
      onOk: async () => {
        setImporting(true)
        try {
          setReport(await importSettings(file))
          setLimits(await getLimitsSettings())
          // Из файла пришли подключения и данные организации — «Обзор» меняется.
          refresh()
        } catch (err) {
          message.error(errorText(err, 'Не удалось загрузить настройки'))
        } finally {
          setImporting(false)
        }
      },
    })
  }

  if (!limits) {
    return error ? <Alert type="error" showIcon title={error} /> : <PageSkeleton />
  }
  return (
    <>
      <SectionTitle sub="Сколько хранится проверка, какой файл можно загрузить, перенос настроек на другой сервер.">
        Настройки и перенос
      </SectionTitle>
      <Row gutter={16}>
        <Col xs={24} lg={12}>
          <Card title="Проверки" style={{ marginBottom: 16 }}>
            <Form<Limits> layout="vertical" initialValues={limits} onFinish={save} key={`${limits.check_ttl_hours}-${limits.max_upload_mb}`}>
              <Form.Item
                label="Срок жизни проверки, часов"
                name="check_ttl_hours"
                rules={[{ required: true, message: 'Укажите срок' }]}
                extra={`После последнего действия менеджера проверка удаляется вместе с файлом и анализом. Значение из .env: ${limits.defaults.check_ttl_hours}. Новый срок действует для новых проверок и после следующего действия в старых.`}
              >
                <InputNumber min={1} max={720} />
              </Form.Item>
              <Form.Item
                label="Максимальный размер файла, МБ"
                name="max_upload_mb"
                rules={[{ required: true, message: 'Укажите размер' }]}
                extra={`Значение из .env: ${limits.defaults.max_upload_mb}.`}
              >
                <InputNumber min={1} max={200} />
              </Form.Item>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
                Сохранить
              </Button>
            </Form>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Перенос настроек" style={{ marginBottom: 16 }}>
            <Typography.Paragraph>
              Один файл: версии промпта, подключения ИИ без ключей, данные организации с подписью и печатью, шаблон
              протокола и лимиты. Пользователей, ключей API, проверок и журнала в нём нет.
            </Typography.Paragraph>
            <Typography.Paragraph type="warning">
              В файле подпись и печать организации — храните его так же бережно, как сами картинки.
            </Typography.Paragraph>
            <Flex gap={8} wrap>
              <Button icon={<DownloadOutlined />} loading={exporting} onClick={() => void download()}>
                Выгрузить настройки
              </Button>
              <Upload
                accept=".json,application/json"
                showUploadList={false}
                beforeUpload={(file) => {
                  upload(file)
                  return false
                }}
              >
                <Button icon={<UploadOutlined />} loading={importing}>
                  Загрузить настройки
                </Button>
              </Upload>
            </Flex>
          </Card>
        </Col>
      </Row>
      <Modal title="Настройки загружены" open={report !== null} onCancel={() => setReport(null)} footer={null} width={640} destroyOnHidden>
        {report && <ImportResult report={report} />}
      </Modal>
    </>
  )
}
