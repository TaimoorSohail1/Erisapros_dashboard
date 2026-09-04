export type FilingStatus =
  | "QUEUED"
  | "UPLOADED"
  | "EXTRACTING"
  | "EXTRACTED"
  | "MAPPED"
  | "QUERYING_FTW_CURRENT"
  | "NEEDS_REVIEW"
  | "READY_FOR_APPROVAL"
  | "WAITING_FOR_WORKSHEET"
  | "WAITING_FOR_SCHEDULE_A"
  | "APPROVED"
  | "REJECTED"
  | "FAILED"
  | "SUPERSEDED"
  | "DELETED";

export type ExtractionJobStatus =
  | "QUEUED"
  | "SENT_TO_GROUNDX"
  | "EXTRACTING"
  | "RAW_EXTRACTION_SAVED"
  | "MAPPING"
  | "COMPLETED"
  | "FAILED";

export type FieldPriority = "HIGH" | "MEDIUM" | "LOW" | "IGNORE";
export type DocumentType = "SCHEDULE_A" | "PLAN_WORKSHEET" | "UNKNOWN";
export type FormType = "SCHEDULE_A" | "FORM_5500";
export type ScheduleAContractType = "UNKNOWN" | "EXPERIENCE_RATED" | "NONEXPERIENCE_RATED" | "NEEDS_REVIEW";

export interface ScheduleABrokerMoneyRow {
  coverage?: string | null;
  amount: string;
  purpose?: string | null;
}

export interface ScheduleABrokerRow {
  name: string;
  address_line_1?: string | null;
  address_line_2?: string | null;
  city?: string | null;
  state?: string | null;
  zip_code?: string | null;
  organization_code?: string | null;
  purpose?: string | null;
  commission_rows?: ScheduleABrokerMoneyRow[];
  fee_rows?: ScheduleABrokerMoneyRow[];
  commission_total?: string | null;
  fee_total?: string | null;
  source_page?: number | null;
  confidence?: number;
}

export interface ScheduleABrokerMatch {
  extracted_index: number;
  ftw_index?: number | null;
  status: "AUTO_MATCHED" | "CONFIRMED" | "CONFIRMED_NEW" | "NEEDS_CONFIRMATION" | string;
  resolved: boolean;
  reason: string;
  candidate_ftw_indexes?: number[];
  current_row?: ScheduleABrokerRow | null;
}

export interface ScheduleAWorksheetValue {
  label: string;
  value: string;
  source?: string | null;
  coverage?: string | null;
}

export interface ScheduleABenefitBreakdownRow {
  benefit_type: string;
  persons_covered?: string | null;
  premium?: string | null;
  source_page?: number | null;
}

export interface ScheduleAWorksheetSummary {
  source: string;
  carrier_name?: string | null;
  account_name?: string | null;
  account_number?: string | null;
  period_begin?: string | null;
  period_end?: string | null;
  ein?: string | null;
  naic_code?: string | null;
  coverage?: string | null;
  values?: ScheduleAWorksheetValue[];
  benefit_rows?: ScheduleABenefitBreakdownRow[];
  notes?: string[];
}

export interface Filing {
  id: string;
  file_name: string;
  content_type: string;
  file_size: number;
  document_type: DocumentType;
  package_document_count: number;
  package_documents: Array<Record<string, unknown>>;
  dashboard_client_name?: string | null;
  dashboard_ein?: string | null;
  dashboard_plan_number?: string | null;
  dashboard_plan_name?: string | null;
  intake_source?: "SHAREFILE" | "MANUAL" | string | null;
  status: FilingStatus;
  s3_key: string;
  s3_bucket?: string | null;
  storage_path?: string | null;
  extraction_provider?: string | null;
  error_message?: string | null;
  overall_confidence: number;
  missing_high_priority_count: number;
  missing_medium_priority_count: number;
  missing_low_priority_count: number;
  low_confidence_count: number;
  unmapped_count: number;
  review_field_count?: number;
  found_field_count?: number;
  excluded_field_count?: number;
  schedule_a_contract_type?: ScheduleAContractType;
  schedule_a_contract_type_reason?: string | null;
  schedule_a_contract_type_confirmed?: boolean;
  schedule_a_contract_type_confidence?: number;
  schedule_a_contract_type_evidence?: string[];
  schedule_a_classification_signals?: string[];
  ftw_schedule_a_contract_type?: ScheduleAContractType;
  ftw_schedule_a_contract_type_reason?: string | null;
  schedule_a_broker_rows?: ScheduleABrokerRow[];
  schedule_a_worksheet_summaries?: ScheduleAWorksheetSummary[];
  proposed_xml?: string | null;
  rejection_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExtractedField {
  id: string;
  filing_id: string;
  source_field_name: string;
  mapped_label?: string | null;
  ftw_field?: string | null;
  xml_tag?: string | null;
  priority: FieldPriority;
  value: string;
  proposed_value: string;
  confidence: number;
  page?: number | null;
  source_text?: string | null;
  source_document_type?: DocumentType | null;
  form_type?: FormType | null;
  ftw_current_value?: string | null;
  ftw_resolved_tag?: string | null;
  status: string;
  status_reason?: string | null;
}

export interface FTWilliamsComparisonField {
  field_id?: string | null;
  rule_key?: string | null;
  label: string;
  form_type?: FormType | null;
  source_document_type?: DocumentType | null;
  ftw_tag?: string | null;
  current_value: string;
  extracted_value: string;
  proposed_value: string;
  confidence: number;
  priority: FieldPriority;
  extraction_status: string;
  changed: boolean;
  update_included: boolean;
  update_exclusion_reason?: string | null;
  validation_status?: "VALID" | "INVALID" | "REQUIRED" | "UNSUPPORTED" | "REVIEW_REQUIRED" | string;
  validation_message?: string | null;
  validation_expected_format?: string | null;
  validation_normalized_value?: string | null;
  validation_blocking?: boolean;
}

export interface FTWilliamsPlanLookup {
  status: string;
  company_employer_id?: string | null;
  plan_number?: string | null;
  year?: string | null;
  plan_name?: string | null;
  sponsor_name?: string | null;
  request_xml?: string | null;
  response_xml?: string | null;
  error_message?: string | null;
  matches: Array<Record<string, unknown>>;
  matched_identity?: Record<string, unknown> | null;
}

export interface ClientRejectedField {
  tag: string;
  label?: string | null;
  value?: string | null;
  reason?: string | null;
  suggested_value?: string | null;
  form_type?: string | null;
  field_id?: string | null;
}

export interface ClientFacingError {
  title: string;
  message: string;
  reason?: string | null;
  next_action?: string | null;
  severity?: "error" | "warning" | "info" | string;
  source?: string | null;
  code?: string | null;
  technical_details?: string | null;
  rejected_fields?: ClientRejectedField[];
}

export interface FTWilliamsOperationDiagnostic {
  operation: string;
  sent: boolean;
  http_status?: number | null;
  outcome_code: string;
  response_received: boolean;
  response_content_type?: string | null;
  response_content_length?: string | null;
  request_id?: string | null;
  elapsed_ms?: number | null;
  error_code?: string | null;
  error_description?: string | null;
  response_excerpt?: string | null;
}

export interface FTWilliamsEditCheckIssue {
  code: string;
  message: string;
  status_type?: string | null;
  form_type: string;
  schedule_seq_no?: string | null;
  schedule_desc?: string | null;
  field_line?: string | null;
  field_label?: string | null;
  current_value?: string | null;
  correction?: string | null;
}

export interface FTWilliamsSchemaValidationIssue {
  tag: string;
  value: string;
  reason: string;
  expected_format?: string | null;
  correction?: string | null;
}

export interface FTWilliamsSchemaValidationResult {
  valid: boolean;
  mode: "OBSERVE" | "ENFORCE" | string;
  schema_source: string;
  schema_version?: string | null;
  issues: FTWilliamsSchemaValidationIssue[];
  validated_at: string;
}

export interface FTWilliamsReview {
  id?: string | null;
  filing_id: string;
  status: string;
  configured: boolean;
  current_query_sent: boolean;
  current_query_success: boolean;
  current_query_complete?: boolean | null;
  query_state?: "NOT_QUERIED" | "MATCHED" | "SCHEDULE_A_MISSING" | "PLAN_MATCH_REQUIRED" | "QUERY_FAILED";
  current_year_exists: boolean;
  bring_forward_required: boolean;
  ftw_editable?: boolean | null;
  ftw_locked_status?: string | null;
  ftw_signed_status?: string | null;
  ftw_filing_status?: string | null;
  ftw_plan_url?: string | null;
  comparison_year?: string | null;
  comparison_year_source?: string | null;
  plan_year_conflict?: {
    worksheet_begin?: string | null;
    worksheet_end?: string | null;
    ftw_form_begin?: string | null;
    ftw_form_end?: string | null;
    ftw_schedule_a_begin?: string | null;
    ftw_schedule_a_end?: string | null;
  } | null;
  plan_year_resolution?: "USE_WORKSHEET" | "KEEP_FTW" | null;
  plan_year_resolution_begin?: string | null;
  plan_year_resolution_end?: string | null;
  schedule_a_match?: Record<string, unknown> | null;
  schedule_a_candidates?: Array<Record<string, unknown>>;
  schedule_a_records?: Array<Record<string, unknown>>;
  schedule_a_broker_rows?: ScheduleABrokerRow[];
  schedule_a_broker_matches?: ScheduleABrokerMatch[];
  schedule_a_broker_match_complete?: boolean;
  schedule_a_worksheet_summaries?: ScheduleAWorksheetSummary[];
  schedule_a_contract_type?: ScheduleAContractType;
  schedule_a_contract_type_reason?: string | null;
  schedule_a_contract_type_confirmed?: boolean;
  schedule_a_contract_type_confidence?: number;
  schedule_a_contract_type_evidence?: string[];
  ftw_schedule_a_contract_type?: ScheduleAContractType;
  ftw_schedule_a_contract_type_reason?: string | null;
  schedule_a_contract_type_mismatch?: boolean;
  customer_id?: string | null;
  plan_id?: string | null;
  year?: string | null;
  ftw_customer_id?: string | null;
  ftw_plan_id?: string | null;
  ftw_seq_no?: string | null;
  plan_lookup?: FTWilliamsPlanLookup | null;
  query_request_xml?: string | null;
  query_response_xml?: string | null;
  form_5500_current_values?: Record<string, string>;
  schedule_a_current_values?: Record<string, string>;
  update_xml_5500?: string | null;
  update_xml_schedule_a?: string | null;
  update_response_xml?: string | null;
  update_verification_attempted?: boolean;
  update_verification_success?: boolean | null;
  update_verification_mismatches?: Array<Record<string, unknown>>;
  update_verification_request_xml?: string | null;
  update_verification_response_xml?: string | null;
  update_attempted_count?: number;
  update_confirmed_count?: number;
  update_remaining_count?: number;
  update_results?: Array<{
    field_id?: string | null;
    tag?: string | null;
    label: string;
    form_type?: string | null;
    sent_value?: string | null;
    returned_value?: string | null;
    status: "VERIFIED" | "NEEDS_CORRECTION" | string;
    reason?: string | null;
  }>;
  update_retry_count?: number;
  update_diagnostics?: FTWilliamsOperationDiagnostic[];
  schema_validation_results?: FTWilliamsSchemaValidationResult[];
  schema_validation_blocked?: boolean;
  query_access_verified?: boolean;
  update_access_status?: string;
  edit_check_baseline_request_xml?: string | null;
  edit_check_baseline_response_xml?: string | null;
  edit_check_baseline_success?: boolean | null;
  edit_check_baseline_issues?: FTWilliamsEditCheckIssue[];
  edit_check_request_xml?: string | null;
  edit_check_response_xml?: string | null;
  edit_check_final_success?: boolean | null;
  edit_check_final_issues?: FTWilliamsEditCheckIssue[];
  edit_check_validation_status?: string;
  edit_check_new_issues?: FTWilliamsEditCheckIssue[];
  edit_check_resolved_issues?: FTWilliamsEditCheckIssue[];
  audit_pdf_status?: string;
  audit_pdf_sha256?: string | null;
  audit_pdf_created_at?: string | null;
  audit_pdf_error?: string | null;
  error_message?: string | null;
  client_error?: ClientFacingError | null;
  active_failure?: boolean;
  active_failure_reason?: string | null;
  active_failure_client_error?: ClientFacingError | null;
  active_failure_at?: string | null;
  failure_dismissed_at?: string | null;
  failure_dismissed_reason?: string | null;
  fields: FTWilliamsComparisonField[];
  created_at: string;
  updated_at: string;
}

export type FTWilliamsHistoryRange = "1d" | "7d" | "30d";

export interface FTWilliamsHistoryItem {
  id?: string | null;
  filing_id: string;
  filing_name: string;
  filing_status?: FilingStatus | null;
  action: string;
  action_label: string;
  status: "success" | "failed" | "info" | string;
  message: string;
  company_employer_id?: string | null;
  plan_number?: string | null;
  plan_name?: string | null;
  sponsor_name?: string | null;
  customer_id?: string | null;
  plan_id?: string | null;
  ftw_customer_id?: string | null;
  ftw_plan_id?: string | null;
  year?: string | null;
  updated_field_count?: number | null;
  error_message?: string | null;
  created_at: string;
}

export interface FTWilliamsHistoryResponse {
  range: FTWilliamsHistoryRange;
  days: number;
  items: FTWilliamsHistoryItem[];
}

export type FTWilliamsFailureType = "NEEDS_RETRY" | "NEEDS_DATA_FIX" | "NEEDS_PLAN_MATCH" | "NEEDS_SERVICE_CHECK";

export interface FTWilliamsFailureIssueGroup {
  label: string;
  count: number;
}

export interface FTWilliamsFailureQueueSummary {
  filing_id: string;
  filing_name: string;
  filing_status: FilingStatus;
  review_status: string;
  failure_type: FTWilliamsFailureType;
  short_reason: string;
  next_action?: string | null;
  plan_name?: string | null;
  sponsor_name?: string | null;
  company_employer_id?: string | null;
  plan_number?: string | null;
  customer_id?: string | null;
  plan_id?: string | null;
  ftw_customer_id?: string | null;
  ftw_plan_id?: string | null;
  year?: string | null;
  attempted_field_count: number;
  issue_count: number;
  issue_groups: FTWilliamsFailureIssueGroup[];
  failed_at: string;
  last_action_label: string;
  error_code?: string | null;
  can_dismiss?: boolean;
}

export interface FTWilliamsFailureQueueItem extends FTWilliamsFailureQueueSummary {
  failure_reason: string;
  technical_details?: string | null;
  operation_diagnostics?: FTWilliamsOperationDiagnostic[];
  edit_check_issues?: FTWilliamsEditCheckIssue[];
}

export interface FTWilliamsFailureCounts {
  active: number;
  needs_retry: number;
  needs_data_fix: number;
  needs_plan_match: number;
  needs_service_check: number;
}

export interface FTWilliamsFailureQueueResponse {
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  counts: FTWilliamsFailureCounts;
  items: FTWilliamsFailureQueueSummary[];
}

export interface FTWilliamsFailureNotificationResponse {
  total: number;
  counts: FTWilliamsFailureCounts;
  items: FTWilliamsFailureQueueSummary[];
}

export interface ReviewEvent {
  id: string;
  filing_id: string;
  type: string;
  reason?: string | null;
  created_at: string;
}

export interface ExtractionJob {
  id: string;
  filing_id: string;
  status: ExtractionJobStatus;
  provider: string;
  attempts: number;
  max_attempts: number;
  last_error?: string | null;
  next_retry_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditLog {
  id: string;
  filing_id?: string | null;
  event: string;
  message: string;
  details?: Record<string, unknown> | null;
  created_at: string;
}

export interface FilingDetail extends Filing {
  fields: ExtractedField[];
  events: ReviewEvent[];
  jobs: ExtractionJob[];
  audit_logs: AuditLog[];
  ftw_review?: FTWilliamsReview | null;
}

export interface FieldRule {
  id?: string | null;
  key: string;
  label: string;
  ftw_field: string;
  xml_tag?: string | null;
  mapping_mode: "FTW_MAPPED" | "EXTRACTION_ONLY";
  update_supported?: boolean;
  approved_update_tag?: string | null;
  priority: FieldPriority;
  source: string;
  form_section?: string | null;
  field_type: string;
  existing_or_new: string;
  existing_behavior?: string | null;
  new_behavior?: string | null;
  notes?: string | null;
  client_notes?: string | null;
  aliases: string[];
  required: boolean;
  order: number;
  applicability: "BOTH" | "EXPERIENCE" | "NONEXPERIENCE" | "FORM_5500";
  status: "DRAFT" | "PUBLISHED" | "DISABLED" | "SUPERSEDED";
  version: number;
  updated_by?: string | null;
  change_reason?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface FTWFieldCatalogEntry {
  key: string;
  label: string;
  form_type: FormType;
  form_section?: string | null;
  supported_years: string[];
  value_type: string;
  format_hint: string;
  current_tag?: string | null;
  update_tag?: string | null;
  update_supported: boolean;
  read_only_reason?: string | null;
  contract_version: string;
  catalog_tier: "VERIFIED" | "DISCOVERED";
}

export interface FieldRuleQAField {
  field_name: string;
  value: string;
  confidence: number;
  page?: number | null;
  source_text?: string | null;
  matched: boolean;
  matched_alias?: string | null;
  mapped_rule_key?: string | null;
  mapped_label?: string | null;
  mapping_mode?: "FTW_MAPPED" | "EXTRACTION_ONLY" | null;
  ftw_field?: string | null;
  ftw_tag?: string | null;
  will_send_to_ftw: boolean;
}

export interface FieldRuleQAResult {
  provider: string;
  rule_set_version: string;
  document_type: "SCHEDULE_A" | "PLAN_WORKSHEET";
  file_name: string;
  summary: {
    extracted: number;
    matched: number;
    unmatched: number;
    extraction_only: number;
  };
  fields: FieldRuleQAField[];
}
