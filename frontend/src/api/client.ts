// Обёртка над fetch для запросов к /api.
// Каждый запрос несёт заголовок X-Requested-With: contract-review — без него сервер отклоняет изменяющие запросы.

export const CSRF_HEADER = 'X-Requested-With'
export const CSRF_VALUE = 'contract-review'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

/** Смещение часового пояса браузера от UTC в минутах: по нему сервер считает местные дату и время. */
export function utcOffset(): string {
  return String(-new Date().getTimezoneOffset())
}

interface RequestOptions {
  method?: Method
  body?: unknown
}

const UNAVAILABLE = 'Сервер недоступен. Проверьте подключение и попробуйте ещё раз.'

async function send(path: string, init: RequestInit): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, { credentials: 'same-origin', ...init })
  } catch {
    throw new ApiError(0, UNAVAILABLE)
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response))
  }
  return response
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body } = options
  const headers: Record<string, string> = { [CSRF_HEADER]: CSRF_VALUE, Accept: 'application/json' }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await send(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

/** Отправка формы с файлом (multipart). Content-Type с границей частей браузер ставит сам. */
export async function apiUpload<T>(path: string, form: FormData, method: 'POST' | 'PUT' = 'POST'): Promise<T> {
  const response = await send(path, {
    method,
    headers: { [CSRF_HEADER]: CSRF_VALUE, Accept: 'application/json' },
    body: form,
  })
  return (await response.json()) as T
}

/**
 * Скачать файл и предложить сохранить его. Имя берётся из Content-Disposition (filename* в UTF-8).
 * С body запрос идёт методом POST: так скачивается образец протокола по несохранённому шаблону.
 */
export async function apiDownload(path: string, fallbackName: string, body?: unknown): Promise<void> {
  const response = await send(
    path,
    body === undefined
      ? { method: 'GET', headers: { [CSRF_HEADER]: CSRF_VALUE } }
      : {
          method: 'POST',
          headers: { [CSRF_HEADER]: CSRF_VALUE, 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  )
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
  const name = encoded ? decodeURIComponent(encoded) : fallbackName
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.append(link)
  link.click()
  link.remove()
  // Ссылка нужна браузеру, пока он начинает загрузку.
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Ошибки проверки полей (422): тексты из валидаторов сервера, например про подстановки шаблона. */
function validationMessage(detail: unknown[]): string {
  const messages = detail
    .map((item) => (item !== null && typeof item === 'object' && 'msg' in item ? String(item.msg) : ''))
    .map((text) => text.replace(/^Value error, /, ''))
    .filter((text) => text !== '')
  const unique = [...new Set(messages)]
  return unique.length > 0 ? `Проверьте заполнение полей: ${unique.join('; ')}` : 'Проверьте заполнение полей'
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json()
    if (data !== null && typeof data === 'object' && 'detail' in data) {
      const { detail } = data as { detail: unknown }
      if (typeof detail === 'string') {
        return detail
      }
      if (Array.isArray(detail)) {
        return validationMessage(detail)
      }
    }
  } catch {
    // Ответ не JSON — например, ошибка прокси.
  }
  return `Ошибка сервера (${response.status})`
}
