// Общее для разделов админки: сообщения об ошибках, деньги, токены, цены моделей.

import { ApiError } from '../../api/client.ts'

export function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

export function money(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : `${value.toFixed(digits).replace('.', ',')} ₽`
}

export function tokens(input: number | null | undefined, output: number | null | undefined): string {
  return input == null && output == null ? '—' : `${input ?? '—'} / ${output ?? '—'}`
}

const CURRENCY = { RUB: '₽', USD: '$' } as const

/** Цена за 1 млн токенов: в списке моделей она за один токен. */
export function pricePerMillion(value: number | null, currency: 'RUB' | 'USD' | null): string {
  if (value == null || currency == null) {
    return '—'
  }
  return `${(value * 1_000_000).toFixed(2).replace('.', ',')} ${CURRENCY[currency]}`
}
