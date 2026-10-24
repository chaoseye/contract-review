// Проверки договоров, результат анализа и протокол (backend/app/api/checks.py, protocol.py).

import { apiDownload, apiRequest, apiUpload, utcOffset } from './client.ts'

export type CheckStatus = 'uploaded' | 'extracting' | 'extracted' | 'analyzing' | 'ready' | 'failed'
export type Procurement = 'commercial' | '223fz' | '44fz' | 'unknown'
export type Severity = 'high' | 'medium' | 'low'
export type QuoteStatus = 'exact' | 'fuzzy' | 'not_found' | 'empty' | 'new_clause'
export type Party = 'buyer' | 'supplier'
export type Specialist = 'lawyer' | 'finance' | 'production' | 'security'

export interface CheckSummary {
  id: string
  status: CheckStatus
  progress: string | null
  file_name: string
  file_kind: string
  file_size: number
  error: string | null
  created_at: string
  expires_at: string
  owner_login: string | null
  // Договор и риски — когда есть результат анализа; номер и покупатель — с правками протокола.
  contract_kind: string | null
  contract_number: string | null
  buyer_name: string | null
  risk_count: number | null
  stop_count: number | null
  // Последний выпуск протокола после последнего анализа.
  protocol_downloaded_at: string | null
  protocol_format: 'docx' | 'pdf' | null
}

export interface Block {
  id: string
  kind: 'heading' | 'paragraph' | 'cell'
  text: string
  clause: string | null
  section: string | null
  page: number | null
}

export interface Risk {
  id: string
  clauses: string[]
  block_ids: string[]
  is_new_clause: boolean
  quote: string
  title: string
  explanation: string
  severity: Severity
  stop_factor: boolean
  category: string
  recommendation: string
  proposed_wording: string | null
}

export interface Obligation {
  party: Party
  obligation: string
  deadline: string | null
  clauses: string[]
  block_ids: string[]
}

export interface Contradiction {
  description: string
  clauses: string[]
  block_ids: string[]
}

export interface FavorableTerm extends Contradiction {
  quote: string
}

export interface AnalysisAnswer {
  contract: {
    kind: string | null
    number: string | null
    date: string | null
    city: string | null
    parties_order: Party[]
    buyer: { name: string | null; representative: string | null; basis: string | null }
    our_role: 'supplier' | 'buyer' | 'other' | 'unknown'
    procurement: Procurement
  }
  summary: string
  approval: { allowed: boolean; reasons: string[] }
  confidence: { level: Severity; reasons: string[] }
  top_risks: string[]
  risks: Risk[]
  obligations: Obligation[]
  contradictions: Contradiction[]
  missing: { item: string; reason: string }[]
  favorable: FavorableTerm[]
  specialist_checks: { specialist: Specialist; what: string }[]
}

export interface Reference {
  pages: number[]
  unknown_block_ids: string[]
  unknown_clauses: string[]
  quote_status?: QuoteStatus
}

export interface RiskCheck extends Reference {
  quote_status: QuoteStatus
  found_block_ids: string[]
}

export interface AnalysisResult {
  answer: AnalysisAnswer
  risk_checks: Record<string, RiskCheck>
  refs: { obligations: Reference[]; contradictions: Reference[]; favorable: Reference[] }
  warnings: string[]
}

export interface AnalysisMeta {
  profile?: string
  model?: string
  response_format?: string
  attempts?: number
  input_tokens?: number | null
  output_tokens?: number | null
  cost_rub?: number | null
  cost_estimated?: boolean
  duration_seconds?: number
  started_at?: string
  finished_at?: string
  skipped?: string
  error?: { type: string; message: string; detail: string | null } | null
}

export type RequisiteStatus = 'match' | 'mismatch' | 'not_found'

/** Наша сторона и сверка реквизитов с карточкой организации (backend/app/analysis/requisites.py). */
export interface SideReport {
  status: 'found' | 'not_found' | 'skipped'
  role: 'supplier' | 'buyer' | 'other' | 'unknown'
  role_word: string | null
  found_by: ('inn' | 'name')[]
  preamble_block: string | null
  requisites_block: string | null
  checks: { key: string; label: string; expected: string; found: string[]; status: RequisiteStatus }[]
  warnings: string[]
}

export interface CheckDetail extends CheckSummary {
  comment: string
  procurement: Procurement
  ocr_used: boolean
  page_count: number | null
  warnings: string[]
  blocks: Block[] | null
  result: AnalysisResult | null
  analysis: AnalysisMeta | null
  requisites: SideReport | null
}

export interface Limits {
  check_ttl_hours: number
  max_upload_mb: number
}

export function getLimits(): Promise<Limits> {
  return apiRequest<Limits>('/limits')
}

export function listChecks(): Promise<CheckSummary[]> {
  return apiRequest<CheckSummary[]>('/checks')
}

export function getCheck(id: string): Promise<CheckDetail> {
  return apiRequest<CheckDetail>(`/checks/${id}`)
}

export function uploadCheck(file: File, procurement: Procurement, comment: string): Promise<CheckSummary> {
  const form = new FormData()
  form.append('file', file)
  form.append('procurement', procurement)
  form.append('comment', comment)
  return apiUpload<CheckSummary>('/checks', form)
}

export function deleteCheck(id: string): Promise<void> {
  return apiRequest<void>(`/checks/${id}`, { method: 'DELETE' })
}

export function analyzeCheck(id: string): Promise<CheckSummary> {
  return apiRequest<CheckSummary>(`/checks/${id}/analyze`, { method: 'POST' })
}

// --- протокол ---------------------------------------------------------------------------------

export interface ProtocolMeta {
  title: string
  contract_kind: string
  contract_number: string
  contract_date: string | null
  city: string
  protocol_date: string | null
  buyer_full_name: string
  buyer_short_name: string
  buyer_representative: string
  buyer_basis: string
  buyer_signer: string
  supplier_first: boolean
  include_agreed_column: boolean
  appendices: string[]
  preamble: string
  table_font_size: number
}

export interface EditableRow {
  id: string
  risk_id: string | null
  include: boolean
  clause: string
  buyer_text: string
  supplier_text: string
}

export interface ProtocolState {
  meta: ProtocolMeta
  rows: EditableRow[]
}

export interface ProtocolOut {
  state: ProtocolState
  preamble_preview: string
  organization: { ready: boolean; signature: boolean; stamp: boolean }
  saved: boolean
}

// Черновик протокола ставит договору без даты сегодняшнюю дату по часам браузера.
function draftQuery(extra: Record<string, string> = {}): URLSearchParams {
  return new URLSearchParams({ ...extra, utc_offset: utcOffset() })
}

export function getProtocol(id: string): Promise<ProtocolOut> {
  return apiRequest<ProtocolOut>(`/checks/${id}/protocol?${draftQuery()}`)
}

export function saveProtocol(id: string, state: ProtocolState): Promise<ProtocolOut> {
  return apiRequest<ProtocolOut>(`/checks/${id}/protocol`, { method: 'PUT', body: state })
}

export function resetProtocol(id: string): Promise<ProtocolOut> {
  return apiRequest<ProtocolOut>(`/checks/${id}/protocol/reset?${draftQuery()}`, { method: 'POST' })
}

export function downloadProtocol(
  id: string,
  format: 'docx' | 'pdf',
  signature: boolean,
  stamp: boolean,
): Promise<void> {
  const query = draftQuery({ signature: String(signature), stamp: String(stamp) })
  return apiDownload(`/checks/${id}/protocol.${format}?${query}`, `Протокол разногласий.${format}`)
}

/**
 * Пока проверка в одном из этих состояний, страница опрашивает сервер. Анализ в очереди — тоже analyzing
 *: по тексту хода «в работе» не определяется.
 */
export function isInProgress(check: Pick<CheckSummary, 'status'>): boolean {
  return check.status === 'uploaded' || check.status === 'extracting' || check.status === 'analyzing'
}
