// Админка: пользователи, промпт, подключения ИИ, организация, шаблон протокола, лимиты, журнал, перенос
// (backend/app/api/admin/).

import type { AnalysisResult } from './checks.ts'
import { apiDownload, apiRequest, apiUpload, utcOffset } from './client.ts'
import type { UserRole } from './auth.ts'

// --- пользователи ---------------------------------------------------------------------------------

export interface AdminUser {
  id: number
  login: string
  name: string
  role: UserRole
  is_active: boolean
  created_at: string
  last_seen_at: string | null
}

export function listUsers(): Promise<AdminUser[]> {
  return apiRequest<AdminUser[]>('/admin/users')
}

export function createUser(body: { login: string; name: string; role: UserRole; password: string }): Promise<AdminUser> {
  return apiRequest<AdminUser>('/admin/users', { method: 'POST', body })
}

export function updateUser(
  id: number,
  body: Partial<{ name: string; role: UserRole; is_active: boolean }>,
): Promise<AdminUser> {
  return apiRequest<AdminUser>(`/admin/users/${id}`, { method: 'PATCH', body })
}

export function resetPassword(id: number, password: string): Promise<void> {
  return apiRequest<void>(`/admin/users/${id}/password`, { method: 'POST', body: { password } })
}

// --- промпт ---------------------------------------------------------------------------------------

export interface PromptVersion {
  id: number
  comment: string
  author: string | null
  created_at: string
  is_active: boolean
  fingerprint: string
  length: number
}

export interface PromptVersionFull extends PromptVersion {
  text: string
}

export interface PromptUsage {
  model?: string
  response_format?: string
  attempts?: number
  input_tokens?: number | null
  output_tokens?: number | null
  cost_rub?: number | null
  cost_estimated?: boolean
  duration_seconds?: number
}

export interface PromptRun {
  id: string
  check_id: string
  profile: string
  fingerprint: string
  status: 'running' | 'done' | 'failed'
  started_at: string
  finished_at: string | null
  result: AnalysisResult | null
  usage: PromptUsage
  error: string | null
  detail: string | null
}

export function listPromptVersions(): Promise<PromptVersion[]> {
  return apiRequest<PromptVersion[]>('/admin/prompts')
}

export function getPromptVersion(id: number): Promise<PromptVersionFull> {
  return apiRequest<PromptVersionFull>(`/admin/prompts/${id}`)
}

export function createPromptVersion(text: string, comment: string, activate: boolean): Promise<PromptVersionFull> {
  return apiRequest<PromptVersionFull>('/admin/prompts', { method: 'POST', body: { text, comment, activate } })
}

export function activatePromptVersion(id: number): Promise<PromptVersionFull> {
  return apiRequest<PromptVersionFull>(`/admin/prompts/${id}/activate`, { method: 'POST' })
}

export function startPromptRun(text: string, checkId: string, profileId: number | null): Promise<PromptRun> {
  return apiRequest<PromptRun>('/admin/prompts/test', {
    method: 'POST',
    body: { text, check_id: checkId, profile_id: profileId },
  })
}

export function getPromptRun(id: string): Promise<PromptRun> {
  return apiRequest<PromptRun>(`/admin/prompts/test/${id}`)
}

// --- подключения ИИ -------------------------------------------------------------------------------

export type LlmKind = 'openai_compat' | 'anthropic' | 'codex_cli'
export type ResponseFormat = 'auto' | 'json_schema' | 'json_object' | 'none'
export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export interface ModelInfo {
  id: string
  name: string
  context_length: number | null
  max_output_tokens: number | null
  currency: 'RUB' | 'USD' | null
  price_input: number | null
  price_output: number | null
  price_cache_read: number | null
  supported_parameters: string[] | null
  countries: string[] | null
  checked_at: string | null
}

export interface ProfileParams {
  timeout_seconds: number
  max_retries: number
  response_format: ResponseFormat
  temperature: number | null
  max_output_tokens: number | null
  russia_only: boolean
  effort: Effort | null
  extra_body: Record<string, unknown>
  codex_config: string[]
}

export interface LlmProfile {
  id: number
  name: string
  kind: LlmKind
  base_url: string
  model: string
  is_active: boolean
  has_key: boolean
  key_hint: string
  params: ProfileParams
  model_info: ModelInfo | null
}

export interface LlmPreset {
  id: string
  title: string
  base_url: string
  needs_key: boolean
  russia_only: boolean
  kind: LlmKind
  effort: Effort | null
}

export interface ConnectionCheck {
  ok: boolean
  message: string
  detail: string | null
  notes: string[]
  model_info: ModelInfo | null
  model: string | null
  response_format: string | null
  duration_seconds: number | null
  input_tokens: number | null
  output_tokens: number | null
  cost_rub: number | null
  cost_estimated: boolean
}

export interface ProfileCreate {
  name: string
  kind: LlmKind
  base_url: string
  model: string
  api_key: string | null
  params: Partial<ProfileParams>
  activate: boolean
}

export type ProfileUpdate = Partial<Omit<ProfileCreate, 'kind' | 'activate'>>

export function listPresets(): Promise<LlmPreset[]> {
  return apiRequest<LlmPreset[]>('/admin/llm-presets')
}

export function listProfiles(): Promise<LlmProfile[]> {
  return apiRequest<LlmProfile[]>('/admin/llm-profiles')
}

export function createProfile(body: ProfileCreate): Promise<LlmProfile> {
  return apiRequest<LlmProfile>('/admin/llm-profiles', { method: 'POST', body })
}

export function updateProfile(id: number, body: ProfileUpdate): Promise<LlmProfile> {
  return apiRequest<LlmProfile>(`/admin/llm-profiles/${id}`, { method: 'PATCH', body })
}

export function activateProfile(id: number): Promise<LlmProfile> {
  return apiRequest<LlmProfile>(`/admin/llm-profiles/${id}/activate`, { method: 'POST' })
}

export function deleteProfile(id: number): Promise<void> {
  return apiRequest<void>(`/admin/llm-profiles/${id}`, { method: 'DELETE' })
}

export function testProfile(id: number): Promise<ConnectionCheck> {
  return apiRequest<ConnectionCheck>(`/admin/llm-profiles/${id}/test`, { method: 'POST' })
}

export function listModels(body: {
  kind: LlmKind
  base_url: string
  api_key: string | null
  profile_id: number | null
  search?: string
}): Promise<{ models: ModelInfo[]; total: number }> {
  return apiRequest('/admin/llm-models', { method: 'POST', body })
}

export interface CodexState {
  installed: boolean
  logged_in: boolean
  message: string
  login: {
    state: 'waiting' | 'done' | 'failed' | 'cancelled'
    url: string | null
    code: string | null
    lines: string[]
    started_at: string
  } | null
}

export function getCodex(): Promise<CodexState> {
  return apiRequest<CodexState>('/admin/codex')
}

export function codexLogin(): Promise<CodexState> {
  return apiRequest<CodexState>('/admin/codex/login', { method: 'POST' })
}

export function codexCancel(): Promise<CodexState> {
  return apiRequest<CodexState>('/admin/codex/login/cancel', { method: 'POST' })
}

export function codexLogout(): Promise<CodexState> {
  return apiRequest<CodexState>('/admin/codex/logout', { method: 'POST' })
}

// --- организация ----------------------------------------------------------------------------------

export type AssetKind = 'signature' | 'stamp' | 'header'

export const REQUISITE_FIELDS: { key: string; label: string; hint?: string }[] = [
  { key: 'inn', label: 'ИНН', hint: '12 цифр у ИП, 10 — у юрлица' },
  { key: 'ogrnip', label: 'ОГРНИП (ОГРН)', hint: '15 цифр у ИП, 13 — у юрлица' },
  { key: 'legal_address', label: 'Юридический адрес' },
  { key: 'postal_address', label: 'Почтовый адрес', hint: 'Как в карточке, с абонентским ящиком, если он есть' },
  { key: 'actual_address', label: 'Фактический адрес' },
  { key: 'bank', label: 'Банк' },
  { key: 'bik', label: 'БИК', hint: '9 цифр' },
  { key: 'account', label: 'Расчётный счёт', hint: '20 цифр' },
  { key: 'corr_account', label: 'Корреспондентский счёт', hint: '20 цифр' },
  { key: 'email', label: 'E-mail' },
  {
    key: 'email_domains',
    label: 'Домены e-mail',
    hint: 'Любой адрес на этих доменах при сверке считается нашим, например example.ru; несколько — через запятую',
  },
  { key: 'phone', label: 'Телефон', hint: 'Несколько — через запятую' },
]

export interface OrganizationData {
  full_name: string
  short_name: string
  signer: string
  representative: string
  basis: string
  default_city: string
  footer_text: string
  requisites: Record<string, string>
}

export interface OrganizationOut extends OrganizationData {
  assets: Record<AssetKind, boolean>
  /** Подпись и печать, чей фон и после подготовки закроет текст протокола. */
  opaque_background: Partial<Record<AssetKind, boolean>>
  ready_for_protocol: boolean
  updated_at: string | null
}

export function getOrganization(): Promise<OrganizationOut> {
  return apiRequest<OrganizationOut>('/admin/organization')
}

export function saveOrganization(body: OrganizationData): Promise<OrganizationOut> {
  return apiRequest<OrganizationOut>('/admin/organization', { method: 'PUT', body })
}

export function uploadAsset(kind: AssetKind, file: File): Promise<OrganizationOut> {
  const form = new FormData()
  form.append('file', file)
  return apiUpload<OrganizationOut>(`/admin/organization/assets/${kind}`, form, 'PUT')
}

export function deleteAsset(kind: AssetKind): Promise<OrganizationOut> {
  return apiRequest<OrganizationOut>(`/admin/organization/assets/${kind}`, { method: 'DELETE' })
}

/** Адрес картинки для <img>: cookie сессии браузер отправляет сам, version сбрасывает кэш после замены. */
export function assetUrl(kind: AssetKind, prepared: boolean, version: number): string {
  return `/api/admin/organization/assets/${kind}?prepared=${prepared}&v=${version}`
}

// --- шаблон протокола и лимиты --------------------------------------------------------------------

export interface ProtocolTemplate {
  title: string
  preamble: string
  closing_title: string
  closing_items: string[]
  appendices_item: string
  include_agreed_column: boolean
  letterhead: boolean
  table_font_size: number
}

export interface TemplateOut {
  template: ProtocolTemplate
  defaults: ProtocolTemplate
  fields: { preamble: Record<string, string>; closing: Record<string, string>; appendices: Record<string, string> }
}

export interface TemplatePreview {
  title: string
  subtitle: string
  preamble: string
  closing_title: string
  closing_items: string[]
}

export function getTemplate(): Promise<TemplateOut> {
  return apiRequest<TemplateOut>('/admin/protocol-template')
}

export function saveTemplate(template: ProtocolTemplate): Promise<ProtocolTemplate> {
  return apiRequest<ProtocolTemplate>('/admin/protocol-template', { method: 'PUT', body: template })
}

export function previewTemplate(template: ProtocolTemplate): Promise<TemplatePreview> {
  return apiRequest<TemplatePreview>('/admin/protocol-template/preview', { method: 'POST', body: template })
}

export function downloadTemplatePreview(
  template: ProtocolTemplate,
  format: 'docx' | 'pdf',
  signature: boolean,
  stamp: boolean,
): Promise<void> {
  const query = new URLSearchParams({ signature: String(signature), stamp: String(stamp) })
  return apiDownload(`/admin/protocol-template/preview.${format}?${query}`, `Образец протокола.${format}`, template)
}

export interface Limits {
  check_ttl_hours: number
  max_upload_mb: number
}

export interface LimitsOut extends Limits {
  defaults: Limits
}

export function getLimitsSettings(): Promise<LimitsOut> {
  return apiRequest<LimitsOut>('/admin/settings')
}

export function saveLimits(limits: Limits): Promise<LimitsOut> {
  return apiRequest<LimitsOut>('/admin/settings', { method: 'PUT', body: limits })
}

// --- журнал ---------------------------------------------------------------------------------------

export interface AuditItem {
  id: number
  at: string
  user_id: number | null
  login: string | null
  action: string
  action_label: string
  check_id: string | null
  counterparty: string | null
  contract_number: string | null
  with_signature: boolean | null
  with_stamp: boolean | null
  model: string | null
  tokens_in: number | null
  tokens_out: number | null
  cost_rub: number | null
  prompt_version: string | null
  details: string | null
}

export interface AuditPage {
  items: AuditItem[]
  total: number
  totals: { count: number; tokens_in: number; tokens_out: number; cost_rub: number }
  actions: Record<string, string>
}

export interface AuditFilters {
  at_from?: string
  at_to?: string
  user_id?: number
  action?: string[]
  check_id?: string
}

function auditQuery(filters: AuditFilters, extra: Record<string, string>): URLSearchParams {
  const query = new URLSearchParams(extra)
  if (filters.at_from) query.set('at_from', filters.at_from)
  if (filters.at_to) query.set('at_to', filters.at_to)
  if (filters.user_id !== undefined) query.set('user_id', String(filters.user_id))
  if (filters.check_id) query.set('check_id', filters.check_id)
  for (const action of filters.action ?? []) query.append('action', action)
  return query
}

export function listAudit(filters: AuditFilters, limit: number, offset: number): Promise<AuditPage> {
  return apiRequest<AuditPage>(`/admin/audit?${auditQuery(filters, { limit: String(limit), offset: String(offset) })}`)
}

export function downloadAuditCsv(filters: AuditFilters): Promise<void> {
  // Время в CSV — по часовому поясу браузера.
  const query = auditQuery(filters, { utc_offset: utcOffset() })
  return apiDownload(`/admin/audit.csv?${query}`, 'Журнал.csv')
}

// --- перенос --------------------------------------------------------------------------------------

export interface ImportReport {
  prompt_versions_added: number
  prompt_active: number | null
  profiles_added: string[]
  profiles_updated: string[]
  profiles_need_key: string[]
  profile_active: string | null
  organization: boolean
  protocol_template: boolean
  limits: boolean
  notes: string[]
}

export function exportSettings(): Promise<void> {
  return apiDownload('/admin/export', 'Настройки contract-review.json')
}

export function importSettings(file: File): Promise<ImportReport> {
  const form = new FormData()
  form.append('file', file)
  return apiUpload<ImportReport>('/admin/import', form)
}
