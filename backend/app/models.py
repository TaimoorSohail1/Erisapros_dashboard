from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class FilingStatus(str, Enum):
    QUEUED = "QUEUED"
    UPLOADED = "UPLOADED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    MAPPED = "MAPPED"
    QUERYING_FTW_CURRENT = "QUERYING_FTW_CURRENT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    WAITING_FOR_WORKSHEET = "WAITING_FOR_WORKSHEET"
    WAITING_FOR_SCHEDULE_A = "WAITING_FOR_SCHEDULE_A"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"


class ExtractionJobStatus(str, Enum):
    QUEUED = "QUEUED"
    SENT_TO_GROUNDX = "SENT_TO_GROUNDX"
    EXTRACTING = "EXTRACTING"
    RAW_EXTRACTION_SAVED = "RAW_EXTRACTION_SAVED"
    MAPPING = "MAPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FieldPriority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    IGNORE = "IGNORE"


class FieldRuleStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    DISABLED = "DISABLED"
    SUPERSEDED = "SUPERSEDED"


class FieldRuleApplicability(str, Enum):
    BOTH = "BOTH"
    EXPERIENCE = "EXPERIENCE"
    NONEXPERIENCE = "NONEXPERIENCE"
    FORM_5500 = "FORM_5500"


class FieldRuleMappingMode(str, Enum):
    FTW_MAPPED = "FTW_MAPPED"
    EXTRACTION_ONLY = "EXTRACTION_ONLY"


class FieldRuleCardinality(str, Enum):
    SCALAR = "SCALAR"
    REPEATING_ROW = "REPEATING_ROW"


class DocumentType(str, Enum):
    SCHEDULE_A = "SCHEDULE_A"
    PLAN_WORKSHEET = "PLAN_WORKSHEET"
    UNKNOWN = "UNKNOWN"


class FormType(str, Enum):
    SCHEDULE_A = "SCHEDULE_A"
    FORM_5500 = "FORM_5500"


class ExtractedFieldStatus(str, Enum):
    MATCHED = "MATCHED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MISSING = "MISSING"
    UNMAPPED = "UNMAPPED"
    IGNORED = "IGNORED"
    EDITED = "EDITED"


class FTWilliamsReviewStatus(str, Enum):
    PREVIEW_READY = "PREVIEW_READY"
    CURRENT_QUERIED = "CURRENT_QUERIED"
    BRING_FORWARD_REQUIRED = "BRING_FORWARD_REQUIRED"
    UPDATE_READY = "UPDATE_READY"
    UPDATE_SENT = "UPDATE_SENT"
    UPDATE_FAILED = "UPDATE_FAILED"
    UPDATE_UNKNOWN = "UPDATE_UNKNOWN"


class FTWilliamsFailureType(str, Enum):
    NEEDS_RETRY = "NEEDS_RETRY"
    NEEDS_DATA_FIX = "NEEDS_DATA_FIX"
    NEEDS_PLAN_MATCH = "NEEDS_PLAN_MATCH"
    NEEDS_SERVICE_CHECK = "NEEDS_SERVICE_CHECK"


class ScheduleAContractType(str, Enum):
    UNKNOWN = "UNKNOWN"
    EXPERIENCE_RATED = "EXPERIENCE_RATED"
    NONEXPERIENCE_RATED = "NONEXPERIENCE_RATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class FTWilliamsPlanYearResolution(str, Enum):
    USE_WORKSHEET = "USE_WORKSHEET"
    KEEP_FTW = "KEEP_FTW"


class FTWilliamsPlanLookupStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    MISSING_IDENTIFIERS = "MISSING_IDENTIFIERS"
    REQUEST_READY = "REQUEST_READY"
    MATCHED = "MATCHED"
    FOUND_NO_FTW_IDS = "FOUND_NO_FTW_IDS"
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"
    NOT_FOUND = "NOT_FOUND"
    FAILED = "FAILED"


class FieldRule(BaseModel):
    id: str | None = None
    key: str
    label: str
    ftw_field: str
    xml_tag: str | None = None
    mapping_mode: FieldRuleMappingMode = FieldRuleMappingMode.FTW_MAPPED
    priority: FieldPriority
    source: str
    form_section: str | None = None
    field_type: str
    existing_or_new: str = "BOTH"
    existing_behavior: str | None = None
    new_behavior: str | None = None
    notes: str | None = None
    client_notes: str | None = None
    aliases: list[str] = Field(default_factory=list)
    cardinality: FieldRuleCardinality = FieldRuleCardinality.SCALAR
    normalization_policy: str | None = None
    validators: list[str] = Field(default_factory=list)
    automatic_update_allowed: bool = True
    required: bool = False
    order: int = 0
    applicability: FieldRuleApplicability = FieldRuleApplicability.BOTH
    status: FieldRuleStatus = FieldRuleStatus.PUBLISHED
    version: int = 1
    updated_by: str | None = None
    change_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FTWFieldCatalogEntry(BaseModel):
    key: str
    label: str
    form_type: FormType
    form_section: str | None = None
    supported_years: list[str] = Field(default_factory=list)
    value_type: str
    format_hint: str
    current_tag: str | None = None
    update_tag: str | None = None
    update_supported: bool = False
    read_only_reason: str | None = None
    contract_version: str
    catalog_tier: str = "VERIFIED"


class FieldRuleDraftRequest(BaseModel):
    rule: FieldRule
    reason: str = ""


class FieldRuleActionRequest(BaseModel):
    reason: str
    version: int | None = None


class FieldRuleTestRequest(BaseModel):
    rule: FieldRule
    sample_field_name: str


class SourceEvidence(BaseModel):
    provider: str | None = None
    page: int | None = None
    source_text: str | None = None
    bounding_box: tuple[float, float, float, float] | None = None
    table_cell: tuple[int, int] | None = None


class ExtractionValidationResult(BaseModel):
    validator: str
    status: str
    reason: str = ""
    normalized_value: str | None = None


class NormalizedExtractionField(BaseModel):
    field_name: str
    value: str = ""
    candidate_values: list[str] = Field(default_factory=list)
    confidence: float = 0
    page: int | None = None
    source_text: str | None = None
    evidence: list[SourceEvidence] = Field(default_factory=list)
    validation_results: list[ExtractionValidationResult] = Field(default_factory=list)
    decision: str = "UNASSESSED"


class ScheduleABrokerMoneyRow(BaseModel):
    coverage: str | None = None
    amount: str = ""
    purpose: str | None = None


class ScheduleABrokerRow(BaseModel):
    name: str = ""
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    organization_code: str | None = None
    purpose: str | None = None
    commission_rows: list[ScheduleABrokerMoneyRow] = Field(default_factory=list)
    fee_rows: list[ScheduleABrokerMoneyRow] = Field(default_factory=list)
    commission_total: str | None = None
    fee_total: str | None = None
    commission_source_text: str | None = None
    fee_source_text: str | None = None
    source_page: int | None = None
    confidence: float = 0.9
    evidence: list[SourceEvidence] = Field(default_factory=list)
    validation_results: list[ExtractionValidationResult] = Field(default_factory=list)
    decision: str = "UNASSESSED"


class ScheduleABrokerMatch(BaseModel):
    extracted_index: int
    ftw_index: int | None = None
    status: str
    resolved: bool = False
    reason: str = ""
    candidate_ftw_indexes: list[int] = Field(default_factory=list)
    current_row: ScheduleABrokerRow | None = None


class ScheduleAWorksheetValue(BaseModel):
    label: str
    value: str
    source: str | None = None
    coverage: str | None = None


class ScheduleABenefitBreakdownRow(BaseModel):
    benefit_type: str
    persons_covered: str | None = None
    premium: str | None = None
    source_page: int | None = None


class ScheduleAWorksheetSummary(BaseModel):
    source: str = ""
    carrier_name: str | None = None
    account_name: str | None = None
    account_number: str | None = None
    period_begin: str | None = None
    period_end: str | None = None
    ein: str | None = None
    naic_code: str | None = None
    coverage: str | None = None
    values: list[ScheduleAWorksheetValue] = Field(default_factory=list)
    benefit_rows: list[ScheduleABenefitBreakdownRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NormalizedExtractionResult(BaseModel):
    provider: str
    fields: list[NormalizedExtractionField]
    raw: dict | list | str | None = None
    classification_signals: list[str] = Field(default_factory=list)
    schedule_a_broker_rows: list[ScheduleABrokerRow] = Field(default_factory=list)
    schedule_a_worksheet_summaries: list[ScheduleAWorksheetSummary] = Field(default_factory=list)


class ExtractedField(BaseModel):
    id: str | None = None
    filing_id: str
    source_field_name: str
    normalized_field_name: str
    mapped_rule_key: str | None = None
    mapped_label: str | None = None
    ftw_field: str | None = None
    xml_tag: str | None = None
    priority: FieldPriority = FieldPriority.LOW
    value: str = ""
    proposed_value: str = ""
    confidence: float = 0
    page: int | None = None
    source_text: str | None = None
    source_document_type: DocumentType | None = None
    form_type: FormType | None = None
    ftw_current_value: str | None = None
    ftw_resolved_tag: str | None = None
    status: ExtractedFieldStatus = ExtractedFieldStatus.MATCHED
    status_reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Filing(BaseModel):
    id: str | None = None
    file_name: str
    content_type: str
    file_size: int
    document_type: DocumentType = DocumentType.SCHEDULE_A
    package_document_count: int = 1
    status: FilingStatus = FilingStatus.UPLOADED
    s3_key: str
    s3_bucket: str | None = None
    storage_path: str | None = None
    package_documents: list[dict] = Field(default_factory=list)
    dashboard_client_name: str | None = None
    dashboard_ein: str | None = None
    dashboard_plan_number: str | None = None
    dashboard_plan_name: str | None = None
    intake_source: str = "MANUAL"
    sharefile_item_id: str | None = None
    sharefile_parent_id: str | None = None
    sharefile_downloaded_at: datetime | None = None
    extraction_provider: str | None = None
    field_rule_set_version: str | None = None
    overall_confidence: float = 0
    missing_high_priority_count: int = 0
    missing_medium_priority_count: int = 0
    missing_low_priority_count: int = 0
    low_confidence_count: int = 0
    unmapped_count: int = 0
    review_field_count: int = 0
    found_field_count: int = 0
    excluded_field_count: int = 0
    schedule_a_contract_type: ScheduleAContractType = ScheduleAContractType.UNKNOWN
    schedule_a_contract_type_reason: str | None = None
    schedule_a_contract_type_confirmed: bool = False
    schedule_a_contract_type_confidence: float = 0
    schedule_a_contract_type_evidence: list[str] = Field(default_factory=list)
    schedule_a_classification_signals: list[str] = Field(default_factory=list)
    ftw_schedule_a_contract_type: ScheduleAContractType = ScheduleAContractType.UNKNOWN
    ftw_schedule_a_contract_type_reason: str | None = None
    schedule_a_broker_rows: list[ScheduleABrokerRow] = Field(default_factory=list)
    schedule_a_worksheet_summaries: list[ScheduleAWorksheetSummary] = Field(default_factory=list)
    proposed_xml: str | None = None
    error_message: str | None = None
    rejection_reason: str | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FilingDetail(Filing):
    fields: list[ExtractedField] = Field(default_factory=list)
    events: list["ReviewEvent"] = Field(default_factory=list)
    jobs: list["ExtractionJob"] = Field(default_factory=list)
    audit_logs: list["AuditLog"] = Field(default_factory=list)
    ftw_review: "FTWilliamsReview | None" = None


class ExtractionJob(BaseModel):
    id: str | None = None
    filing_id: str
    status: ExtractionJobStatus = ExtractionJobStatus.QUEUED
    provider: str = "GroundX"
    attempts: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    next_retry_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RawExtraction(BaseModel):
    id: str | None = None
    filing_id: str
    job_id: str | None = None
    provider: str
    raw: dict | list | str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewEvent(BaseModel):
    id: str | None = None
    filing_id: str
    type: str
    field_id: str | None = None
    before: str | None = None
    after: str | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AuditLog(BaseModel):
    id: str | None = None
    filing_id: str | None = None
    event: str
    message: str
    details: dict | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FieldEditRequest(BaseModel):
    proposed_value: str
    mark_missing: bool = False


class RejectRequest(BaseModel):
    reason: str = ""


class ApproveRequest(BaseModel):
    reason: str = ""
    send_to_ftw: bool = False
    refresh_current_before_update: bool = True
    run_edit_checks: bool = False
    override_blockers: bool = False


class ShareFileStatus(BaseModel):
    configured: bool
    message: str
    subdomain: str | None = None
    intake_folder_id: str | None = None
    configured_folder_ids: list[str] = Field(default_factory=list)
    discover_shared_folders: bool = False
    shared_root_folder_id: str | None = None
    scan_scope: str | None = None
    connected: bool = False


class ShareFileOAuthToken(BaseModel):
    id: str | None = None
    provider: str = "sharefile"
    subdomain: str
    apicp: str = "sf-api.com"
    appcp: str = "sharefile.com"
    token_type: str = "Bearer"
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FTWilliamsQueryRequest(BaseModel):
    operation: str
    customer_id: str | None = None
    plan_id: str | None = None
    year: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    year_end: str | None = None
    ftw_seq_no: str | None = None
    company_employer_id: str | None = None
    plan_number: str | None = None
    company_name: str | None = None
    company_state: str | None = None
    send: bool = False


class FTWilliamsStatusItem(BaseModel):
    type: str | None = None
    error_code: str | None = None
    error_desc: str | None = None
    customer_id: str | None = None
    plan_id: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    ftw_seq_no: str | None = None
    plan_name: str | None = None
    plan_year: str | None = None
    status_success: str | None = None
    successful_fields: list[str] = Field(default_factory=list)
    query_results: dict[str, str] = Field(default_factory=dict)
    query_subparts: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    query_result_record_count: int = Field(default=1, ge=1)


class FTWilliamsQueryResponse(BaseModel):
    operation: str
    configured: bool
    sent: bool
    request_xml: str
    http_status: int | None = None
    success: bool = False
    statuses: list[FTWilliamsStatusItem] = Field(default_factory=list)
    raw_response: str | None = None
    error: str | None = None
    response_headers: dict[str, str] = Field(default_factory=dict)
    elapsed_ms: int | None = None


class FTWilliamsSchemaField(BaseModel):
    var: str
    prompt_text: str | None = None
    required: bool = False
    field_type: str | None = None
    expected_format: str | None = None
    max_length: int | None = None
    allowed_values: list[str] = Field(default_factory=list)
    section: str | None = None
    default_value: str | None = None


class FTWilliamsSchemaSnapshot(BaseModel):
    cache_key: str
    checklist: str
    plan_type: str
    checklist_version: str
    status: str = "FRESH"
    source: str = "FTW_DOC_SCHEMA"
    fields: list[FTWilliamsSchemaField] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    last_error: str | None = None


class FTWilliamsSchemaValidationIssue(BaseModel):
    tag: str
    value: str = ""
    reason: str
    expected_format: str | None = None
    correction: str | None = None


class FTWilliamsSchemaValidationResult(BaseModel):
    valid: bool = True
    mode: str = "OBSERVE"
    schema_source: str
    schema_version: str | None = None
    issues: list[FTWilliamsSchemaValidationIssue] = Field(default_factory=list)
    validated_at: datetime = Field(default_factory=datetime.utcnow)


class FTWilliamsOperationDiagnostic(BaseModel):
    operation: str
    sent: bool = False
    http_status: int | None = None
    outcome_code: str
    response_received: bool = False
    response_content_type: str | None = None
    response_content_length: str | None = None
    request_id: str | None = None
    elapsed_ms: int | None = None
    error_code: str | None = None
    error_description: str | None = None
    response_excerpt: str | None = None


class FTWilliamsEditCheckIssue(BaseModel):
    code: str
    message: str
    status_type: str | None = None
    form_type: str = "FTW"
    schedule_seq_no: str | None = None
    schedule_desc: str | None = None
    field_line: str | None = None
    field_label: str | None = None
    current_value: str | None = None
    correction: str | None = None


class FTWilliamsHistoryItem(BaseModel):
    id: str | None = None
    filing_id: str
    filing_name: str
    filing_status: FilingStatus | None = None
    action: str
    action_label: str
    status: str
    message: str
    company_employer_id: str | None = None
    plan_number: str | None = None
    plan_name: str | None = None
    sponsor_name: str | None = None
    customer_id: str | None = None
    plan_id: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    year: str | None = None
    updated_field_count: int | None = None
    error_message: str | None = None
    created_at: datetime


class FTWilliamsHistoryResponse(BaseModel):
    range: str
    days: int
    items: list[FTWilliamsHistoryItem] = Field(default_factory=list)


class FTWilliamsFailureIssueGroup(BaseModel):
    label: str
    count: int = 1


class FTWilliamsFailureQueueSummary(BaseModel):
    filing_id: str
    filing_name: str
    filing_status: FilingStatus
    review_status: FTWilliamsReviewStatus
    failure_type: FTWilliamsFailureType
    short_reason: str
    next_action: str | None = None
    plan_name: str | None = None
    sponsor_name: str | None = None
    company_employer_id: str | None = None
    plan_number: str | None = None
    customer_id: str | None = None
    plan_id: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    year: str | None = None
    attempted_field_count: int = 0
    issue_count: int = 1
    issue_groups: list[FTWilliamsFailureIssueGroup] = Field(default_factory=list)
    failed_at: datetime
    last_action_label: str = "Update failed"
    error_code: str | None = None
    can_dismiss: bool = True


class FTWilliamsFailureQueueItem(FTWilliamsFailureQueueSummary):
    failure_reason: str
    technical_details: str | None = None
    operation_diagnostics: list[FTWilliamsOperationDiagnostic] = Field(default_factory=list)
    edit_check_issues: list[FTWilliamsEditCheckIssue] = Field(default_factory=list)


class FTWilliamsFailureCounts(BaseModel):
    active: int = 0
    needs_retry: int = 0
    needs_data_fix: int = 0
    needs_plan_match: int = 0
    needs_service_check: int = 0


class FTWilliamsFailureQueueResponse(BaseModel):
    total: int
    page: int = 1
    page_size: int = 10
    total_pages: int = 1
    counts: FTWilliamsFailureCounts = Field(default_factory=FTWilliamsFailureCounts)
    items: list[FTWilliamsFailureQueueSummary] = Field(default_factory=list)


class FTWilliamsFailureNotificationResponse(BaseModel):
    total: int
    counts: FTWilliamsFailureCounts = Field(default_factory=FTWilliamsFailureCounts)
    items: list[FTWilliamsFailureQueueSummary] = Field(default_factory=list)


class FTWilliamsComparisonField(BaseModel):
    field_id: str | None = None
    rule_key: str | None = None
    label: str
    form_type: FormType | None = None
    source_document_type: DocumentType | None = None
    ftw_tag: str | None = None
    current_value: str = ""
    extracted_value: str = ""
    proposed_value: str = ""
    confidence: float = 0
    priority: FieldPriority = FieldPriority.LOW
    extraction_status: ExtractedFieldStatus = ExtractedFieldStatus.MATCHED
    changed: bool = False
    update_included: bool = False
    update_exclusion_reason: str | None = None
    validation_status: str = "VALID"
    validation_message: str | None = None
    validation_expected_format: str | None = None
    validation_normalized_value: str | None = None
    validation_blocking: bool = False


class ClientRejectedField(BaseModel):
    tag: str
    label: str | None = None
    value: str | None = None
    reason: str | None = None
    suggested_value: str | None = None
    form_type: FormType | None = None
    field_id: str | None = None


class FTWilliamsPlanLookup(BaseModel):
    status: FTWilliamsPlanLookupStatus = FTWilliamsPlanLookupStatus.NOT_RUN
    company_employer_id: str | None = None
    plan_number: str | None = None
    year: str | None = None
    plan_name: str | None = None
    sponsor_name: str | None = None
    company_state: str | None = None
    company_name_candidates: list[str] = Field(default_factory=list)
    request_xml: str | None = None
    response_xml: str | None = None
    error_message: str | None = None
    matches: list[dict] = Field(default_factory=list)
    matched_identity: dict | None = None


class ClientFacingError(BaseModel):
    title: str
    message: str
    reason: str | None = None
    next_action: str | None = None
    severity: str = "error"
    source: str = "System"
    code: str | None = None
    technical_details: str | None = None
    rejected_fields: list[ClientRejectedField] = Field(default_factory=list)


class FTWilliamsReview(BaseModel):
    id: str | None = None
    filing_id: str
    status: FTWilliamsReviewStatus = FTWilliamsReviewStatus.PREVIEW_READY
    configured: bool = False
    current_query_sent: bool = False
    current_query_success: bool = False
    current_query_complete: bool | None = None
    current_year_exists: bool = False
    bring_forward_required: bool = False
    ftw_editable: bool | None = None
    ftw_locked_status: str | None = None
    ftw_signed_status: str | None = None
    ftw_filing_status: str | None = None
    ftw_plan_url: str | None = None
    comparison_year: str | None = None
    comparison_year_source: str | None = None
    plan_year_conflict: dict | None = None
    plan_year_resolution: FTWilliamsPlanYearResolution | None = None
    plan_year_resolution_begin: str | None = None
    plan_year_resolution_end: str | None = None
    schedule_a_match: dict | None = None
    schedule_a_candidates: list[dict] = Field(default_factory=list)
    schedule_a_records: list[dict] = Field(default_factory=list)
    schedule_a_broker_rows: list[ScheduleABrokerRow] = Field(default_factory=list)
    schedule_a_broker_matches: list[ScheduleABrokerMatch] = Field(default_factory=list)
    schedule_a_broker_match_complete: bool = True
    schedule_a_worksheet_summaries: list[ScheduleAWorksheetSummary] = Field(default_factory=list)
    schedule_a_contract_type: ScheduleAContractType = ScheduleAContractType.UNKNOWN
    schedule_a_contract_type_reason: str | None = None
    schedule_a_contract_type_confirmed: bool = False
    schedule_a_contract_type_confidence: float = 0
    schedule_a_contract_type_evidence: list[str] = Field(default_factory=list)
    ftw_schedule_a_contract_type: ScheduleAContractType = ScheduleAContractType.UNKNOWN
    ftw_schedule_a_contract_type_reason: str | None = None
    schedule_a_contract_type_mismatch: bool = False
    customer_id: str | None = None
    plan_id: str | None = None
    year: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    ftw_seq_no: str | None = None
    plan_lookup: FTWilliamsPlanLookup | None = None
    query_request_xml: str | None = None
    query_response_xml: str | None = None
    form_5500_current_values: dict[str, str] = Field(default_factory=dict)
    schedule_a_current_values: dict[str, str] = Field(default_factory=dict)
    update_xml_5500: str | None = None
    update_xml_schedule_a: str | None = None
    update_response_xml: str | None = None
    update_verification_attempted: bool = False
    update_verification_success: bool | None = None
    update_verification_mismatches: list[dict] = Field(default_factory=list)
    update_verification_request_xml: str | None = None
    update_verification_response_xml: str | None = None
    schedule_a_restore_attempted: bool = False
    schedule_a_restore_success: bool | None = None
    schedule_a_restore_response_xml: str | None = None
    schedule_a_restore_verification_request_xml: str | None = None
    schedule_a_restore_verification_response_xml: str | None = None
    schedule_a_restore_verification_mismatches: list[dict] = Field(default_factory=list)
    update_attempted_count: int = 0
    update_confirmed_count: int = 0
    update_remaining_count: int = 0
    update_results: list[dict] = Field(default_factory=list)
    update_retry_count: int = 0
    update_diagnostics: list[FTWilliamsOperationDiagnostic] = Field(default_factory=list)
    schema_validation_results: list[FTWilliamsSchemaValidationResult] = Field(default_factory=list)
    schema_validation_blocked: bool = False
    query_access_verified: bool = False
    update_access_status: str = "NOT_ATTEMPTED"
    edit_check_baseline_request_xml: str | None = None
    edit_check_baseline_response_xml: str | None = None
    edit_check_baseline_success: bool | None = None
    edit_check_baseline_issues: list[FTWilliamsEditCheckIssue] = Field(default_factory=list)
    edit_check_request_xml: str | None = None
    edit_check_response_xml: str | None = None
    edit_check_final_success: bool | None = None
    edit_check_final_issues: list[FTWilliamsEditCheckIssue] = Field(default_factory=list)
    edit_check_validation_status: str = "NOT_RUN"
    edit_check_new_issues: list[FTWilliamsEditCheckIssue] = Field(default_factory=list)
    edit_check_resolved_issues: list[FTWilliamsEditCheckIssue] = Field(default_factory=list)
    audit_pdf_status: str = "NOT_REQUESTED"
    audit_pdf_key: str | None = None
    audit_pdf_bucket: str | None = None
    audit_pdf_local_path: str | None = None
    audit_pdf_sha256: str | None = None
    audit_pdf_created_at: datetime | None = None
    audit_pdf_error: str | None = None
    error_message: str | None = None
    client_error: ClientFacingError | None = None
    active_failure: bool = False
    active_failure_reason: str | None = None
    active_failure_client_error: ClientFacingError | None = None
    active_failure_type: FTWilliamsFailureType | None = None
    active_failure_issue_count: int | None = None
    active_failure_issue_groups: list[FTWilliamsFailureIssueGroup] = Field(default_factory=list)
    active_failure_at: datetime | None = None
    failure_dismissed_at: datetime | None = None
    failure_dismissed_reason: str | None = None
    fields: list[FTWilliamsComparisonField] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("client_error", "active_failure_client_error", mode="before")
    @classmethod
    def normalize_legacy_empty_client_error(cls, value):
        # Older reviews may contain an empty embedded document. Treat it as
        # "no friendly error" so those records remain readable in the queue.
        return None if value == {} else value


class FTWilliamsPrepareReviewRequest(BaseModel):
    send_queries: bool = False


class FTWilliamsManualMatchRequest(BaseModel):
    customer_id: str | None = None
    plan_id: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    year: str | None = None


class FTWilliamsScheduleAMatchRequest(BaseModel):
    ftw_seq_no: str | None = None
    carrier: str | None = None
    carrier_ein: str | None = None
    contract: str | None = None
    create_new: bool = False
    schedule_desc: str | None = None


class FTWilliamsBrokerMatchDecision(BaseModel):
    extracted_index: int
    ftw_index: int | None = None
    create_new: bool = False


class FTWilliamsBrokerMatchesRequest(BaseModel):
    decisions: list[FTWilliamsBrokerMatchDecision] = Field(default_factory=list)


class FTWilliamsScheduleABrokerRowsRequest(BaseModel):
    rows: list[ScheduleABrokerRow] = Field(default_factory=list)


class FTWilliamsScheduleAContractTypeRequest(BaseModel):
    contract_type: ScheduleAContractType
    reason: str | None = None


class FTWilliamsPlanYearResolutionRequest(BaseModel):
    resolution: FTWilliamsPlanYearResolution


class FTWilliamsSendUpdateRequest(BaseModel):
    reason: str = ""
    refresh_current_before_update: bool = True
    run_edit_checks: bool = False


class FTWilliamsDismissFailureRequest(BaseModel):
    reason: str = "Dismissed by operator"


class FTWilliamsPlanMapping(BaseModel):
    id: str | None = None
    company_employer_id: str
    plan_number: str
    year: str | None = None
    plan_name: str | None = None
    sponsor_name: str | None = None
    customer_id: str | None = None
    plan_id: str | None = None
    ftw_customer_id: str | None = None
    ftw_plan_id: str | None = None
    source: str = "MANUAL"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
