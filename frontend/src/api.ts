import type {
  ExtractedField,
  FieldRule,
  Filing,
  FilingDetail,
  FTWilliamsFailureQueueResponse,
  FTWilliamsHistoryRange,
  FTWilliamsHistoryResponse,
  FTWilliamsReview,
} from "./types";
import { getIdToken } from "./auth";

const API_BASE =
  import.meta.env.VITE_API_BASE_URL || (import.meta.env.PROD ? "/api" : "http://localhost:8001");

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  const idToken = await getIdToken();
  if (idToken) headers.set("Authorization", `Bearer ${idToken}`);
  const response = await fetch(API_BASE + path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || payload.error || "Request failed");
  }
  return response.json();
}

export async function listFilings(): Promise<Filing[]> {
  const payload = await request<{ filings: Filing[] }>("/filings");
  return payload.filings;
}

export async function listFTWilliamsHistory(range: FTWilliamsHistoryRange): Promise<FTWilliamsHistoryResponse> {
  return request<FTWilliamsHistoryResponse>("/ftwilliams/history?range=" + encodeURIComponent(range));
}

export async function listFTWilliamsFailureQueue(): Promise<FTWilliamsFailureQueueResponse> {
  return request<FTWilliamsFailureQueueResponse>("/ftwilliams/failure-queue");
}

export async function getFiling(id: string): Promise<FilingDetail> {
  return request<FilingDetail>("/filings/" + id);
}

export async function deleteFiling(filingId: string): Promise<{ status: string }> {
  return request("/filings/" + filingId, { method: "DELETE" });
}

export async function updateField(
  filingId: string,
  fieldId: string,
  proposedValue: string,
  options?: { markMissing?: boolean },
): Promise<{ field: ExtractedField; proposed_xml: string; ftw_review?: FTWilliamsReview | null }> {
  return request("/filings/" + filingId + "/fields/" + fieldId, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ proposed_value: proposedValue, mark_missing: Boolean(options?.markMissing) })
  });
}

export async function approveFiling(
  filingId: string,
  reason: string,
  options?: { send_to_ftw?: boolean; refresh_current_before_update?: boolean; run_edit_checks?: boolean; override_blockers?: boolean },
): Promise<{ status: string; ftw_review?: FTWilliamsReview | null }> {
  return request("/filings/" + filingId + "/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason, ...(options || {}) })
  });
}

export async function unapproveFiling(filingId: string): Promise<{ status: string }> {
  return request("/filings/" + filingId + "/unapprove", { method: "POST" });
}

export async function rejectFiling(filingId: string, reason: string): Promise<void> {
  await request("/filings/" + filingId + "/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason })
  });
}

export async function regenerateXml(filingId: string): Promise<{ proposed_xml: string }> {
  return request("/filings/" + filingId + "/regenerate-xml", { method: "POST" });
}

export async function prepareFTWilliamsReview(filingId: string, sendQueries = false): Promise<{ ftw_review: FTWilliamsReview }> {
  return request("/filings/" + filingId + "/ftw/prepare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ send_queries: sendQueries })
  });
}

export async function getFTWilliamsBringForwardLink(filingId: string): Promise<{
  url: string;
  target_year?: string | null;
  prior_year?: string | null;
}> {
  return request("/filings/" + filingId + "/ftw/bring-forward-link", { method: "POST" });
}

export async function saveManualFTWilliamsMatch(
  filingId: string,
  payload: {
    customer_id?: string;
    plan_id?: string;
    ftw_customer_id?: string;
    ftw_plan_id?: string;
    year?: string;
  },
): Promise<{ ftw_review: FTWilliamsReview }> {
  return request("/filings/" + filingId + "/ftw/manual-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function selectFTWilliamsScheduleAMatch(
  filingId: string,
  payload: {
    ftw_seq_no?: string;
    carrier?: string;
    carrier_ein?: string;
    contract?: string;
    create_new?: boolean;
    schedule_desc?: string;
  },
): Promise<{ ftw_review: FTWilliamsReview }> {
  return request("/filings/" + filingId + "/ftw/schedule-a-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function sendApprovedFTWilliamsUpdate(
  filingId: string,
  payload: {
    reason?: string;
    refresh_current_before_update?: boolean;
    run_edit_checks?: boolean;
  },
): Promise<{ ftw_review: FTWilliamsReview | null }> {
  return request("/filings/" + filingId + "/ftw/send-update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}

export async function retryExtraction(filingId: string): Promise<{ id: string; status: string; job_id?: string }> {
  return request("/filings/" + filingId + "/retry-extraction", { method: "POST" });
}

export interface FieldRuleListResponse {
  field_rules: FieldRule[];
  published_version: string;
  can_manage: boolean;
}

export async function listFieldRules(): Promise<FieldRuleListResponse> {
  return request<FieldRuleListResponse>("/field-rules");
}

export async function saveFieldRuleDraft(rule: FieldRule, reason: string): Promise<FieldRule> {
  const payload = await request<{ field_rule: FieldRule }>("/field-rules/drafts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule, reason })
  });
  return payload.field_rule;
}

export async function testFieldRule(rule: FieldRule, sampleFieldName: string): Promise<{
  valid: boolean;
  matched: boolean;
  mapped_rule_key?: string | null;
  mapped_ftw_field?: string | null;
  message: string;
}> {
  return request("/field-rules/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rule, sample_field_name: sampleFieldName })
  });
}

export async function publishFieldRule(key: string, reason: string): Promise<FieldRule> {
  const payload = await request<{ field_rule: FieldRule }>(`/field-rules/${encodeURIComponent(key)}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason })
  });
  return payload.field_rule;
}

export async function disableFieldRule(key: string, reason: string): Promise<FieldRule> {
  const payload = await request<{ field_rule: FieldRule }>(`/field-rules/${encodeURIComponent(key)}/disable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason })
  });
  return payload.field_rule;
}

export async function getFieldRuleHistory(key: string): Promise<FieldRule[]> {
  const payload = await request<{ history: FieldRule[] }>(`/field-rules/${encodeURIComponent(key)}/history`);
  return payload.history;
}

export async function rollbackFieldRule(key: string, version: number, reason: string): Promise<FieldRule> {
  const payload = await request<{ field_rule: FieldRule }>(`/field-rules/${encodeURIComponent(key)}/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version, reason })
  });
  return payload.field_rule;
}

export async function reEvaluateFilingRules(filingId: string): Promise<{ status: string; field_rule_set_version: string; field_count: number }> {
  return request(`/filings/${filingId}/re-evaluate-rules`, { method: "POST" });
}

export async function getShareFileStatus(): Promise<{
  configured: boolean;
  connected?: boolean;
  message: string;
  subdomain?: string;
  intake_folder_id?: string;
  configured_folder_ids?: string[];
  discover_shared_folders?: boolean;
  shared_root_folder_id?: string;
  scan_scope?: string;
}> {
  return request("/sharefile/status");
}

export async function getShareFileAuthorizationUrl(): Promise<{ configured: boolean; authorization_url?: string; redirect_uri?: string; message?: string }> {
  return request("/sharefile/oauth/start");
}

export async function syncShareFileFolder(): Promise<{
  connected: boolean;
  folder_access: boolean;
  found: number;
  useful?: number;
  supported: number;
  packages?: number;
  synced: number;
  skipped: number;
  failed: number;
  deleted?: number;
  scan_scope?: string;
  scan_roots?: Array<{ id: string; name: string; source: string; path: string }>;
  scan_errors?: Array<{ folder_id: string; path: string; status_code: number; response: string }>;
  message: string;
}> {
  return request("/sharefile/sync-folder", { method: "POST" });
}

export async function pollShareFileFolder(): Promise<{
  connected: boolean;
  folder_access: boolean;
  found: number;
  useful?: number;
  supported: number;
  packages?: number;
  synced: number;
  skipped: number;
  failed: number;
  deleted?: number;
  scan_scope?: string;
  scan_roots?: Array<{ id: string; name: string; source: string; path: string }>;
  scan_errors?: Array<{ folder_id: string; path: string; status_code: number; response: string }>;
  message: string;
}> {
  return request("/sharefile/poll", { method: "POST" });
}
