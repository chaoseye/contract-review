import { FileTextOutlined, InboxOutlined } from '@ant-design/icons'
import { Alert, Button, Form, Input, Modal, Select, Upload } from 'antd'
import { useState } from 'react'

import { type CheckSummary, type Limits, type Procurement, uploadCheck } from '../api/checks.ts'
import { ApiError } from '../api/client.ts'
import { formatSize, PROCUREMENT_OPTIONS, UPLOAD_ACCEPT } from './check/labels.ts'
import { useIsMobile } from './useIsMobile.ts'

interface UploadForm {
  procurement: Procurement
  comment: string
}

interface Props {
  open: boolean
  /** Файл, выбранный на странице; в окне его можно заменить. */
  file: File | null
  limits: Limits | null
  onFile: (file: File) => void
  onClose: () => void
  onUploaded: (check: CheckSummary) => void
}

/** Новая проверка: выбранный файл, режим закупки и параметры сделки. */
export function UploadModal({ open, file, limits, onFile, onClose, onUploaded }: Props) {
  const mobile = useIsMobile()
  const [form] = Form.useForm<UploadForm>()
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const tooBig = file !== null && limits !== null && file.size > limits.max_upload_mb * 1024 * 1024

  const close = () => {
    if (sending) return
    setError(null)
    form.resetFields()
    onClose()
  }

  const choose = (chosen: File) => {
    onFile(chosen)
    setError(null)
    return false
  }

  const submit = async (values: UploadForm) => {
    if (!file) {
      setError('Выберите файл договора.')
      return
    }
    if (tooBig) return
    setError(null)
    setSending(true)
    try {
      const check = await uploadCheck(file, values.procurement, values.comment ?? '')
      form.resetFields()
      onUploaded(check)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось загрузить файл, попробуйте ещё раз.')
    } finally {
      setSending(false)
    }
  }

  return (
    <Modal
      title="Новая проверка"
      open={open}
      onCancel={close}
      onOk={() => form.submit()}
      okText="Загрузить и проверить"
      cancelText="Отмена"
      okButtonProps={{ disabled: !file || tooBig }}
      confirmLoading={sending}
      width={600}
      // На телефоне окно ближе к верху: так кнопка «Загрузить и проверить» видна без прокрутки чаще.
      style={mobile ? { top: 12 } : undefined}
      destroyOnHidden
    >
      <Form<UploadForm>
        form={form}
        layout="vertical"
        initialValues={{ procurement: 'unknown', comment: '' }}
        onFinish={submit}
        requiredMark={false}
      >
        <Form.Item label="Договор">
          {file ? (
            <div className="z-file">
              <span className="z-file-icon" aria-hidden="true">
                <FileTextOutlined />
              </span>
              <span className="z-file-name">
                <b>{file.name}</b>
                <span>{formatSize(file.size)}</span>
              </span>
              <Upload accept={UPLOAD_ACCEPT} showUploadList={false} beforeUpload={choose}>
                <Button>Заменить</Button>
              </Upload>
            </div>
          ) : (
            <Upload.Dragger accept={UPLOAD_ACCEPT} maxCount={1} showUploadList={false} beforeUpload={choose}>
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">{mobile ? 'Нажмите, чтобы выбрать файл' : 'Перетащите файл сюда или нажмите, чтобы выбрать'}</p>
              <p className="ant-upload-hint">
                Word (docx, doc), RTF или PDF, в том числе скан{limits ? `, до ${limits.max_upload_mb} МБ` : ''}
              </p>
            </Upload.Dragger>
          )}
          {tooBig && (
            <Alert
              type="error"
              showIcon
              title={`Файл больше ${limits?.max_upload_mb} МБ. Загрузите договор меньшего размера.`}
              style={{ marginTop: 8 }}
            />
          )}
        </Form.Item>
        <Form.Item
          label="Режим закупки"
          name="procurement"
          extra="Для госконтрактов и закупок по 223-ФЗ анализ учитывает особые требования."
        >
          <Select options={PROCUREMENT_OPTIONS} />
        </Form.Item>
        <Form.Item label="Параметры сделки и что учесть" name="comment">
          <Input.TextArea
            rows={4}
            maxLength={2000}
            showCount
            placeholder="Сумма, предоплата, сроки, договорённости с покупателем — всё, что поможет анализу"
          />
        </Form.Item>
        <p className="z-hint" style={{ margin: 0 }}>
          Проверка и файл хранятся {limits ? `${limits.check_ttl_hours} ч` : 'ограниченное время'} после последнего действия,
          потом удаляются.
        </p>
        {error && <Alert type="error" showIcon title={error} style={{ marginTop: 12 }} />}
      </Form>
    </Modal>
  )
}
