import { DeleteOutlined, SaveOutlined, UploadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Empty, Flex, Form, Input, Popconfirm, Row, Segmented, Upload, theme } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import {
  type AssetKind,
  assetUrl,
  deleteAsset,
  getOrganization,
  type OrganizationData,
  type OrganizationOut,
  REQUISITE_FIELDS,
  saveOrganization,
  uploadAsset,
} from '../../api/admin.ts'
import { PageSkeleton } from '../../components/PageSkeleton.tsx'
import { useIsMobile } from '../../components/useIsMobile.ts'
import { errorText } from './format.ts'
import { useAdminContext } from './readiness.ts'
import { SectionTitle } from './SectionTitle.tsx'

// Белый фон скана система делает прозрачным сама.
const ASSETS: { kind: AssetKind; title: string; hint: string }[] = [
  {
    kind: 'signature',
    title: 'Подпись',
    hint: 'PNG с прозрачным фоном или скан на белом фоне. Ставится над линией подписи, шириной 34 мм.',
  },
  {
    kind: 'stamp',
    title: 'Печать',
    hint: 'PNG с прозрачным фоном или скан на белом фоне. Ставится на «М.П.», диаметром 40 мм, с небольшим поворотом.',
  },
  { kind: 'header', title: 'Шапка бланка', hint: 'Нужна, только если в шаблоне протокола включён фирменный бланк.' },
]
const REQUISITES_HINT = 'Сравниваются с реквизитами поставщика в договоре; в модель не отправляются'

function AssetCard({
  kind,
  title,
  hint,
  present,
  opaque,
  version,
  onChanged,
}: {
  kind: AssetKind
  title: string
  hint: string
  present: boolean
  /** Фон картинки не прозрачный и после подготовки: в протоколе он закроет текст. */
  opaque: boolean
  version: number
  onChanged: (organization: OrganizationOut) => void
}) {
  const { message } = App.useApp()
  const { token } = theme.useToken()
  const [view, setView] = useState<'prepared' | 'raw'>('prepared')
  const [busy, setBusy] = useState(false)
  const prepared = kind !== 'header' && view === 'prepared'

  const upload = async (file: File) => {
    setBusy(true)
    try {
      onChanged(await uploadAsset(kind, file))
      message.success(`${title}: загружено`)
    } catch (err) {
      message.error(errorText(err, 'Не удалось загрузить картинку'))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      onChanged(await deleteAsset(kind))
      message.success(`${title}: удалено`)
    } catch (err) {
      message.error(errorText(err, 'Не удалось удалить картинку'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      size="small"
      title={title}
      extra={
        present &&
        kind !== 'header' && (
          <Segmented
            size="small"
            value={view}
            onChange={setView}
            options={[
              { value: 'prepared', label: 'Как в протоколе' },
              { value: 'raw', label: 'Как загружена' },
            ]}
          />
        )
      }
    >
      <Flex vertical gap={8}>
        <div
          style={{
            height: 160,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            // Клетка показывает прозрачные места картинки.
            background: `repeating-conic-gradient(${token.colorFillSecondary} 0% 25%, transparent 0% 50%) 50% / 16px 16px`,
            border: `1px solid ${token.colorBorderSecondary}`,
            borderRadius: token.borderRadius,
          }}
        >
          {present ? (
            <img src={assetUrl(kind, prepared, version)} alt={title} style={{ maxWidth: '100%', maxHeight: 150, objectFit: 'contain' }} />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Не загружена" />
          )}
        </div>
        {present && opaque && (
          <Alert
            type="warning"
            showIcon
            title="Фон не прозрачный"
            description="В протоколе он закроет линию подписи и «М.П.». Загрузите PNG с прозрачным фоном или скан на белом фоне."
          />
        )}
        <span style={{ color: token.colorTextSecondary, fontSize: 12 }}>{hint}</span>
        <Flex gap={8}>
          <Upload
            accept=".png,.jpg,.jpeg"
            showUploadList={false}
            beforeUpload={(file) => {
              void upload(file)
              return false
            }}
          >
            <Button icon={<UploadOutlined />} loading={busy}>
              {present ? 'Заменить' : 'Загрузить'}
            </Button>
          </Upload>
          {present && (
            <Popconfirm title={`Удалить: ${title.toLowerCase()}?`} okText="Удалить" cancelText="Отмена" okButtonProps={{ danger: true }} onConfirm={remove}>
              <Button danger icon={<DeleteOutlined />} disabled={busy} aria-label="Удалить" />
            </Popconfirm>
          )}
        </Flex>
      </Flex>
    </Card>
  )
}

/** Наша организация: данные для протокола, реквизиты для сверки, подпись, печать и шапка. */
export function OrganizationPage() {
  const { message } = App.useApp()
  const { refresh } = useAdminContext()
  const mobile = useIsMobile()
  const [organization, setOrganization] = useState<OrganizationOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [version, setVersion] = useState(() => Date.now())
  // Форма пересоздаётся с новыми значениями только при загрузке и сохранении, а не при замене картинки:
  // иначе пропали бы несохранённые правки полей.
  const [formKey, setFormKey] = useState(0)

  const apply = useCallback((data: OrganizationOut, resetForm: boolean) => {
    setOrganization(data)
    if (resetForm) setFormKey((key) => key + 1)
  }, [])

  useEffect(() => {
    getOrganization()
      .then((data) => apply(data, true))
      .catch((err: unknown) => setError(errorText(err, 'Не удалось загрузить данные организации')))
  }, [apply])

  const save = async (values: OrganizationData) => {
    setSaving(true)
    try {
      const requisites = Object.fromEntries(
        Object.entries(values.requisites ?? {}).map(([key, value]) => [key, (value ?? '').trim()]),
      )
      apply(await saveOrganization({ ...values, requisites }), true)
      message.success('Данные организации сохранены')
      // Готовность к протоколу видно в «Обзоре».
      refresh()
    } catch (err) {
      message.error(errorText(err, 'Не удалось сохранить'))
    } finally {
      setSaving(false)
    }
  }

  if (!organization) {
    return error ? <Alert type="error" showIcon title={error} /> : <PageSkeleton />
  }
  return (
    <>
      <SectionTitle sub="Наименование и подписант попадают в протокол разногласий, реквизиты сверяются с договором.">
        Организация
      </SectionTitle>
      {!organization.ready_for_protocol && (
        <Alert
          type="warning"
          showIcon
          title="Протокол не соберётся, пока не заполнено полное наименование"
          style={{ marginBottom: 16 }}
        />
      )}
      <Form<OrganizationData>
        key={formKey}
        layout="vertical"
        initialValues={{ ...organization, requisites: { ...organization.requisites } }}
        onFinish={save}
      >
        <Card title="Для протокола" style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                label="Полное наименование"
                name="full_name"
                extra="Как во вступлении протокола: «Индивидуальный предприниматель Иванов Иван Иванович»"
              >
                <Input maxLength={500} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="Краткое наименование" name="short_name" extra="В блоке подписей: «ИП Иванов Иван Иванович»">
                <Input maxLength={300} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Подписант (расшифровка подписи)" name="signer" extra="«И.И. Иванов»; пусто — в протоколе линия">
                <Input maxLength={200} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="В лице (для юрлица)" name="representative" extra="«генерального директора Иванова И.И.»; у ИП пусто">
                <Input maxLength={300} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Действует на основании" name="basis" extra="«Устава»; у ИП пусто">
                <Input maxLength={300} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item label="Город по умолчанию" name="default_city" extra="Если в договоре его нет">
                <Input maxLength={100} />
              </Form.Item>
            </Col>
            <Col xs={24} md={16}>
              <Form.Item label="Контакты для колонтитула бланка" name="footer_text" extra="Печатаются, только если в шаблоне включён бланк">
                <Input maxLength={500} />
              </Form.Item>
            </Col>
          </Row>
        </Card>
        <Card
          title="Реквизиты для сверки"
          // На телефоне пояснение — в теле карточки: рядом с заголовком оно его обрезает.
          extra={!mobile && <span style={{ fontSize: 12 }}>{REQUISITES_HINT}</span>}
          style={{ marginBottom: 16 }}
        >
          {mobile && <p style={{ fontSize: 12, marginTop: 0 }}>{REQUISITES_HINT}</p>}
          <Row gutter={16}>
            {REQUISITE_FIELDS.map((field) => (
              <Col xs={24} md={field.key.includes('address') || field.key === 'bank' ? 24 : 12} key={field.key}>
                <Form.Item label={field.label} name={['requisites', field.key]} extra={field.hint}>
                  <Input maxLength={500} />
                </Form.Item>
              </Col>
            ))}
          </Row>
        </Card>
        <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving} style={{ marginBottom: 28 }}>
          Сохранить данные и реквизиты
        </Button>
      </Form>
      <div className="z-block-head">
        <h2>Подпись, печать и шапка</h2>
        <p>Картинки сохраняются сразу при загрузке, кнопка «Сохранить» для них не нужна.</p>
      </div>
      <Row gutter={16}>
        {ASSETS.map((asset) => (
          <Col xs={24} md={8} key={asset.kind} style={{ marginBottom: 16 }}>
            <AssetCard
              {...asset}
              present={organization.assets[asset.kind]}
              opaque={organization.opaque_background[asset.kind] ?? false}
              version={version}
              onChanged={(data) => {
                apply(data, false)
                setVersion(Date.now())
                refresh()
              }}
            />
          </Col>
        ))}
      </Row>
    </>
  )
}
