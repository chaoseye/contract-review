import { DeleteOutlined, FilePdfOutlined, FileWordOutlined, PlusOutlined, SaveOutlined, UndoOutlined } from '@ant-design/icons'
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Flex,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Switch,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { useEffect, useState } from 'react'

import {
  downloadTemplatePreview,
  getTemplate,
  previewTemplate,
  type ProtocolTemplate,
  saveTemplate,
  type TemplateOut,
  type TemplatePreview,
} from '../../api/admin.ts'
import { PageSkeleton } from '../../components/PageSkeleton.tsx'
import { useIsMobile } from '../../components/useIsMobile.ts'
import { errorText } from './format.ts'
import { SectionTitle } from './SectionTitle.tsx'

const PREVIEW_DELAY_MS = 400

function Placeholders({ fields }: { fields: Record<string, string> }) {
  return (
    <Flex gap={4} wrap style={{ marginTop: 4 }}>
      {Object.entries(fields).map(([name, hint]) => (
        <Tooltip key={name} title={hint}>
          <Tag style={{ cursor: 'help' }}>{`{${name}}`}</Tag>
        </Tooltip>
      ))}
    </Flex>
  )
}

/** Шаблон протокола: заголовок, вступление, заключительные положения, колонки, бланк; предпросмотр. */
export function TemplatePage() {
  const { message } = App.useApp()
  const mobile = useIsMobile()
  const [data, setData] = useState<TemplateOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<TemplatePreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [downloading, setDownloading] = useState<'docx' | 'pdf' | null>(null)
  const [signature, setSignature] = useState(true)
  const [stamp, setStamp] = useState(true)
  const [form] = Form.useForm<ProtocolTemplate>()
  const values = Form.useWatch([], form) as ProtocolTemplate | undefined

  useEffect(() => {
    getTemplate()
      .then(setData)
      .catch((err: unknown) => setError(errorText(err, 'Не удалось загрузить шаблон')))
  }, [])

  // Предпросмотр текста пересчитывается после паузы в наборе.
  useEffect(() => {
    if (!values || !data) return
    const timer = setTimeout(() => {
      previewTemplate(values)
        .then((result) => {
          setPreview(result)
          setPreviewError(null)
        })
        .catch((err: unknown) => setPreviewError(errorText(err, 'Предпросмотр не собран')))
    }, PREVIEW_DELAY_MS)
    return () => clearTimeout(timer)
  }, [values, data])

  const save = async (template: ProtocolTemplate) => {
    setSaving(true)
    try {
      const saved = await saveTemplate(template)
      setData((current) => (current ? { ...current, template: saved } : current))
      form.setFieldsValue(saved)
      message.success('Шаблон сохранён: он действует для новых протоколов')
    } catch (err) {
      message.error(errorText(err, 'Не удалось сохранить шаблон'))
    } finally {
      setSaving(false)
    }
  }

  const download = async (format: 'docx' | 'pdf') => {
    setDownloading(format)
    try {
      await downloadTemplatePreview(form.getFieldsValue(true), format, signature, stamp)
    } catch (err) {
      message.error(errorText(err, 'Образец не собран'))
    } finally {
      setDownloading(null)
    }
  }

  if (!data) {
    return error ? <Alert type="error" showIcon title={error} /> : <PageSkeleton />
  }
  return (
    <>
      <SectionTitle sub="Тексты собираются с подстановками в фигурных скобках — наведите на подстановку, чтобы увидеть, что в неё попадёт. Шаблон действует для протоколов, собранных после сохранения; правки, которые менеджер уже сохранил в проверке, не меняются.">
        Шаблон протокола
      </SectionTitle>
      <Row gutter={16}>
        <Col xs={24} xl={13}>
          <Form<ProtocolTemplate> form={form} layout="vertical" initialValues={data.template} onFinish={save}>
            <Card style={{ marginBottom: 16 }}>
              <Form.Item label="Заголовок" name="title" rules={[{ required: true, message: 'Заголовок не может быть пустым' }]}>
                <Input maxLength={200} />
              </Form.Item>
              <Form.Item
                label="Вступление"
                name="preamble"
                rules={[{ required: true, message: 'Вступление не может быть пустым' }]}
                extra={<Placeholders fields={data.fields.preamble} />}
              >
                <Input.TextArea autoSize={{ minRows: 3, maxRows: 10 }} maxLength={3000} />
              </Form.Item>
              <Form.Item label="Заголовок заключительных положений" name="closing_title">
                <Input maxLength={200} />
              </Form.Item>
              <Typography.Text style={{ display: 'block', marginBottom: 8 }}>Заключительные положения</Typography.Text>
              <Form.List name="closing_items">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map((field, index) => (
                      <Flex key={field.key} gap={8} align="start" style={{ marginBottom: 8 }}>
                        <Typography.Text type="secondary" style={{ paddingTop: 5, width: 20 }}>
                          {index + 1}.
                        </Typography.Text>
                        <Form.Item name={field.name} noStyle>
                          <Input.TextArea autoSize={{ minRows: 1, maxRows: 6 }} maxLength={2000} />
                        </Form.Item>
                        <Button icon={<DeleteOutlined />} onClick={() => remove(field.name)} aria-label="Удалить пункт" />
                      </Flex>
                    ))}
                    {fields.length < 10 && (
                      <Button icon={<PlusOutlined />} onClick={() => add('')} style={{ marginBottom: 4 }}>
                        Добавить пункт
                      </Button>
                    )}
                    <Placeholders fields={data.fields.closing} />
                  </>
                )}
              </Form.List>
              <Form.Item
                label="Пункт о приложениях"
                name="appendices_item"
                style={{ marginTop: 16 }}
                rules={[{ required: true, message: 'Пункт не может быть пустым' }]}
                extra={
                  <>
                    Добавляется последним, если менеджер указал приложения. <Placeholders fields={data.fields.appendices} />
                  </>
                }
              >
                <Input maxLength={300} />
              </Form.Item>
            </Card>
            <Card title="Оформление по умолчанию" style={{ marginBottom: 16 }}>
              <Row gutter={16}>
                <Col xs={24} md={12}>
                  <Form.Item
                    label="Колонка «Согласованная редакция»"
                    name="include_agreed_column"
                    valuePropName="checked"
                    extra="Менеджер может изменить в своём протоколе"
                  >
                    <Switch />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item label="Шрифт таблицы, pt" name="table_font_size" extra="От 8 до 12, обычно 10">
                    <InputNumber min={8} max={12} step={0.5} />
                  </Form.Item>
                </Col>
                <Col span={24}>
                  <Form.Item
                    label="Фирменный бланк"
                    name="letterhead"
                    valuePropName="checked"
                    extra="Шапка из раздела «Организация» на первой странице и контакты внизу."
                  >
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>
            </Card>
            <Flex gap={8} wrap style={{ marginBottom: 16 }}>
              <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
                Сохранить шаблон
              </Button>
              <Popconfirm
                title="Подставить тексты по умолчанию?"
                description="Шаблон изменится после сохранения."
                okText="Подставить"
                cancelText="Отмена"
                onConfirm={() => form.setFieldsValue(data.defaults)}
              >
                <Button icon={<UndoOutlined />}>Тексты по умолчанию</Button>
              </Popconfirm>
            </Flex>
          </Form>
        </Col>
        <Col xs={24} xl={11}>
          {/* На телефоне длинный заголовок обрезается: там — коротко. */}
          <Card title={mobile ? 'Предпросмотр' : 'Предпросмотр на вымышленном договоре'} style={{ position: 'sticky', top: 16 }}>
            {previewError && <Alert type="error" showIcon title={previewError} style={{ marginBottom: 12 }} />}
            {preview && (
              <div className="z-doc" style={{ fontSize: 14 }}>
                <div style={{ textAlign: 'center', fontWeight: 700 }}>{preview.title}</div>
                <div style={{ textAlign: 'center', marginBottom: 12 }}>{preview.subtitle}</div>
                <p style={{ textAlign: 'justify' }}>{preview.preamble}</p>
                <p style={{ color: 'var(--z-text-3)', textAlign: 'center' }}>[таблица разногласий]</p>
                <div style={{ textAlign: 'center', fontWeight: 700 }}>{preview.closing_title}</div>
                <ol>
                  {preview.closing_items.map((item, index) => (
                    <li key={index} style={{ textAlign: 'justify' }}>
                      {item}
                    </li>
                  ))}
                </ol>
              </div>
            )}
            <Flex gap={8} wrap align="center" style={{ marginTop: 12 }}>
              <Checkbox checked={signature} onChange={(event) => setSignature(event.target.checked)}>
                подпись
              </Checkbox>
              <Checkbox checked={stamp} onChange={(event) => setStamp(event.target.checked)}>
                печать
              </Checkbox>
              <Button icon={<FileWordOutlined />} loading={downloading === 'docx'} onClick={() => void download('docx')}>
                Образец Word
              </Button>
              <Button icon={<FilePdfOutlined />} loading={downloading === 'pdf'} onClick={() => void download('pdf')}>
                Образец PDF
              </Button>
            </Flex>
            <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
              В образце — данные, подпись и печать из раздела «Организация» и две вымышленные строки таблицы.
            </Typography.Paragraph>
          </Card>
        </Col>
      </Row>
    </>
  )
}
