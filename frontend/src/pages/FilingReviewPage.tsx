import {
  AlertTriangle,
  Ban,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardCheck,
  Edit3,
  Eye,
  ExternalLink,
  FileText,
  ListChecks,
  Lock,
  Plus,
  RefreshCw,
  Search,
  SearchX,
  ShieldCheck,
  Sparkles,
  SlidersHorizontal,
  X,
  XCircle,
} from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "../router";
import { useDialogFocus } from "../ui/useDialogFocus";
import {
  approveFiling,
  getFiling,
  getFTWilliamsBringForwardLink,
  openFTWilliamsAuditPDF,
  prepareFTWilliamsReview,
  regenerateXml,
  reEvaluateFilingRules,
  rejectFiling,
  retryExtraction,
  saveManualFTWilliamsMatch,
  selectFTWilliamsScheduleAMatch,
  setFTWilliamsScheduleABrokerMatches,
  sendApprovedFTWilliamsUpdate,
  unapproveFiling,
  updateField,
} from "../api";
import type { ClientFacingError, ClientRejectedField, ExtractedField, FilingDetail, FTWilliamsComparisonField, FTWilliamsReview, ScheduleABrokerMatch, ScheduleABrokerRow, ScheduleAContractType, ScheduleAWorksheetSummary } from "../types";
import { InlineLoader, Skeleton } from "../ui/Loading";
import { FTWilliamsDiagnostic } from "../ui/FTWilliamsDiagnostic";
import { refreshFTWilliamsFailures } from "../ui/ftWilliamsNotificationStore";
import { formatDate, formatFilingDisplayName, percent } from "../utils";

type ReviewTab = "NEEDS_DECISION" | "WILL_UPDATE" | "SAME" | "MISSING" | "LOW_CONFIDENCE" | "ALL";
type WorkflowStepKey = "INTAKE" | "EXTRACTION" | "FTW_LOADED" | "REVIEW" | "APPROVAL" | "FTW_UPDATE";
type FilterValue = "ALL" | string;
type ContractTypeFilter = "ALL" | ScheduleAContractType;

type ReviewRowGroup = "NEEDS_DECISION" | "WILL_UPDATE" | "SAME" | "MISSING" | "LOW_CONFIDENCE";
type ReviewToast = {
  message: string;
  title: string;
  tone: "error" | "success" | "warning";
} | null;
type FieldSaveOptions = {
  markMissing?: boolean;
  successMessage?: string;
  successTitle?: string;
};
const REVIEW_POLL_MS = 30000;
const EXPERIENCE_SCHEDULE_A_RULES = new Set([
  "schedule_a_part_iii_9a_premiums_1_amount_received",
  "schedule_a_part_iii_9a_2_increase_decrease_in_amount_due_but_unpaid",
  "schedule_a_part_iii_9a_3_increase_decrease_in_unearned_premium_reserve",
  "schedule_a_part_iii_9a_4_earned_1_2_3",
  "schedule_a_part_iii_9b_1_benefit_charges_1_claims_paid",
  "schedule_a_part_iii_9b_2_increase_decrease_in_claim_reserves",
  "schedule_a_part_iii_9b_3_incurred_claims_add_1_and_2",
  "schedule_a_part_iii_9b_4_claims_charged",
  "schedule_a_part_iii_9c_1_a_commissions",
  "schedule_a_part_iii_9c_1_b_administrative_service_or_other_fees",
  "schedule_a_part_iii_9c_1_c_other_specific_acquisition_costs",
  "schedule_a_part_iii_9c_1_d_other_expenses",
  "schedule_a_part_iii_9c_1_e_taxes",
  "schedule_a_part_iii_9c_1_f_charges_for_risks_or_other_contingencies",
  "schedule_a_part_iii_9c_1_g_other_retention_charges",
  "schedule_a_part_iii_9c_1_h_total_retention",
  "schedule_a_part_iii_9c_2_dividends_or_retroactive_rate_refunds",
  "schedule_a_part_iii_9d_1_status_of_policyholder_reserves_at_end_of_year_1_amount_held_to_provide_benefits_after_retirement",
  "schedule_a_part_iii_9d_2_claim_reserves",
  "schedule_a_part_iii_9d_3_other_reserves",
  "schedule_a_part_iii_9e_dividends_or_retroactive_rate_refunds_due",
]);
const NONEXPERIENCE_SCHEDULE_A_RULES = new Set([
  "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier",
]);
const NONEXPERIENCE_DERIVED_ZERO_RULES = new Set([
  "schedule_a_part_iii_9a_4_earned_1_2_3",
  "schedule_a_part_iii_9b_3_incurred_claims_add_1_and_2",
  "schedule_a_part_iii_9c_1_h_total_retention",
]);

interface ReviewDecisionRow {
  key: string;
  fieldId?: string | null;
  label: string;
  formLabel: string;
  section: string;
  sourceLabel: string;
  currentFtw: string;
  extracted: string;
  proposed: string;
  issue: string;
  statusLabel: string;
  group: ReviewRowGroup;
  priority: ExtractedField["priority"];
  confidence: number;
  extractedField?: ExtractedField;
  failedByFtw?: boolean;
  ftwFailureReason?: string;
}

export function FilingReviewPage() {
  const { id } = useParams();
  const [filing, setFiling] = useState<FilingDetail | null>(null);
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState("");
  const [activeTab, setActiveTab] = useState<ReviewTab>("NEEDS_DECISION");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<FilterValue>("ALL");
  const [priorityFilter, setPriorityFilter] = useState<FilterValue>("ALL");
  const [sectionFilter, setSectionFilter] = useState<FilterValue>("ALL");
  const [formFilter, setFormFilter] = useState<FilterValue>("ALL");
  const [contractTypeFilter, setContractTypeFilter] = useState<ContractTypeFilter>("ALL");
  const [reviewPage, setReviewPage] = useState(1);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [pollVersion, setPollVersion] = useState(0);
  const [ftwBusy, setFtwBusy] = useState(false);
  const [ftwSendBusy, setFtwSendBusy] = useState(false);
  const [xmlBusy, setXmlBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [decisionAction, setDecisionAction] = useState<"approve" | "reject" | "unapprove" | null>(null);
  const [fieldSavingId, setFieldSavingId] = useState<string | null>(null);
  const [rulesBusy, setRulesBusy] = useState(false);
  const [toast, setToast] = useState<ReviewToast>(null);
  const [activeWorkflowStep, setActiveWorkflowStep] = useState<WorkflowStepKey | null>(null);
  const [showTechnicalDrawer, setShowTechnicalDrawer] = useState(false);
  const [showExcludedFields, setShowExcludedFields] = useState(false);
  const [showApproveConfirm, setShowApproveConfirm] = useState(false);
  const [showUnapproveConfirm, setShowUnapproveConfirm] = useState(false);
  const previousFilingRef = useRef<FilingDetail | null>(null);
  const bringForwardOpenedRef = useRef(false);
  const ftwSendInFlightRef = useRef(false);
  const pollingPaused = ftwBusy || ftwSendBusy || xmlBusy || retryBusy || rulesBusy || Boolean(decisionAction) || Boolean(fieldSavingId);
  const shouldPollReview = !pollingPaused && isProcessingStatus(filing?.status ?? "UPLOADED");

  useEffect(() => {
    if (!id) return;
    if (pollingPaused) return;
    const filingId = id;
    let active = true;
    let requestInFlight = false;

    async function load({ announceChanges = false }: { announceChanges?: boolean } = {}) {
      if (requestInFlight) return;
      requestInFlight = true;
      try {
        const result = await getFiling(filingId);
        if (!active) return;
        const toastMessage = announceChanges ? reviewChangeToast(previousFilingRef.current, result) : null;
        previousFilingRef.current = result;
        setFiling(result);
        if (toastMessage) setToast(toastMessage);
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : "Could not load filing");
      } finally {
        requestInFlight = false;
      }
    }

    load();
    const interval = shouldPollReview
      ? window.setInterval(() => load({ announceChanges: true }), REVIEW_POLL_MS)
      : undefined;
    return () => {
      active = false;
      if (interval) window.clearInterval(interval);
    };
  }, [id, pollVersion, pollingPaused, shouldPollReview]);

  const fields = filing?.fields ?? [];
  const ftwReview = filing?.ftw_review || null;
  const scheduleAContractType = ftwReview?.schedule_a_contract_type || filing?.schedule_a_contract_type || "UNKNOWN";
  const scheduleABrokerRows = ftwReview?.schedule_a_broker_rows?.length ? ftwReview.schedule_a_broker_rows : filing?.schedule_a_broker_rows || [];
  const scheduleABrokerMatches = ftwReview?.schedule_a_broker_matches || [];
  const scheduleAWorksheetSummaries = ftwReview?.schedule_a_worksheet_summaries?.length ? ftwReview.schedule_a_worksheet_summaries : filing?.schedule_a_worksheet_summaries || [];
  const approvalRelevantFields = fields.filter((field) => fieldAllowedForContractType(field, scheduleAContractType));
  const excludedFields = fields.filter((field) => !fieldAllowedForContractType(field, scheduleAContractType));
  const missingHigh = approvalRelevantFields.filter((field) => field.priority === "HIGH" && field.status === "MISSING");
  const missingOther = approvalRelevantFields.filter((field) => field.status === "MISSING" && field.priority !== "HIGH");
  const lowConfidence = approvalRelevantFields.filter((field) => field.status === "LOW_CONFIDENCE");
  const unmapped = approvalRelevantFields.filter((field) => field.status === "UNMAPPED");
  const extracted = approvalRelevantFields.filter((field) => hasValue(field) && field.status !== "UNMAPPED");
  const matched = approvalRelevantFields.filter((field) => field.status === "MATCHED" || field.status === "EDITED");
  const actionFields = useMemo(
    () => [...missingHigh, ...lowConfidence, ...unmapped, ...missingOther].sort(compareFields),
    [missingHigh, lowConfidence, unmapped, missingOther],
  );
  const reviewRows = useMemo(
    () => buildReviewDecisionRows(fields, filing?.ftw_review || null, scheduleAContractType, false),
    [fields, filing?.ftw_review, scheduleAContractType],
  );
  const visibleReviewRows = useMemo(
    () => showExcludedFields
      ? buildReviewDecisionRows(fields, filing?.ftw_review || null, scheduleAContractType, true)
      : reviewRows,
    [fields, filing?.ftw_review, reviewRows, scheduleAContractType, showExcludedFields],
  );
  const sectionOptions = useMemo(() => [...new Set(visibleReviewRows.map((row) => row.section))].sort(), [visibleReviewRows]);
  const needsDecisionRows = reviewRows.filter((row) => row.group === "NEEDS_DECISION" && isActionRequiredRow(row));
  const willUpdateRows = reviewRows.filter((row) => row.group === "WILL_UPDATE");
  const sameRows = reviewRows.filter((row) => row.group === "SAME");
  const missingRows = reviewRows.filter((row) => row.group === "MISSING");
  const lowConfidenceRows = reviewRows.filter((row) => row.group === "LOW_CONFIDENCE");
  const actionRequiredRows = reviewRows.filter(isActionRequiredRow);
  const approvalBlockerRows = actionRequiredRows;
  const displayRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return visibleReviewRows.filter((row) => {
      const haystack = [
        row.label,
        row.formLabel,
        row.sourceLabel,
        row.currentFtw,
        row.extracted,
        row.proposed,
        row.issue,
        row.statusLabel,
        row.section,
      ].join(" ").toLowerCase();
      return (
        (!needle || haystack.includes(needle)) &&
        (activeTab === "ALL" || (activeTab === "NEEDS_DECISION" ? isActionRequiredRow(row) : row.group === activeTab)) &&
        (statusFilter === "ALL" || row.group === statusFilter) &&
        (priorityFilter === "ALL" || row.priority === priorityFilter) &&
        (sectionFilter === "ALL" || row.section === sectionFilter) &&
        (formFilter === "ALL" || row.formLabel.toUpperCase().replace(" ", "_") === formFilter) &&
        (contractTypeFilter === "ALL" || scheduleAContractType === contractTypeFilter)
      );
    });
  }, [activeTab, contractTypeFilter, formFilter, priorityFilter, visibleReviewRows, scheduleAContractType, search, sectionFilter, statusFilter]);
  const reviewRowsPerPage = 8;
  const reviewTotalPages = Math.max(1, Math.ceil(displayRows.length / reviewRowsPerPage));
  const paginatedDisplayRows = displayRows.slice((reviewPage - 1) * reviewRowsPerPage, reviewPage * reviewRowsPerPage);

  useEffect(() => {
    setReviewPage(1);
  }, [activeTab, contractTypeFilter, formFilter, priorityFilter, search, sectionFilter, showExcludedFields, statusFilter]);

  useEffect(() => {
    setReviewPage((current) => Math.min(current, reviewTotalPages));
  }, [reviewTotalPages]);
  const selectedField = useMemo(
    () => selectedFieldId ? fields.find((field) => field.id === selectedFieldId) : undefined,
    [fields, selectedFieldId],
  );
  const approvalBlocked = missingHigh.length > 0 || unmapped.length > 0;
  const expectsForm5500Current = expectsCurrentForForm(approvalRelevantFields, reviewRows, "FORM_5500");
  const expectsScheduleACurrent = expectsCurrentForForm(approvalRelevantFields, reviewRows, "SCHEDULE_A");
  const form5500CurrentLoaded = hasLoadedCurrentForForm(ftwReview, "FORM_5500");
  const scheduleACurrentLoaded = hasLoadedCurrentForForm(ftwReview, "SCHEDULE_A");
  const scheduleAIsNew = Boolean(ftwReview?.schedule_a_match?.create_new);
  const scheduleASafetyReady = !expectsScheduleACurrent || scheduleACurrentLoaded || (scheduleAIsNew && Boolean(ftwReview?.schedule_a_records?.length));
  const scheduleABrokersReady = !scheduleABrokerRows.length || ftwReview?.schedule_a_broker_match_complete !== false;
  const form5500SafetyReady = !expectsForm5500Current || form5500CurrentLoaded;
  const bringForwardRequired = Boolean(ftwReview?.bring_forward_required);
  const ftwCurrentLoaded = Boolean(
    ftwReview?.configured &&
    ftwReview.current_query_success &&
    ftwReview.current_query_complete !== false &&
    ftwReview.current_year_exists &&
    !bringForwardRequired &&
    (form5500CurrentLoaded || scheduleACurrentLoaded),
  );
  const ftwUpdateFailed = Boolean(ftwReview?.active_failure || ftwReview?.status === "UPDATE_FAILED");
  const ftwUpdateUnknown = ftwReview?.status === "UPDATE_UNKNOWN";
  const autoFtwQueryBusy = filing?.status === "QUERYING_FTW_CURRENT";
  const ftwInteractionBusy = ftwBusy || autoFtwQueryBusy;
  const decisionBusy = Boolean(decisionAction);
  const reviewInteractionBusy = ftwInteractionBusy || xmlBusy || retryBusy || decisionBusy || Boolean(fieldSavingId);
  const showFtwSendAction = filing?.status === "APPROVED" || (filing?.status === "FAILED" && (ftwUpdateFailed || ftwUpdateUnknown));
  const ftwReadyToSend = Boolean(
    showFtwSendAction &&
    ftwReview?.configured &&
    ftwReview.current_query_success &&
    ftwReview.current_query_complete !== false &&
    ftwReview.ftw_editable !== false &&
    form5500SafetyReady &&
    scheduleASafetyReady &&
    scheduleABrokersReady,
  );
  const foundCount = extracted.length;
  const totalFields = approvalRelevantFields.filter((field) => field.priority !== "IGNORE").length;
  const displayFileName = formatFilingDisplayName(filing?.file_name || "");
  const isProcessing = isProcessingStatus(filing?.status ?? "UPLOADED");
  const retryingFailedFtwUpdate = filing?.status === "FAILED" && (ftwUpdateFailed || ftwUpdateUnknown) && ftwReadyToSend;
  const scheduleMatch = formatScheduleAMatch(filing?.ftw_review?.schedule_a_match);
  const lookup = filing?.ftw_review?.plan_lookup || null;
  const clientError = filing?.ftw_review?.active_failure_client_error || filing?.ftw_review?.client_error || null;
  const filingClientError = filing?.status === "FAILED" && filing.error_message ? clientErrorFromRaw(filing.error_message, "Processing") : null;
  const ftwFailed = !bringForwardRequired && Boolean(clientError || ftwUpdateFailed || ftwUpdateUnknown);
  const scheduleCandidates = ftwReview?.schedule_a_candidates || [];
  const scheduleSelectionRequired = scheduleCandidates.length > 0 && !ftwReview?.schedule_a_match;
  const approvalReady = !isProcessing && !scheduleSelectionRequired && !retryingFailedFtwUpdate;

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 6500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function saveField(fieldId: string, proposedValue: string, options: FieldSaveOptions = {}) {
    if (!id || !filing || fieldSavingId) return false;
    setFieldSavingId(fieldId);
    setToast(null);
    try {
      const result = await updateField(id, fieldId, proposedValue, { markMissing: options.markMissing });
      setFiling((current) => current ? {
        ...current,
        ftw_review: mergeFieldDecisionReview(current.ftw_review, result.ftw_review, fieldId),
        proposed_xml: result.proposed_xml,
        fields: current.fields.map((field) => field.id === fieldId ? result.field : field),
      } : current);
      setToast({
        tone: "success",
        title: options.successTitle || (options.markMissing ? "Field marked missing" : "Field decision saved"),
        message: options.successMessage || (options.markMissing
          ? "This field will remain excluded until a value is entered."
          : "The proposed FT Williams value has been updated."),
      });
      return true;
    } catch (error) {
      setToast({
        tone: "error",
        title: "Field decision was not saved",
        message: error instanceof Error ? error.message : "Please try again.",
      });
      return false;
    } finally {
      setFieldSavingId(null);
    }
  }

  async function decide(action: "approve" | "reject", options?: { overrideBlockers?: boolean }) {
    if (!id) return;
    if (action === "approve") {
      await approveFiling(id, reason, { override_blockers: Boolean(options?.overrideBlockers) });
    }
    else await rejectFiling(id, reason);
    setFiling(await getFiling(id));
  }

  async function approveAnyway() {
    setShowApproveConfirm(false);
    setDecisionAction("approve");
    setToast(null);
    try {
      await decide("approve", { overrideBlockers: approvalBlocked });
      setToast({
        tone: "success",
        title: "Filing approved",
        message: `${displayFileName} is approved and ready for the remaining FT Williams safety checks.`,
      });
    } catch (error) {
      setToast({
        tone: "error",
        title: "Could not approve filing",
        message: error instanceof Error ? error.message : "The filing could not be approved.",
      });
    } finally {
      setDecisionAction(null);
    }
  }

  function handleApproveClick() {
    if (filing?.status === "APPROVED") {
      setShowUnapproveConfirm(true);
      return;
    }
    setMessage("");
    setShowApproveConfirm(true);
  }

  async function confirmUnapprove() {
    if (!id) return;
    setShowUnapproveConfirm(false);
    setDecisionAction("unapprove");
    try {
      await unapproveFiling(id);
      const updated = await getFiling(id);
      setFiling(updated);
      previousFilingRef.current = updated;
      setToast({
        tone: "success",
        title: "Approval removed",
        message: `${formatFilingDisplayName(updated.file_name)} is no longer approved. FT Williams sending is locked until approval is restored.`,
      });
    } catch (error) {
      setToast({
        tone: "error",
        title: "Could not remove approval",
        message: error instanceof Error ? error.message : "The filing approval could not be removed.",
      });
    } finally {
      setDecisionAction(null);
    }
  }

  async function rejectDecision() {
    setDecisionAction("reject");
    setToast(null);
    try {
      await decide("reject");
      setToast({
        tone: "success",
        title: "Filing rejected",
        message: `${displayFileName} was returned for correction.`,
      });
    } catch (error) {
      setToast({
        tone: "error",
        title: "Could not reject filing",
        message: error instanceof Error ? error.message : "The filing could not be rejected.",
      });
    } finally {
      setDecisionAction(null);
    }
  }

  function reviewBlockingFields() {
    setShowApproveConfirm(false);
    setActiveTab("NEEDS_DECISION");
    setStatusFilter("ALL");
    setPriorityFilter("ALL");
    setSectionFilter("ALL");
    setFormFilter("ALL");
    setContractTypeFilter("ALL");
    window.requestAnimationFrame(() => document.getElementById("filing-review-table")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function editAllFields() {
    setActiveTab("ALL");
    resetFilters();
    window.requestAnimationFrame(() => document.getElementById("filing-review-table")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  // Auto-refresh FTW data when the user returns from the FT Williams tab after
  // opening Bring Forward, instead of requiring a manual "Refresh FTW Data" click.
  useEffect(() => {
    function handleReturnFromFtw() {
      if (document.visibilityState !== "visible") return;
      if (!bringForwardOpenedRef.current || ftwBusy) return;
      bringForwardOpenedRef.current = false;
      setToast({
        tone: "success",
        title: "Refreshing FT Williams data",
        message: "Checking whether the current-year record now exists after Bring Forward.",
      });
      void prepareFtw(true);
    }
    document.addEventListener("visibilitychange", handleReturnFromFtw);
    window.addEventListener("focus", handleReturnFromFtw);
    return () => {
      document.removeEventListener("visibilitychange", handleReturnFromFtw);
      window.removeEventListener("focus", handleReturnFromFtw);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ftwBusy, id]);

  async function prepareFtw(sendQueries: boolean) {
    if (!id) return;
    setFtwBusy(true);
    setMessage("");
    try {
      const result = await prepareFTWilliamsReview(id, sendQueries);
      const updated = await getFiling(id);
      setFiling(updated);
      previousFilingRef.current = updated;
      if (sendQueries && result.ftw_review.current_year_exists && !result.ftw_review.bring_forward_required) {
        setToast({
          tone: "success",
          title: "Current-year FTW data loaded",
          message: `FT Williams ${result.ftw_review.year || "current-year"} data is ready for comparison.`,
        });
      } else if (sendQueries && result.ftw_review.bring_forward_required) {
        setToast({
          tone: "error",
          title: "Bring Forward still required",
          message: "The current-year FTW record is still missing. Complete Bring Forward in FT Williams, then refresh again.",
        });
      }
    } catch (error) {
      setToast({
        tone: "error",
        title: "FT Williams refresh needs attention",
        message: error instanceof Error ? error.message : "Current FT Williams data could not be refreshed.",
      });
    } finally {
      setFtwBusy(false);
    }
  }

  async function openFtwBringForward() {
    if (!id) return;
    const ftwWindow = window.open("about:blank", "_blank");
    if (ftwWindow) ftwWindow.opener = null;
    setFtwBusy(true);
    setMessage("");
    try {
      const result = await getFTWilliamsBringForwardLink(id);
      if (ftwWindow) ftwWindow.location.href = result.url;
      else window.open(result.url, "_blank", "noopener,noreferrer");
      bringForwardOpenedRef.current = true;
      setToast({
        tone: "success",
        title: "FT Williams opened",
        message: "Complete FTW's native Bring Forward action and return here - FTW data will refresh automatically.",
      });
    } catch (error) {
      ftwWindow?.close();
      setToast({
        tone: "error",
        title: "Could not open FT Williams",
        message: error instanceof Error ? error.message : "The FT Williams page could not be opened.",
      });
    } finally {
      setFtwBusy(false);
    }
  }

  async function saveFtwManualMatch(payload: {
    customer_id?: string;
    plan_id?: string;
    ftw_customer_id?: string;
    ftw_plan_id?: string;
    year?: string;
  }) {
    if (!id) return;
    setFtwBusy(true);
    setMessage("");
    try {
      await saveManualFTWilliamsMatch(id, payload);
      setFiling(await getFiling(id));
    } catch (error) {
      setToast({
        tone: "error",
        title: "FT Williams match was not saved",
        message: error instanceof Error ? error.message : "Check the plan identifiers and try again.",
      });
    } finally {
      setFtwBusy(false);
    }
  }

  async function selectFtwScheduleMatch(payload: {
    ftw_seq_no?: string;
    carrier?: string;
    carrier_ein?: string;
    contract?: string;
    create_new?: boolean;
    schedule_desc?: string;
  }) {
    if (!id) return;
    setFtwBusy(true);
    setMessage("");
    try {
      await selectFTWilliamsScheduleAMatch(id, payload);
      setFiling(await getFiling(id));
    } catch (error) {
      setToast({
        tone: "error",
        title: "Schedule A was not selected",
        message: error instanceof Error ? error.message : "Check the FT Williams Schedule A and try again.",
      });
    } finally {
      setFtwBusy(false);
    }
  }

  async function saveScheduleABrokerMatch(extractedIndex: number, ftwIndex?: number, createNew = false) {
    if (!id || !ftwReview) return;
    setFtwBusy(true);
    setToast(null);
    try {
      const decisions = (ftwReview.schedule_a_broker_matches || [])
        .filter((match) => match.status === "CONFIRMED" || match.status === "CONFIRMED_NEW")
        .filter((match) => match.extracted_index !== extractedIndex)
        .map((match) => ({
          extracted_index: match.extracted_index,
          ftw_index: match.ftw_index ?? undefined,
          create_new: match.status === "CONFIRMED_NEW",
        }));
      decisions.push({ extracted_index: extractedIndex, ftw_index: ftwIndex, create_new: createNew });
      await setFTWilliamsScheduleABrokerMatches(id, decisions);
      const updated = await getFiling(id);
      setFiling(updated);
      previousFilingRef.current = updated;
      setToast({
        tone: "success",
        title: "Broker match saved",
        message: createNew ? "The broker will be added as a new FT Williams row." : "The broker is linked to the selected FT Williams row.",
      });
    } catch (error) {
      setToast({
        tone: "error",
        title: "Broker match was not saved",
        message: error instanceof Error ? error.message : "Select a different FT Williams broker row and try again.",
      });
    } finally {
      setFtwBusy(false);
    }
  }

  async function sendFtwUpdate() {
    if (!id || ftwSendInFlightRef.current) return;
    ftwSendInFlightRef.current = true;
    setFtwBusy(true);
    setFtwSendBusy(true);
    setMessage("");
    setToast(null);
    try {
      const sendResult = await sendApprovedFTWilliamsUpdate(id, {
        reason,
        refresh_current_before_update: true,
        run_edit_checks: true,
      });
      const sentReview = sendResult.ftw_review;
      const updatedFiling = await getFiling(id);
      setFiling(updatedFiling);
      if (!sentReview || sentReview.status !== "UPDATE_SENT") {
        const confirmed = sentReview?.update_confirmed_count || 0;
        const remaining = sentReview?.update_remaining_count || 0;
        setToast({
          tone: confirmed ? "warning" : "error",
          title: confirmed ? "FT Williams partially updated" : "FT Williams needs attention",
          message: confirmed
            ? `${confirmed} field${confirmed === 1 ? "" : "s"} updated — ${remaining} need${remaining === 1 ? "s" : ""} review.`
            : sentReview?.active_failure_client_error?.message || sentReview?.client_error?.message || "The latest FT Williams values were refreshed. Review the remaining field issue below.",
        });
        return;
      }
      const updateCount = sentReview.update_confirmed_count || sentReview.update_attempted_count || 0;
      const validationOutcome = ftwEditCheckOutcome(sentReview);
      setToast({
        tone: validationOutcome?.tone === "attention" ? "warning" : "success",
        title: "FT Williams updated successfully",
        message: validationOutcome?.summary || (updateCount
          ? `${updateCount} field${updateCount === 1 ? "" : "s"} verified. Current FTW values are now refreshed.`
          : "Current FTW values were refreshed and verified successfully."),
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Could not send approved FT Williams update";
      setToast({
        tone: "error",
        title: "FT Williams needs attention",
        message: errorMessage,
      });
    } finally {
      void refreshFTWilliamsFailures().catch(() => undefined);
      ftwSendInFlightRef.current = false;
      setFtwBusy(false);
      setFtwSendBusy(false);
    }
  }

  async function viewFtwAuditPdf() {
    if (!id) return;
    try {
      await openFTWilliamsAuditPDF(id);
    } catch (error) {
      setToast({
        tone: "error",
        title: "Verified PDF is unavailable",
        message: error instanceof Error ? error.message : "Generate the FT Williams evidence again.",
      });
    }
  }

  async function rebuildXml() {
    if (!id || !filing) return;
    setXmlBusy(true);
    try {
      const result = await regenerateXml(id);
      setFiling({ ...filing, proposed_xml: result.proposed_xml });
    } catch (error) {
      setToast({ tone: "error", title: "XML preview failed", message: error instanceof Error ? error.message : "The XML preview could not be generated." });
    } finally {
      setXmlBusy(false);
    }
  }

  async function retryFailedExtraction() {
    if (!id) return;
    setRetryBusy(true);
    try {
      await retryExtraction(id);
      setFiling(await getFiling(id));
      setPollVersion((value) => value + 1);
    } catch (error) {
      setToast({ tone: "error", title: "Extraction retry failed", message: error instanceof Error ? error.message : "Extraction could not be restarted." });
    } finally {
      setRetryBusy(false);
    }
  }

  async function reEvaluateWithLatestRules() {
    if (!id) return;
    setRulesBusy(true);
    setMessage("");
    setToast(null);
    try {
      const result = await reEvaluateFilingRules(id);
      setFiling(await getFiling(id));
      setToast({
        tone: "success",
        title: "Rules re-evaluated",
        message: `${result.field_count} fields were remapped with rule set ${result.field_rule_set_version}. EyeLevel was not run again.`,
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : "Rules could not be re-evaluated.";
      setToast({ tone: "error", title: "Re-evaluation failed", message: errorMessage });
    } finally {
      setRulesBusy(false);
    }
  }

  function resetFilters() {
    setSearch("");
    setStatusFilter("ALL");
    setPriorityFilter("ALL");
    setSectionFilter("ALL");
    setFormFilter("ALL");
    setContractTypeFilter("ALL");
  }

  async function setProposedValue(row: ReviewDecisionRow, value: string, options: FieldSaveOptions = {}) {
    if (!row.fieldId) return;
    await saveField(row.fieldId, value, options);
  }

  if (message && !filing) return <div className="card card-pad">{message}</div>;
  if (!filing) return <FilingReviewSkeleton />;

  return (
    <div className="review-page approval-workspace-page">
      {toast ? <ReviewToastMessage toast={toast} onClose={() => setToast(null)} /> : null}

      <main className="approval-workspace">
        <WorkflowStepper
          filing={filing}
          ftwReadyToSend={ftwReadyToSend}
          needsDecisionCount={actionRequiredRows.length}
          onStepSelect={setActiveWorkflowStep}
        />

        {isProcessing && !fields.length ? (
          <ProcessingPanel filing={filing} />
        ) : filing.status === "FAILED" && !fields.length ? (
          <ExtractionFailurePanel busy={retryBusy} onRetry={retryFailedExtraction} />
        ) : (
          <section className="approval-decision-table-shell approval-preview-shell" id="filing-review-table">
            <div className="approval-table-head compact-review-header">
              <div className="compact-review-file">
                <strong>{displayFileName}</strong>
              </div>
              <div className="compact-review-meta" aria-label="Filing review summary">
                <span><small>Fields found</small><strong>{foundCount} / {totalFields || 61}</strong></span>
                <span><small>Needs review</small><strong>{actionRequiredRows.length}</strong></span>
                <span><small>FTW match</small><strong>{lookup?.status === "MATCHED" || filing.ftw_review?.customer_id ? "Matched" : "Pending"}</strong></span>
              </div>
              <div className="compact-review-toolbar">
              <ReviewPrimaryActions
                approvalBlocked={approvalBlocked}
                approvalReady={approvalReady}
                bringForwardRequired={bringForwardRequired}
                busy={reviewInteractionBusy}
                decisionAction={decisionAction}
                filingStatus={filing.status}
                showFtwSendAction={showFtwSendAction}
                ftwSendBusy={ftwSendBusy}
                queryBusy={ftwInteractionBusy}
                retryBusy={retryBusy}
                rulesBusy={rulesBusy}
                xmlBusy={xmlBusy}
                onApprove={handleApproveClick}
                onOpenBringForward={openFtwBringForward}
                onPreviewXml={rebuildXml}
                onQuery={() => prepareFtw(true)}
                onReEvaluate={reEvaluateWithLatestRules}
                onReject={rejectDecision}
                onRetryExtraction={retryFailedExtraction}
                onSend={sendFtwUpdate}
                onOpenTechnical={() => setShowTechnicalDrawer(true)}
              />
                <button className="button secondary table-filter-button" onClick={resetFilters}><SlidersHorizontal size={14} /> Reset</button>
              </div>
            </div>

            <div className="approval-count-tabs">
              <ReviewCountTab active={activeTab === "NEEDS_DECISION"} icon={<AlertTriangle size={15} />} label="Action Required" count={actionRequiredRows.length} onClick={() => setActiveTab("NEEDS_DECISION")} />
              <ReviewCountTab active={activeTab === "WILL_UPDATE"} label="Will Update FTW" count={willUpdateRows.length} onClick={() => setActiveTab("WILL_UPDATE")} />
              <ReviewCountTab active={activeTab === "ALL"} icon={<ListChecks size={15} />} label="All Fields" count={reviewRows.length || totalFields} onClick={() => setActiveTab("ALL")} />
            </div>

            <div className="field-filter-row approval-filter-row">
              <SelectFilter label="Form" value={formFilter} onChange={setFormFilter} options={["SCHEDULE_A", "FORM_5500"]} />
              <SelectFilter label="Contract" value={contractTypeFilter} onChange={(value) => setContractTypeFilter(value as ContractTypeFilter)} options={["EXPERIENCE_RATED", "NONEXPERIENCE_RATED", "NEEDS_REVIEW", "UNKNOWN"]} />
              <SelectFilter label="Status" value={statusFilter} onChange={setStatusFilter} options={["NEEDS_DECISION", "WILL_UPDATE", "SAME", "MISSING", "LOW_CONFIDENCE"]} />
              <SelectFilter label="Priority" value={priorityFilter} onChange={setPriorityFilter} options={["HIGH", "MEDIUM", "LOW"]} />
              <SelectFilter label="Section" value={sectionFilter} onChange={setSectionFilter} options={sectionOptions} />
              {excludedFields.length ? (
                <button className="button secondary" type="button" onClick={() => setShowExcludedFields((current) => !current)}>
                  <Eye size={16} /> {showExcludedFields ? "Hide" : "Show"} excluded fields ({excludedFields.length})
                </button>
              ) : null}
              <label className="table-search compact-table-search">
                <Search size={14} />
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search fields..." />
              </label>
            </div>

            <div className="approval-table-wrap">
              <table className="approval-decision-table">
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Extracted</th>
                    <th>Current FTW</th>
                    <th>Proposed To Send</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {paginatedDisplayRows.map((row) => (
                    <ReviewDecisionTableRow
                      key={row.key}
                      row={row}
                      selected={row.fieldId === selectedFieldId}
                      onInspect={() => row.fieldId ? setSelectedFieldId(row.fieldId) : undefined}
                      onAccept={() => setProposedValue(row, row.extracted, {
                        successTitle: "Extracted value selected",
                        successMessage: `${row.label} is ready for FT Williams review.`,
                      })}
                      onKeepFtw={() => setProposedValue(row, row.currentFtw, {
                        successTitle: "Current FT Williams value kept",
                        successMessage: `${row.label} will keep its current FT Williams value.`,
                      })}
                      disabled={reviewInteractionBusy}
                      saving={fieldSavingId === row.fieldId}
                    />
                  ))}
                </tbody>
              </table>
            </div>

            {!displayRows.length ? (
              <div className="empty-state"><SearchX size={18} /> No fields match this view.</div>
            ) : null}

            <div className="approval-preview-footer">
              <span>
                Showing {displayRows.length ? (reviewPage - 1) * reviewRowsPerPage + 1 : 0}
                -{Math.min(reviewPage * reviewRowsPerPage, displayRows.length)} of {displayRows.length} matching field{displayRows.length === 1 ? "" : "s"}
              </span>
              {reviewTotalPages > 1 ? (
                <nav className="compact-table-pagination" aria-label="Review table pages">
                  <button type="button" disabled={reviewPage === 1} onClick={() => setReviewPage((page) => Math.max(1, page - 1))} aria-label="Previous page">‹</button>
                  {Array.from({ length: reviewTotalPages }, (_, index) => index + 1).map((page) => (
                    <button key={page} type="button" className={page === reviewPage ? "active" : ""} onClick={() => setReviewPage(page)} aria-label={`Page ${page}`}>{page}</button>
                  ))}
                  <button type="button" disabled={reviewPage === reviewTotalPages} onClick={() => setReviewPage((page) => Math.min(reviewTotalPages, page + 1))} aria-label="Next page">›</button>
                </nav>
              ) : null}
            </div>

            <ScheduleABrokerRowsPanel
              busy={ftwBusy}
              matches={scheduleABrokerMatches}
              onConfirm={saveScheduleABrokerMatch}
              rows={scheduleABrokerRows}
            />
          </section>
        )}

      </main>

      {selectedField ? (
        <FieldReviewModal
          field={selectedField}
          onClose={() => setSelectedFieldId(null)}
          onSave={saveField}
          saving={fieldSavingId === selectedField.id}
        />
      ) : null}

      {showApproveConfirm ? (
        <ApproveConfirmationModal
          blockers={{
            highPriorityMissing: missingHigh.length,
            lowConfidence: lowConfidence.length,
            needsDecision: needsDecisionRows.length,
            unmapped: unmapped.length,
            willKeepFtw: sameRows.length,
            willUpdate: willUpdateRows.length,
          }}
          hasBlockers={actionRequiredRows.length > 0}
          unresolvedRows={approvalBlockerRows}
          onApprove={approveAnyway}
          onClose={() => setShowApproveConfirm(false)}
          onReviewFields={reviewBlockingFields}
        />
      ) : null}

      {showUnapproveConfirm ? (
        <UnapproveConfirmationModal
          filingName={displayFileName}
          onClose={() => setShowUnapproveConfirm(false)}
          onConfirm={confirmUnapprove}
        />
      ) : null}

      {activeWorkflowStep ? (
        <WorkflowDetailDialog
          actionRequiredCount={actionRequiredRows.length}
          approvalBlocked={approvalBlocked}
          approvalReady={approvalReady}
          busy={reviewInteractionBusy}
          filing={filing}
          foundCount={foundCount}
          ftwCurrentLoaded={ftwCurrentLoaded}
          ftwReadyToSend={ftwReadyToSend}
          onClose={() => setActiveWorkflowStep(null)}
          onApprove={() => {
            setActiveWorkflowStep(null);
            handleApproveClick();
          }}
          onOpenBringForward={openFtwBringForward}
          onQuery={() => prepareFtw(true)}
          onViewAuditPDF={viewFtwAuditPdf}
          onSelectSchedule={selectFtwScheduleMatch}
          onShowTab={(tab) => {
            setActiveTab(tab);
            setActiveWorkflowStep(null);
            window.requestAnimationFrame(() => document.getElementById("filing-review-table")?.scrollIntoView({ behavior: "smooth", block: "start" }));
          }}
          scheduleCandidates={scheduleCandidates}
          step={activeWorkflowStep}
          totalFields={totalFields}
          willUpdateCount={willUpdateRows.length}
        />
      ) : null}

      {showTechnicalDrawer ? (
        <TechnicalReviewDrawer
          busy={reviewInteractionBusy}
          canSendUpdate={ftwReadyToSend}
          filing={filing}
          onClose={() => setShowTechnicalDrawer(false)}
          onPreparePreview={() => prepareFtw(false)}
          onPreviewXml={rebuildXml}
          onQueryCurrent={() => prepareFtw(true)}
          onReEvaluate={reEvaluateWithLatestRules}
          onRetryExtraction={retryFailedExtraction}
          onSaveManualMatch={saveFtwManualMatch}
          onSelectScheduleMatch={selectFtwScheduleMatch}
          onSendUpdate={sendFtwUpdate}
          sendBusy={ftwSendBusy}
        />
      ) : null}
    </div>
  );
}

function ReviewStat({ icon, label, value, detail, tone }: { icon: ReactNode; label: string; value: string | number; detail: string; tone?: "ready" | "danger" | "warn" | "info" }) {
  return (
    <div className={`review-stat ${tone ? `review-stat-${tone}` : ""}`}>
      <span className="review-stat-icon">{icon}</span>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

function ReviewToastMessage({ onClose, toast }: { onClose: () => void; toast: NonNullable<ReviewToast> }) {
  const Icon = toast.tone === "success" ? CheckCircle2 : AlertTriangle;
  return (
    <div className={`review-toast toast-${toast.tone}`} role="status" aria-live="polite">
      <Icon size={18} />
      <span>
        <strong>{toast.title}</strong>
        <small>{toast.message}</small>
      </span>
      <button type="button" onClick={onClose} aria-label="Dismiss notification">
        <X size={15} />
      </button>
    </div>
  );
}

function reviewChangeToast(previous: FilingDetail | null, next: FilingDetail): ReviewToast {
  if (!previous) return null;
  if (reviewChangeSignature(previous) === reviewChangeSignature(next)) return null;
  const displayName = formatFilingDisplayName(next.file_name);
  if (!previous.ftw_review?.current_query_success && next.ftw_review?.current_query_success) {
    return {
      tone: "success",
      title: "FT Williams data loaded",
      message: `Current Form 5500 and Schedule A values are ready for ${displayName}.`,
    };
  }
  if (previous.status !== next.status) {
    if (next.status === "QUERYING_FTW_CURRENT" || next.status === "EXTRACTED" || next.status === "MAPPED") {
      return {
        tone: "success",
        title: "Loading FT Williams current data",
        message: "ERISAPros is fetching current values for comparison.",
      };
    }
    if (next.status === "NEEDS_REVIEW" || next.status === "READY_FOR_APPROVAL") {
      return {
        tone: "success",
        title: "Review is ready",
        message: `${displayName} is ready for field decisions.`,
      };
    }
    if (next.status === "APPROVED") {
      return {
        tone: "success",
        title: "Filing approved",
        message: `${displayName} is approved and ready for FT Williams.`,
      };
    }
    if (next.status === "FAILED" || next.status === "REJECTED") {
      return {
        tone: "error",
        title: "Filing needs attention",
        message: `${displayName} needs review before it can continue.`,
      };
    }
  }
  return null;
}

function reviewChangeSignature(filing: FilingDetail) {
  return [
    filing.status,
    filing.updated_at,
    (filing.fields || []).length,
    filing.missing_high_priority_count,
    filing.missing_medium_priority_count,
    filing.missing_low_priority_count,
    filing.low_confidence_count,
    filing.unmapped_count,
    filing.ftw_review?.status || "",
    filing.ftw_review?.current_query_success ? "ftw-loaded" : "ftw-pending",
    filing.ftw_review?.updated_at || "",
  ].join("|");
}

type FTWFormKind = "FORM_5500" | "SCHEDULE_A";

function expectsCurrentForForm(fields: ExtractedField[], reviewRows: ReviewDecisionRow[], formType: FTWFormKind) {
  const formLabelValue = formType === "FORM_5500" ? "Form 5500" : "Schedule A";
  return fields.some((field) => field.form_type === formType) || reviewRows.some((row) => row.formLabel === formLabelValue);
}

function hasLoadedCurrentForForm(review: FTWilliamsReview | null, formType: FTWFormKind) {
  if (!review?.current_query_success) return false;
  return (review.fields || []).some((field) => {
    if (field.form_type !== formType) return false;
    return hasUsableFtwCurrentValue(field.current_value);
  });
}

function hasUsableFtwCurrentValue(value: string | null | undefined) {
  const text = String(value || "").trim().toLowerCase();
  return Boolean(text && text !== "no current value" && text !== "not found" && text !== "pending");
}

function sendLockReason(
  filing: FilingDetail | null,
  form5500SafetyReady: boolean,
  scheduleASafetyReady: boolean,
  ftwCurrentLoaded: boolean,
) {
  const retryingFailedFtwUpdate = filing?.status === "FAILED" && filing.ftw_review?.status === "UPDATE_FAILED";
  if (filing?.status !== "APPROVED" && !retryingFailedFtwUpdate) return "Requires approval";
  if (filing?.ftw_review?.ftw_editable === false) return "Filing is locked in FT Williams";
  if (!ftwCurrentLoaded) return "Query FTW current data first";
  if (!form5500SafetyReady) return "Form 5500 current data missing";
  if (!scheduleASafetyReady) return "Schedule A match or current data missing";
  return "Requires safe FTW current query";
}

function WorkflowStepper({
  filing,
  ftwReadyToSend,
  needsDecisionCount,
  onStepSelect,
}: {
  filing: FilingDetail;
  ftwReadyToSend: boolean;
  needsDecisionCount: number;
  onStepSelect: (step: WorkflowStepKey) => void;
}) {
  const processing = isProcessingStatus(filing.status ?? "UPLOADED") && !(filing.fields || []).length;
  const ftwQuerying = filing.status === "QUERYING_FTW_CURRENT";
  const ftwLoaded = Boolean(filing.ftw_review?.current_query_success);
  const ftwScheduleMatch = Boolean(filing.ftw_review?.schedule_a_match);
  const ftwScheduleNeedsDecision = Boolean(
    ftwLoaded
    && !filing.ftw_review?.schedule_a_match
    && ((filing.ftw_review?.schedule_a_candidates || []).length || filing.ftw_review?.bring_forward_required),
  );
  const ftwScheduleStatus = ftwScheduleMatch ? "Best match selected" : ftwScheduleNeedsDecision ? "Needs your decision" : null;
  const approved = filing.status === "APPROVED";
  const updateSent = filing.ftw_review?.status === "UPDATE_SENT";
  const steps = [
    { key: "INTAKE" as const, label: "Intake", detail: "Package received", state: "done" },
    { key: "EXTRACTION" as const, label: "Extraction", detail: filing.extraction_provider || "Waiting", state: (filing.fields || []).length ? "done" : processing ? "active" : "pending" },
    { key: "FTW_LOADED" as const, label: "FTW loaded", detail: ftwLoaded ? "Current values loaded" : ftwQuerying ? "Fetching current values" : "Query current values", state: ftwScheduleNeedsDecision ? "active" : ftwLoaded ? "done" : ftwQuerying ? "active" : "pending" },
    { key: "REVIEW" as const, label: "Review", detail: processing ? "Waiting for extraction" : needsDecisionCount ? `${needsDecisionCount} fields need decision` : "No blockers", state: processing ? "pending" : needsDecisionCount ? "active" : "done" },
    { key: "APPROVAL" as const, label: "Approval", detail: processing ? "Waiting for extraction" : approved ? "Approved" : needsDecisionCount ? "Confirm unresolved items" : "Ready for approval", state: processing ? "locked" : approved ? "done" : "active" },
    { key: "FTW_UPDATE" as const, label: "FTW update", detail: updateSent ? "Sent" : ftwReadyToSend ? "Ready to send" : "Locked until approval", state: updateSent ? "done" : ftwReadyToSend ? "active" : "locked" },
  ];
  return (
    <section className="approval-progress-card">
      <div className="approval-workflow-source">
        <span><FileText size={14} /> Share File</span>
        <i aria-hidden="true">⇄</i>
        <strong>FTW Comparison</strong>
      </div>
      <div className="approval-stepper">
        {steps.map((step, index) => (
          <button className={`approval-step step-${step.state}`} key={step.label} type="button" onClick={() => onStepSelect(step.key)}>
            <span className="approval-step-index">
              {step.state === "done" ? <Check size={16} /> : step.state === "locked" ? <Lock size={16} /> : index + 1}
            </span>
            <div>
              <strong>{step.label}</strong>
              <small>{step.key === "FTW_LOADED" && ftwScheduleStatus ? ftwScheduleStatus : step.state === "done" ? "Complete" : step.state === "active" ? "Needs review" : step.state === "locked" ? "Locked" : "Pending"}</small>
              <em>{step.detail}</em>
            </div>
            {index < steps.length - 1 ? <i /> : null}
          </button>
        ))}
      </div>
    </section>
  );
}

function FilingGuidancePanel({
  actionRequiredCount,
  actions,
  approvalBlocked,
  bringForwardRequired,
  clientError,
  filingStatus,
  ftwReadyToSend,
  isProcessing,
  missingHighCount,
  scheduleSelectionRequired,
}: {
  actionRequiredCount: number;
  actions?: ReactNode;
  approvalBlocked: boolean;
  bringForwardRequired: boolean;
  clientError: ClientFacingError | null;
  filingStatus: string;
  ftwReadyToSend: boolean;
  isProcessing: boolean;
  missingHighCount: number;
  scheduleSelectionRequired: boolean;
}) {
  let source = "User Review";
  let title = "Ready for approval";
  let message = "Review the FT Williams update preview, then approve this filing.";
  let nextAction = "No blocking field issues remain.";
  let tone = "ready";

  if (isProcessing) {
    source = "Extraction";
    title = "Document processing is in progress";
    message = "The uploaded documents are being extracted and matched to field rules.";
    nextAction = "This page refreshes automatically when the review is ready.";
    tone = "ready";
  } else if (bringForwardRequired) {
    source = "FT Williams";
    title = "Current-year FT Williams record is missing";
    message = "Use Bring Forward for this plan in FT Williams, then refresh the current data here.";
    nextAction = "This must be completed before approval and sending.";
    tone = "warning";
  } else if (clientError) {
    source = clientError.source || "FT Williams";
    title = clientError.title || "This filing needs attention";
    message = clientError.message;
    nextAction = clientError.next_action || "Review the affected field and retry the action.";
    tone = clientError.severity === "warning" ? "warning" : "error";
  } else if (scheduleSelectionRequired) {
    source = "FT Williams";
    title = "Choose the matching Schedule A";
    message = "FT Williams returned more than one Schedule A for this plan.";
    nextAction = "Confirm the carrier, contract, year, and sequence below.";
    tone = "warning";
  } else if (approvalBlocked) {
    title = `${actionRequiredCount} field${actionRequiredCount === 1 ? "" : "s"} require attention`;
    message = `${missingHighCount} high-priority field${missingHighCount === 1 ? " is" : "s are"} missing or unresolved.`;
    nextAction = "Review them now, or approve with the confirmation override.";
    tone = "warning";
  } else if (filingStatus === "APPROVED" && ftwReadyToSend) {
    source = "FT Williams";
    title = "Approved and ready to send";
    message = "The proposed changes passed the review and FT Williams safety checks.";
    nextAction = "Send the update; the dashboard will verify every field automatically.";
  } else if (filingStatus === "APPROVED") {
    source = "FT Williams";
    title = "Approved — FT Williams check required";
    message = "Approval is complete, but FT Williams current data must be refreshed before sending.";
    nextAction = "Query FTW Current to unlock sending.";
    tone = "warning";
  }

  return (
    <section className={`filing-guidance guidance-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <div className="filing-guidance-icon">
        {tone === "ready" ? <CheckCircle2 size={21} /> : <AlertTriangle size={21} />}
      </div>
      <div className="filing-guidance-copy">
        <span className="filing-guidance-source">{source}</span>
        <strong>{title}</strong>
        <p>{message}</p>
        <small>{nextAction}</small>
      </div>
      {actions ? <div className="filing-guidance-actions">{actions}</div> : null}
    </section>
  );
}

function ReviewPrimaryActions({
  approvalBlocked,
  approvalReady,
  bringForwardRequired,
  busy,
  decisionAction,
  filingStatus,
  showFtwSendAction,
  ftwSendBusy,
  onApprove,
  onOpenBringForward,
  onPreviewXml,
  onQuery,
  onReEvaluate,
  onReject,
  onRetryExtraction,
  onSend,
  onOpenTechnical,
  queryBusy,
  retryBusy,
  rulesBusy,
  xmlBusy,
}: {
  approvalBlocked: boolean;
  approvalReady: boolean;
  bringForwardRequired: boolean;
  busy: boolean;
  decisionAction: "approve" | "reject" | "unapprove" | null;
  filingStatus: string;
  showFtwSendAction: boolean;
  ftwSendBusy: boolean;
  onApprove: () => void;
  onOpenBringForward: () => void;
  onPreviewXml: () => void;
  onQuery: () => void;
  onReEvaluate: () => void;
  onReject: () => void;
  onRetryExtraction: () => void;
  onSend: () => void;
  onOpenTechnical: () => void;
  queryBusy: boolean;
  retryBusy: boolean;
  rulesBusy: boolean;
  xmlBusy: boolean;
}) {
  const approved = filingStatus === "APPROVED";
  const failed = filingStatus === "FAILED";
  return (
    <div className="review-primary-actions" aria-label="Filing actions">
      {bringForwardRequired ? (
        <button className="button button-warn" type="button" disabled={busy} onClick={onOpenBringForward}>
          <ExternalLink size={16} /> Open FTW Bring Forward
        </button>
      ) : null}
      <button className="button secondary" type="button" disabled={queryBusy} onClick={onQuery}>
        {queryBusy ? <InlineLoader label="Fetching FTW" /> : <><Search size={16} /> Query FTW Current</>}
      </button>
      {!approved && approvalReady ? (
        <>
          <button
            className={`button ${approvalBlocked ? "button-warn" : ""}`}
            disabled={busy}
            onClick={onApprove}
          >
            {decisionAction === "approve" ? <InlineLoader label="Approving" /> : <><CheckCircle2 size={16} /> Approve Filing</>}
          </button>
          <button className="button danger" disabled={busy} onClick={onReject}>
            {decisionAction === "reject" ? <InlineLoader label="Rejecting" /> : "Reject"}
          </button>
        </>
      ) : null}
      {approved ? <span className="review-approved-badge"><CheckCircle2 size={16} /> Approved</span> : null}
      {showFtwSendAction ? (
        <button className="button" disabled={busy} onClick={onSend}>
          {ftwSendBusy ? <InlineLoader label="Sending to FT Williams" /> : <><ShieldCheck size={16} /> {failed ? "Retry remaining" : "Send to FT Williams"}</>}
        </button>
      ) : null}
      <button className="button secondary" type="button" onClick={onOpenTechnical}>
        <SlidersHorizontal size={16} /> More actions
      </button>
    </div>
  );
}

function WorkflowDetailDialog({
  actionRequiredCount,
  approvalBlocked,
  approvalReady,
  busy,
  filing,
  foundCount,
  ftwCurrentLoaded,
  ftwReadyToSend,
  onClose,
  onApprove,
  onOpenBringForward,
  onQuery,
  onViewAuditPDF,
  onSelectSchedule,
  onShowTab,
  scheduleCandidates,
  step,
  totalFields,
  willUpdateCount,
}: {
  actionRequiredCount: number;
  approvalBlocked: boolean;
  approvalReady: boolean;
  busy: boolean;
  filing: FilingDetail;
  foundCount: number;
  ftwCurrentLoaded: boolean;
  ftwReadyToSend: boolean;
  onClose: () => void;
  onApprove: () => void;
  onOpenBringForward: () => void;
  onQuery: () => void;
  onViewAuditPDF: () => void;
  onSelectSchedule: (payload: { ftw_seq_no?: string; carrier?: string; carrier_ein?: string; contract?: string }) => void;
  onShowTab: (tab: ReviewTab) => void;
  scheduleCandidates: Array<Record<string, unknown>>;
  step: WorkflowStepKey;
  totalFields: number;
  willUpdateCount: number;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, dialogRef, onClose);
  const stepNumber = ["INTAKE", "EXTRACTION", "FTW_LOADED", "REVIEW", "APPROVAL", "FTW_UPDATE"].indexOf(step) + 1;
  const titles: Record<WorkflowStepKey, string> = {
    INTAKE: "Intake",
    EXTRACTION: "Extraction",
    FTW_LOADED: "FTW loaded",
    REVIEW: "User review",
    APPROVAL: "Approval",
    FTW_UPDATE: "FT Williams verification",
  };
  const review = filing.ftw_review || null;
  const attempted = review?.update_attempted_count || 0;
  const confirmed = review?.update_confirmed_count || 0;
  const remaining = review?.update_remaining_count || 0;
  const packageCount = filing.package_document_count || 1;
  const currentScheduleSequence = textValue(review?.schedule_a_match?.ftw_seq_no);
  const [selectedScheduleIndex, setSelectedScheduleIndex] = useState(() => {
    const currentIndex = scheduleCandidates.findIndex((candidate) => textValue(candidate.ftw_seq_no) === currentScheduleSequence);
    return currentIndex >= 0 ? String(currentIndex) : "";
  });
  const selectedSchedule = selectedScheduleIndex === "" ? null : scheduleCandidates[Number(selectedScheduleIndex)] || null;
  const scheduleMatchSelected = Boolean(review?.schedule_a_match);
  const scheduleDecisionRequired = Boolean(
    (review?.current_query_success || ftwCurrentLoaded)
    && !scheduleMatchSelected
    && (scheduleCandidates.length || review?.bring_forward_required),
  );
  const matchSource = textValue(review?.schedule_a_match?.source).toUpperCase();
  const recommendedSequence = scheduleMatchSelected && !["MANUAL", "NEW_SCHEDULE_A"].includes(matchSource)
    ? currentScheduleSequence
    : "";
  const selectedScheduleScore = textValue(selectedSchedule?.score) || textValue(review?.schedule_a_match?.score) || "Not scored";
  const rawMatchReasons = review?.schedule_a_match?.match_reasons;
  const selectedScheduleReasons = Array.isArray(rawMatchReasons)
    ? rawMatchReasons.map(textValue).filter(Boolean).join(", ")
    : "";
  const selectedScheduleLabel = selectedSchedule
    ? textValue(selectedSchedule.carrier) || textValue(selectedSchedule.description) || `FTW sequence ${textValue(selectedSchedule.ftw_seq_no)}`
    : currentScheduleSequence
      ? formatScheduleAMatch(review?.schedule_a_match)
      : "No Schedule A selected";
  const footerNotes: Record<WorkflowStepKey, string> = {
    INTAKE: `${packageCount} document${packageCount === 1 ? "" : "s"} received from ${filing.intake_source || "ShareFile"}`,
    EXTRACTION: `${foundCount} of ${totalFields} fields extracted`,
    FTW_LOADED: selectedScheduleLabel,
    REVIEW: actionRequiredCount ? `${actionRequiredCount} field${actionRequiredCount === 1 ? "" : "s"} still require action` : "Review complete",
    APPROVAL: filing.status === "APPROVED" ? "Filing approved" : "Approval not yet confirmed",
    FTW_UPDATE: review?.status === "UPDATE_SENT" ? `${confirmed} field${confirmed === 1 ? "" : "s"} verified` : "Update not yet verified",
  };

  function handleScheduleChange(index: string) {
    setSelectedScheduleIndex(index);
    if (index === "") return;
    const candidate = scheduleCandidates[Number(index)];
    if (!candidate) return;
    onSelectSchedule({
      ftw_seq_no: textValue(candidate.ftw_seq_no) || undefined,
      carrier: textValue(candidate.carrier) || undefined,
      carrier_ein: textValue(candidate.carrier_ein) || undefined,
      contract: textValue(candidate.contract) || undefined,
    });
  }

  return (
    <div className="workflow-dialog-backdrop" role="presentation">
      <section ref={dialogRef} tabIndex={-1} className="workflow-dialog" role="dialog" aria-modal="true" aria-labelledby="workflow-dialog-title">
        <header>
          <span className="workflow-dialog-number">{stepNumber}</span>
          <div>
            <small>Workflow step</small>
            <h2 id="workflow-dialog-title">{titles[step]}</h2>
          </div>
          <button type="button" className="icon-button" aria-label="Close workflow details" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="workflow-dialog-body">
          {step === "INTAKE" ? (
            <>
              <WorkflowStatus tone="success" label="Package received" />
              <p className="workflow-explanation">The Schedule A and Plan Worksheet package has been received and is tracked as one filing.</p>
              <WorkflowActivity items={[
                formatFilingDisplayName(filing.file_name),
                `${packageCount} document${packageCount === 1 ? "" : "s"} received`,
                `${filing.intake_source || "ShareFile"} intake connected`,
              ]} />
            </>
          ) : null}

          {step === "EXTRACTION" ? (
            <>
              <WorkflowStatus tone={foundCount ? "success" : "warning"} label={foundCount ? "Extraction complete" : "Extraction in progress"} />
              <p className="workflow-explanation">Published field rules and Schedule A aliases guide extraction. The resulting values appear in the Extracted column.</p>
              <WorkflowActivity items={[
                `${foundCount} of ${totalFields} fields extracted`,
                `${filing.extraction_provider || "Extraction service"} completed the document review`,
                `${percent(filing.overall_confidence)} overall confidence`,
              ]} />
            </>
          ) : null}

          {step === "FTW_LOADED" ? (
            <>
              <WorkflowStatus
                tone={scheduleDecisionRequired || !ftwCurrentLoaded ? "warning" : "success"}
                label={scheduleMatchSelected ? "Best match selected" : scheduleDecisionRequired ? "Needs your decision" : ftwCurrentLoaded ? "FTW current data loaded" : "Processing"}
              />
              {scheduleMatchSelected ? (
                <div className="workflow-schedule-match-summary">
                  <span><CheckCircle2 aria-hidden="true" size={14} /> Selected Schedule A</span>
                  <strong>{selectedScheduleLabel}</strong>
                  <small>
                    <span>Match score {selectedScheduleScore}</span>
                    {selectedScheduleReasons ? <span>Matched by {selectedScheduleReasons}</span> : null}
                  </small>
                </div>
              ) : scheduleDecisionRequired && review?.bring_forward_required ? (
                <p className="workflow-schedule-decision-note">
                  FT Williams has no current Schedule A for this filing. Complete Bring Forward in FT Williams, then refresh the current data.
                </p>
              ) : scheduleDecisionRequired ? (
                <p className="workflow-schedule-decision-note">
                  FT Williams returned {scheduleCandidates.length} possible Schedule A {scheduleCandidates.length === 1 ? "record" : "records"}, but none passed the safe identity match. Choose the correct record to continue.
                </p>
              ) : null}
              {scheduleCandidates.length ? (
                <WorkflowScheduleSelect
                  busy={busy}
                  candidates={scheduleCandidates}
                  recommendedSequence={recommendedSequence}
                  selectedIndex={selectedScheduleIndex}
                  onChange={handleScheduleChange}
                />
              ) : null}
              <p className="workflow-explanation">ERISAPros fetches current FT Williams values so extracted and existing data can be compared.</p>
              <WorkflowActivity items={[
                `${foundCount} of ${totalFields} fields extracted`,
                review?.customer_id ? "FT Williams plan connection active" : "FT Williams plan match pending",
                ftwCurrentLoaded ? "Current values loaded automatically" : "Current values are ready to refresh",
                `Filing is ${review?.ftw_editable === false ? "locked" : review?.ftw_editable === true ? "editable" : "awaiting editability check"}`,
              ]} />
              <div className="workflow-dialog-actions">
                {review?.bring_forward_required ? <button className="button secondary" type="button" disabled={busy} onClick={onOpenBringForward}>Open FTW Bring Forward</button> : null}
                <button className="button secondary" type="button" disabled={busy} onClick={onQuery}><Search size={15} /> Refresh FTW Data</button>
              </div>
            </>
          ) : null}

          {step === "REVIEW" ? (
            <>
              <WorkflowStatus tone={actionRequiredCount ? "warning" : "success"} label={actionRequiredCount ? "Needs review" : "Review complete"} />
              {scheduleCandidates.length ? (
                <WorkflowScheduleSelect busy={busy} candidates={scheduleCandidates} recommendedSequence={recommendedSequence} selectedIndex={selectedScheduleIndex} onChange={handleScheduleChange} />
              ) : null}
              <WorkflowReviewCenter
                actionRequiredCount={actionRequiredCount}
                onShowTab={onShowTab}
                review={review}
                totalFields={totalFields}
                willUpdateCount={willUpdateCount}
              />
            </>
          ) : null}

          {step === "APPROVAL" ? (
            <>
              <WorkflowStatus tone={filing.status === "APPROVED" ? "success" : "warning"} label={filing.status === "APPROVED" ? "Filing approved" : "Approval pending"} />
              <p className="workflow-explanation">Approval confirms the reviewed values. Any remaining issues are shown before the user confirms.</p>
              <WorkflowActivity items={[
                actionRequiredCount ? `${actionRequiredCount} unresolved field${actionRequiredCount === 1 ? "" : "s"} will be confirmed during approval` : "All field decisions are complete",
                ftwCurrentLoaded ? "FT Williams current data is loaded" : "FT Williams current data is required",
                filing.status === "APPROVED" ? "Approval recorded" : "Approval is waiting for confirmation",
              ]} />
            </>
          ) : null}

          {step === "FTW_UPDATE" ? (
            <>
              <WorkflowStatus
                tone={review?.status === "UPDATE_SENT" ? "success" : review?.status === "UPDATE_UNKNOWN" || ftwReadyToSend ? "warning" : "neutral"}
                label={review?.status === "UPDATE_SENT" ? "Update verified" : review?.status === "UPDATE_UNKNOWN" ? "Verification required" : ftwReadyToSend ? "Ready to send" : "Waiting for approval and FTW checks"}
              />
              <p className="workflow-explanation">After sending, ERISAPros refreshes FT Williams and verifies every returned value automatically.</p>
              <WorkflowActivity items={[
                `${attempted} field${attempted === 1 ? "" : "s"} attempted`,
                `${confirmed} field${confirmed === 1 ? "" : "s"} verified`,
                `${remaining} field${remaining === 1 ? "" : "s"} need review`,
                review?.schema_validation_results?.length
                  ? review.schema_validation_results.every((result) => result.valid)
                    ? "Outgoing FTW schema validation passed"
                    : "Outgoing FTW schema validation needs attention"
                  : "Outgoing FTW schema validation not run",
                review?.edit_check_baseline_success === true
                  ? "Baseline Edit Checks passed"
                  : review?.edit_check_baseline_success === false
                    ? `${review.edit_check_baseline_issues?.length || 0} baseline FT issue${review.edit_check_baseline_issues?.length === 1 ? "" : "s"} recorded as warning${review.edit_check_baseline_issues?.length === 1 ? "" : "s"}`
                    : "Baseline Edit Checks not run",
                review?.edit_check_final_success === true
                  ? "Final Edit Checks passed"
                  : review?.edit_check_final_success === false
                    ? ftwEditCheckOutcome(review)?.title || "Final Edit Checks need attention"
                    : "Final Edit Checks not run",
              ]} />
              {(review?.schema_validation_results || []).flatMap((result) => result.issues || []).length ? (
                <div className="workflow-result-list" aria-label="FT Williams schema validation issues">
                  {(review?.schema_validation_results || []).flatMap((result) => result.issues || []).map((issue, index) => (
                    <div key={`${issue.tag}-${index}`} className="needs-review">
                      <AlertTriangle size={16} />
                      <span><strong>{issue.tag}: {issue.value || "blank"}</strong><small>{issue.reason} Expected: {issue.expected_format || "documented FT format"}. {issue.correction || ""}</small></span>
                    </div>
                  ))}
                </div>
              ) : null}
              {(review?.update_results || []).length ? (
                <div className="workflow-result-list">
                  {(review?.update_results || []).map((result, index) => (
                    <div key={result.field_id || result.tag || index} className={result.status === "VERIFIED" ? "verified" : "needs-review"}>
                      {result.status === "VERIFIED" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                      <span><strong>{result.label}</strong><small>{result.status === "VERIFIED" ? `Updated to ${result.sent_value || "approved value"}` : result.reason || "FT Williams returned a different value."}</small></span>
                    </div>
                  ))}
                </div>
              ) : null}
              {review?.audit_pdf_status === "AVAILABLE" ? (
                <div className="workflow-dialog-actions">
                  <button className="button secondary" type="button" onClick={onViewAuditPDF}>
                    <FileText size={15} /> View verified PDF
                  </button>
                  {review.audit_pdf_sha256 ? <small>SHA-256 {review.audit_pdf_sha256.slice(0, 12)}…</small> : null}
                </div>
              ) : review?.audit_pdf_status === "FAILED" ? (
                <p className="workflow-schedule-decision-note">The FTW update was verified, but PDF evidence could not be generated: {review.audit_pdf_error}</p>
              ) : null}
            </>
          ) : null}
        </div>
        <footer>
          <span className="workflow-dialog-footer-note">{footerNotes[step]}</span>
          <div className="workflow-dialog-footer-actions">
            {step === "APPROVAL" && filing.status !== "APPROVED" && approvalReady ? (
              <button
                className={`button ${approvalBlocked ? "button-warn" : ""}`}
                type="button"
                disabled={busy}
                onClick={onApprove}
              >
                <CheckCircle2 size={15} /> Approve filing
              </button>
            ) : null}
            <button className="button secondary" type="button" onClick={onClose}>Done</button>
          </div>
        </footer>
      </section>
    </div>
  );
}

function WorkflowStatus({ label, tone }: { label: string; tone: "neutral" | "success" | "warning" }) {
  return <span className={`workflow-status-pill workflow-status-pill-${tone}`}>{label}</span>;
}

function WorkflowActivity({ items }: { items: string[] }) {
  return (
    <section className="workflow-step-activity" aria-label="Step activity">
      <h3>Step activity</h3>
      {items.map((item, index) => <div key={`${item}-${index}`}><span aria-hidden="true" />{item}</div>)}
    </section>
  );
}

function WorkflowScheduleSelect({
  busy,
  candidates,
  onChange,
  recommendedSequence,
  selectedIndex,
}: {
  busy: boolean;
  candidates: Array<Record<string, unknown>>;
  onChange: (index: string) => void;
  recommendedSequence: string;
  selectedIndex: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const selectedCandidate = selectedIndex === "" ? null : candidates[Number(selectedIndex)] || null;
  const selectedLabel = selectedCandidate
    ? [
      textValue(selectedCandidate.carrier) || textValue(selectedCandidate.description) || "Schedule A",
      textValue(selectedCandidate.contract) && `Contract ${textValue(selectedCandidate.contract)}`,
      textValue(selectedCandidate.ftw_seq_no) && `Seq ${textValue(selectedCandidate.ftw_seq_no)}`,
    ].filter(Boolean).join(" · ")
    : "Select Schedule A";

  useEffect(() => {
    function closeOnOutsidePointer(event: PointerEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  return (
    <div className="workflow-schedule-select" ref={ref}>
      <span className="workflow-schedule-label" id="workflow-schedule-candidate-label">{selectedCandidate ? "Selected Schedule A" : "Choose Schedule A"}</span>
      <button
        aria-controls="workflow-schedule-candidate-menu"
        aria-expanded={open}
        aria-haspopup="listbox"
        className="workflow-schedule-trigger"
        disabled={busy}
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        <span>{selectedLabel}</span>
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div
          aria-labelledby="workflow-schedule-candidate-label"
          className="workflow-schedule-menu"
          id="workflow-schedule-candidate-menu"
          role="listbox"
        >
          {candidates.map((candidate, index) => {
            const carrier = textValue(candidate.carrier) || textValue(candidate.description) || "Schedule A";
            const contract = textValue(candidate.contract);
            const sequence = textValue(candidate.ftw_seq_no);
            const score = textValue(candidate.score);
            const recommended = Boolean(recommendedSequence && sequence === recommendedSequence);
            const hasCurrentData = candidate.has_current_data === true;
            const selected = selectedIndex === String(index);
            return (
              <button
                aria-selected={selected}
                className={[selected ? "selected" : "", recommended ? "recommended" : ""].filter(Boolean).join(" ")}
                key={sequence || `${carrier}-${index}`}
                onClick={() => {
                  onChange(String(index));
                  setOpen(false);
                }}
                role="option"
                type="button"
              >
                <span className="workflow-schedule-candidate-copy">
                  <span className="workflow-schedule-candidate-heading">
                    <strong>{carrier}</strong>
                    {recommended ? <span className="workflow-schedule-recommended"><Sparkles aria-hidden="true" size={10} /> Recommended</span> : null}
                  </span>
                  <small>{[contract && `Contract ${contract}`, sequence && `Seq ${sequence}`].filter(Boolean).join(" · ") || "Schedule details unavailable"}</small>
                  <span className="workflow-schedule-candidate-meta">
                    <span className={hasCurrentData ? "loaded" : "unavailable"}>
                      {hasCurrentData ? <CheckCircle2 aria-hidden="true" size={11} /> : <AlertTriangle aria-hidden="true" size={11} />}
                      {hasCurrentData ? "Current data loaded" : "Details unavailable"}
                    </span>
                    {score ? <span className="score">Match score {score}</span> : null}
                  </span>
                </span>
                {selected ? <span className="workflow-schedule-selected"><Check aria-hidden="true" size={14} /></span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
      <small>{candidates.length} Schedule A filing{candidates.length === 1 ? "" : "s"} available</small>
    </div>
  );
}

function WorkflowReviewCenter({
  actionRequiredCount,
  onShowTab,
  review,
  totalFields,
  willUpdateCount,
}: {
  actionRequiredCount: number;
  onShowTab: (tab: ReviewTab) => void;
  review: FTWilliamsReview | null;
  totalFields: number;
  willUpdateCount: number;
}) {
  const reviewComplete = actionRequiredCount === 0;
  return (
    <div className="workflow-review-center">
      <section className="workflow-review-panel workflow-user-review-panel">
        <header>
          <span className={reviewComplete ? "complete" : "attention"}>
            {reviewComplete ? <CheckCircle2 size={17} /> : <ClipboardCheck size={17} />}
          </span>
          <div>
            <small>User review</small>
            <strong>{reviewComplete ? "All field decisions are complete" : `${actionRequiredCount} field${actionRequiredCount === 1 ? "" : "s"} require attention`}</strong>
          </div>
        </header>
        <p>{reviewComplete ? "The reviewed values are ready for approval." : "Resolve missing or different values, then confirm what should be sent to FT Williams."}</p>
        <div className="workflow-review-metrics" aria-label="User review counts">
          <span><small>Action required</small><strong>{actionRequiredCount}</strong></span>
          <span><small>Will update FTW</small><strong>{willUpdateCount}</strong></span>
          <span><small>Compared</small><strong>{totalFields}</strong></span>
        </div>
        <div className="workflow-dialog-actions">
          {actionRequiredCount ? <button className="button" type="button" onClick={() => onShowTab("NEEDS_DECISION")}>Review action required</button> : null}
          <button className="button secondary" type="button" onClick={() => onShowTab("WILL_UPDATE")}>Review FTW updates</button>
        </div>
      </section>
      <FTWVerificationSummary review={review} onReview={() => onShowTab("NEEDS_DECISION")} compact />
    </div>
  );
}

function TechnicalReviewDrawer({
  busy,
  canSendUpdate,
  filing,
  onClose,
  onPreparePreview,
  onPreviewXml,
  onQueryCurrent,
  onReEvaluate,
  onRetryExtraction,
  onSaveManualMatch,
  onSelectScheduleMatch,
  onSendUpdate,
  sendBusy,
}: {
  busy: boolean;
  canSendUpdate: boolean;
  filing: FilingDetail;
  onClose: () => void;
  onPreparePreview: () => void;
  onPreviewXml: () => void;
  onQueryCurrent: () => void;
  onReEvaluate: () => void;
  onRetryExtraction: () => void;
  onSaveManualMatch: (payload: { customer_id?: string; plan_id?: string; ftw_customer_id?: string; ftw_plan_id?: string; year?: string }) => void;
  onSelectScheduleMatch: (payload: { ftw_seq_no?: string; carrier?: string; carrier_ein?: string; contract?: string; create_new?: boolean; schedule_desc?: string }) => void;
  onSendUpdate: () => void;
  sendBusy: boolean;
}) {
  const drawerRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, drawerRef, onClose);
  return (
    <div className="technical-drawer-backdrop" role="presentation">
      <aside ref={drawerRef} tabIndex={-1} className="technical-review-drawer" role="dialog" aria-modal="true" aria-labelledby="technical-drawer-title">
        <header>
          <div>
            <span className="eyebrow">Administrator workspace</span>
            <h2 id="technical-drawer-title">Technical details</h2>
            <p>FT Williams matching, processing logs, and XML.</p>
          </div>
          <button type="button" className="icon-button" aria-label="Close technical details" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="technical-drawer-toolbar">
          <button className="button secondary" type="button" disabled={busy} onClick={onReEvaluate}><Sparkles size={15} /> Re-evaluate rules</button>
          <button className="button secondary" type="button" disabled={busy} onClick={onRetryExtraction}><RefreshCw size={15} /> Retry extraction</button>
          <button className="button secondary" type="button" disabled={busy} onClick={onPreviewXml}><FileText size={15} /> Regenerate XML</button>
        </div>
        <div className="technical-drawer-body">
          <FTWilliamsComparisonPanel
            review={filing.ftw_review || null}
            busy={busy}
            sendBusy={sendBusy}
            canSendUpdate={canSendUpdate}
            onPreparePreview={onPreparePreview}
            onQueryCurrent={onQueryCurrent}
            onSaveManualMatch={onSaveManualMatch}
            onSelectScheduleMatch={onSelectScheduleMatch}
            onSendUpdate={onSendUpdate}
          />
          <details className="review-xml card">
            <summary><span><ChevronDown size={16} /> Processing logs</span></summary>
            <div className="audit-log-list">
              {(filing.jobs || []).map((job) => <div key={job.id} className="audit-log-row"><strong>{job.status.replaceAll("_", " ")}</strong><span>{job.provider} attempt {job.attempts || 0}/{job.max_attempts}</span>{job.last_error ? <small>{job.last_error}</small> : null}</div>)}
              {(filing.audit_logs || []).map((log) => <div key={log.id} className="audit-log-row"><strong>{log.event.replaceAll("_", " ")}</strong><span>{log.message}</span><small>{formatDate(log.created_at)}</small></div>)}
              {!(filing.jobs || []).length && !(filing.audit_logs || []).length ? <p className="subtle">No logs yet.</p> : null}
            </div>
          </details>
          <details className="review-xml card">
            <summary><span><ChevronDown size={16} /> Technical XML preview</span></summary>
            <pre className="xml">{filing.proposed_xml || "XML will appear after extraction."}</pre>
          </details>
        </div>
      </aside>
    </div>
  );
}

function ScheduleASelectionStep({
  busy,
  candidates,
  onSelect,
}: {
  busy: boolean;
  candidates: Array<Record<string, unknown>>;
  onSelect: (payload: { ftw_seq_no?: string; carrier?: string; carrier_ein?: string; contract?: string }) => void;
}) {
  return (
    <section className="schedule-selection-step" aria-labelledby="schedule-selection-title">
      <div className="schedule-selection-head">
        <div>
          <span className="eyebrow">Required step</span>
          <h2 id="schedule-selection-title">Select the matching Schedule A</h2>
          <p>Compare the carrier, contract, plan year, and FTW sequence. The strongest match is shown first.</p>
        </div>
        <span>{candidates.length} candidates</span>
      </div>
      <div className="schedule-selection-grid">
        {candidates.map((candidate, index) => {
          const seq = textValue(candidate.ftw_seq_no);
          const carrier = textValue(candidate.carrier) || textValue(candidate.description) || "Carrier unavailable";
          const contract = textValue(candidate.contract) || "No contract number";
          const year = textValue(candidate.year) || [textValue(candidate.plan_year_begin), textValue(candidate.plan_year_end)].filter(Boolean).join(" – ") || "Year unavailable";
          return (
            <article className={index === 0 ? "recommended" : ""} key={seq || `${carrier}-${index}`}>
              <div>
                {index === 0 ? <span className="schedule-recommended">Recommended</span> : null}
                <strong>{carrier}</strong>
                <small>Contract {contract}</small>
                <small>{year}</small>
              </div>
              <div className="schedule-selection-action">
                <span>FTW sequence {seq || "—"}</span>
                <button
                  className="button secondary"
                  type="button"
                  disabled={busy || !seq}
                  onClick={() => onSelect({
                    ftw_seq_no: seq,
                    carrier: textValue(candidate.carrier) || undefined,
                    carrier_ein: textValue(candidate.carrier_ein) || undefined,
                    contract: textValue(candidate.contract) || undefined,
                  })}
                >
                  Select Schedule A
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function FTWVerificationSummary({ review, onReview, compact = false }: { review: FTWilliamsReview | null; onReview: () => void; compact?: boolean }) {
  const attempted = review?.update_attempted_count || 0;
  const verificationAttempted = Boolean(review?.update_verification_attempted || attempted);
  const confirmed = review?.update_confirmed_count || 0;
  const remaining = review?.update_remaining_count || 0;
  const results = review?.update_results || [];
  const complete = attempted > 0 && remaining === 0 && review?.update_verification_success !== false;
  const validationOutcome = ftwEditCheckOutcome(review);
  const finalEditCheckIssues = review?.edit_check_final_issues || [];

  if (compact && !verificationAttempted) {
    return (
      <section className="workflow-review-panel workflow-verification-panel waiting" aria-label="FT Williams verification">
        <header>
          <span><ShieldCheck size={17} /></span>
          <div>
            <small>FT Williams verification</small>
            <strong>Waiting for an FT Williams update</strong>
          </div>
        </header>
        <p>Verification results will appear here automatically after data is sent to FT Williams.</p>
      </section>
    );
  }

  if (!verificationAttempted) return null;

  if (compact) {
    return (
      <section className={`workflow-review-panel workflow-verification-panel ${complete ? "complete" : "attention"}`} aria-label="FT Williams verification">
        <header>
          <span>{complete ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}</span>
          <div>
            <small>FT Williams verification</small>
            <strong>{complete ? `${confirmed} field${confirmed === 1 ? "" : "s"} updated and verified` : `${confirmed} field${confirmed === 1 ? "" : "s"} updated · ${remaining} need review`}</strong>
          </div>
        </header>
        <p>The dashboard refreshed FT Williams and compared every returned value automatically.</p>
        <div className="workflow-review-metrics" aria-label="FT Williams verification counts">
          <span><small>Attempted</small><strong>{attempted}</strong></span>
          <span><small>Verified</small><strong>{confirmed}</strong></span>
          <span><small>Need review</small><strong>{remaining}</strong></span>
        </div>
        {results.length ? (
          <details className="workflow-verification-results" open={!complete}>
            <summary>View field results</summary>
            <div>
              {results.map((result, index) => (
                <article className={result.status === "VERIFIED" ? "verified" : "needs-correction"} key={result.field_id || result.tag || index}>
                  {result.status === "VERIFIED" ? <Check size={14} /> : <AlertTriangle size={14} />}
                  <span><strong>{result.label}</strong><small>{result.status === "VERIFIED" ? `Updated to ${result.sent_value || "the approved value"}` : result.reason || "FT Williams returned a different value."}</small></span>
                </article>
              ))}
            </div>
          </details>
        ) : null}
        {validationOutcome ? (
          <div className={`ftw-validation-outcome ${validationOutcome.tone}`}>
            <strong>{validationOutcome.title}</strong>
            <small>{validationOutcome.detail}</small>
            {review?.edit_check_final_success === false || finalEditCheckIssues.length ? (
              <FTWilliamsDiagnostic
                editCheckIssues={finalEditCheckIssues}
                message={validationOutcome.summary}
                operations={review?.update_diagnostics || []}
              />
            ) : null}
          </div>
        ) : null}
        {!complete ? <div className="workflow-dialog-actions"><button className="button secondary" type="button" onClick={onReview}>Review remaining fields</button></div> : null}
      </section>
    );
  }

  return (
    <section className={`ftw-verification-summary ${complete ? "complete" : "partial"}`} role="status">
      <div className="ftw-verification-icon">{complete ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}</div>
      <div className="ftw-verification-copy">
        <span>FT Williams verification</span>
        <strong>{complete ? `FT Williams updated successfully — ${confirmed} field${confirmed === 1 ? "" : "s"} verified` : `${confirmed} field${confirmed === 1 ? "" : "s"} updated — ${remaining} need review`}</strong>
        <small>The dashboard refreshed FT Williams and compared the returned values automatically.</small>
        {results.length ? (
          <details>
            <summary>View field results</summary>
            <div className="ftw-verification-results">
              {results.map((result, index) => (
                <div className={result.status === "VERIFIED" ? "verified" : "needs-correction"} key={result.field_id || result.tag || index}>
                  <span>{result.status === "VERIFIED" ? <Check size={14} /> : <AlertTriangle size={14} />}</span>
                  <strong>{result.label}</strong>
                  <small>{result.status === "VERIFIED" ? `Updated to ${result.sent_value || "the approved value"}` : result.reason || "FT Williams returned a different value."}</small>
                </div>
              ))}
            </div>
          </details>
        ) : null}
        {validationOutcome ? (
          <div className={`ftw-validation-outcome ${validationOutcome.tone}`}>
            <strong>{validationOutcome.title}</strong>
            <small>{validationOutcome.detail}</small>
          </div>
        ) : null}
      </div>
      {!complete ? <button className="button secondary" type="button" onClick={onReview}>Review remaining fields</button> : null}
    </section>
  );
}

function ftwEditCheckOutcome(review: FTWilliamsReview | null | undefined): {
  detail: string;
  summary: string;
  title: string;
  tone: "attention" | "warning";
} | null {
  switch (review?.edit_check_validation_status) {
    case "EXISTING_ISSUES":
      return {
        title: "Update verified — existing FT Williams issues remain",
        summary: "The approved data was updated and verified. Existing FT Williams validation issues remain.",
        detail: "These issues existed before this update and did not prevent the approved values from being saved.",
        tone: "warning",
      };
    case "IMPROVED":
      return {
        title: "Update verified — fewer FT Williams issues remain",
        summary: "The approved data was updated and verified, and some existing FT Williams issues were resolved.",
        detail: "Any remaining validation issues can be corrected separately.",
        tone: "warning",
      };
    case "NEW_ISSUES":
      return {
        title: "Update verified — new FT Williams validation issues need attention",
        summary: "The approved data was updated and verified, but final FT Williams checks reported new issues.",
        detail: "Review the new issues before the filing is finalized. The verified update itself was successful.",
        tone: "attention",
      };
    case "CHECK_UNAVAILABLE":
    case "CANNOT_COMPARE":
      return {
        title: "Update verified — FT Williams validation could not be confirmed",
        summary: "The approved data was updated and verified, but FT Williams validation results were unavailable.",
        detail: "Run Edit Checks again before the filing is finalized.",
        tone: "attention",
      };
    default:
      return null;
  }
}

function PanelHeading({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <div className="approval-panel-heading">
      {icon}
      <strong>{title}</strong>
    </div>
  );
}

function PackageDocumentCard({
  compact,
  confidence,
  found,
  label,
  missing,
  source,
  title,
}: {
  compact?: boolean;
  confidence: string;
  found?: number;
  label: string;
  missing?: number;
  source: string;
  title: string;
}) {
  return (
    <div className={`package-document-card ${compact ? "compact" : ""}`}>
      <div className="package-document-top">
        <FileText size={18} />
        <span>{label}</span>
      </div>
      <strong>{title}</strong>
      <dl>
        <div>
          <dt>Source</dt>
          <dd>{source}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{confidence}</dd>
        </div>
      </dl>
      {typeof found === "number" ? (
        <div className="package-document-counts">
          <span><strong>{found}</strong> found</span>
          <span><strong>{missing ?? 0}</strong> missing</span>
        </div>
      ) : null}
    </div>
  );
}

function ReviewCountTab({
  active,
  count,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  count: number;
  icon?: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={active ? "active" : ""} onClick={onClick} type="button">
      {icon}
      <span>{label}</span>
      <strong>{count}</strong>
    </button>
  );
}

function ReviewDecisionTableRow({
  disabled = false,
  onAccept,
  onInspect,
  onKeepFtw,
  row,
  saving = false,
  selected,
}: {
  disabled?: boolean;
  onAccept: () => void;
  onInspect: () => void;
  onKeepFtw: () => void;
  row: ReviewDecisionRow;
  saving?: boolean;
  selected: boolean;
}) {
  const canEdit = Boolean(row.fieldId);
  const issueClass = row.failedByFtw ? "issue-ftw-rejected" : `issue-${row.group.toLowerCase()}`;
  return (
    <tr className={`${selected ? "selected" : ""} review-row-${row.group.toLowerCase()} ${row.failedByFtw ? "review-row-ftw-rejected" : ""}`}>
      <td>
        <strong>{row.label}</strong>
        <small>{row.formLabel} / {row.section}</small>
      </td>
      <td>{row.extracted || <span className="muted-value">Not found</span>}</td>
      <td>{row.currentFtw || <span className="muted-value">No current value</span>}</td>
      <td className="proposed-cell">
        <button className="proposed-value-button" type="button" disabled={disabled || !canEdit} onClick={onInspect}>
          <span>{row.proposed || "No proposed value"}</span>
          {canEdit ? <Edit3 size={14} /> : null}
        </button>
        <div className="decision-actions">
          {saving ? (
            <button type="button" disabled><InlineLoader label="Saving" /></button>
          ) : (
            <>
              {row.extracted ? (
                <button
                  className={row.proposed === row.extracted ? "active" : ""}
                  type="button"
                  disabled={disabled || !canEdit}
                  onClick={onAccept}
                >
                  Keep Extracted
                </button>
              ) : null}
              <button
                className={row.proposed === row.currentFtw && Boolean(row.currentFtw) ? "active" : ""}
                type="button"
                disabled={disabled || !canEdit || !row.currentFtw}
                onClick={onKeepFtw}
                title={!row.currentFtw ? "No current FT Williams value is available to keep." : "Keep the current FT Williams value."}
              >
                Keep Current
              </button>
            </>
          )}
        </div>
      </td>
      <td>
        <span className={`review-issue-pill ${issueClass}`}>{row.statusLabel}</span>
        <small className={row.failedByFtw ? "ftw-row-error" : undefined}>{row.ftwFailureReason || row.issue}</small>
      </td>
    </tr>
  );
}

function FullFieldReviewDrawer({
  activeTab,
  contractTypeFilter,
  currentPage,
  disabled,
  displayRows,
  formFilter,
  onAccept,
  onClose,
  onInspect,
  onKeepFtw,
  onPageChange,
  onResetFilters,
  onRowsPerPageChange,
  onSearchChange,
  onTabChange,
  pagedRows,
  pageEnd,
  pageStart,
  priorityFilter,
  reviewRows,
  rowsPerPage,
  savingFieldId,
  search,
  sectionFilter,
  sectionOptions,
  selectedFieldId,
  setContractTypeFilter,
  setFormFilter,
  setPriorityFilter,
  setSectionFilter,
  setStatusFilter,
  statusFilter,
  totalFields,
  totalPages,
  willUpdateRows,
}: {
  activeTab: ReviewTab;
  contractTypeFilter: ContractTypeFilter;
  currentPage: number;
  disabled: boolean;
  displayRows: ReviewDecisionRow[];
  formFilter: FilterValue;
  onAccept: (row: ReviewDecisionRow) => void;
  onClose: () => void;
  onInspect: (row: ReviewDecisionRow) => void;
  onKeepFtw: (row: ReviewDecisionRow) => void;
  onPageChange: (page: number | ((page: number) => number)) => void;
  onResetFilters: () => void;
  onRowsPerPageChange: (rows: number) => void;
  onSearchChange: (value: string) => void;
  onTabChange: (tab: ReviewTab) => void;
  pagedRows: ReviewDecisionRow[];
  pageEnd: number;
  pageStart: number;
  priorityFilter: FilterValue;
  reviewRows: ReviewDecisionRow[];
  rowsPerPage: number;
  savingFieldId: string | null;
  search: string;
  sectionFilter: FilterValue;
  sectionOptions: string[];
  selectedFieldId: string | null;
  setContractTypeFilter: (value: ContractTypeFilter) => void;
  setFormFilter: (value: FilterValue) => void;
  setPriorityFilter: (value: FilterValue) => void;
  setSectionFilter: (value: FilterValue) => void;
  setStatusFilter: (value: FilterValue) => void;
  statusFilter: FilterValue;
  totalFields: number;
  totalPages: number;
  willUpdateRows: ReviewDecisionRow[];
}) {
  const drawerRef = useRef<HTMLElement | null>(null);
  const actionRequiredCount = reviewRows.filter(isActionRequiredRow).length;
  useDialogFocus(true, drawerRef, onClose);
  return (
    <div className="field-drawer-backdrop" role="presentation">
      <section ref={drawerRef} tabIndex={-1} className="field-drawer" role="dialog" aria-modal="true" aria-label="All fields review table">
        <header className="field-drawer-header">
          <div>
            <span className="eyebrow">Full Field Review</span>
            <h2>All fields prepared for FT Williams</h2>
            <p>Use filters, search, and pagination without crowding the main review page.</p>
          </div>
          <button className="button secondary" type="button" onClick={onClose}>
            <X size={16} /> Close
          </button>
        </header>

        <div className="approval-count-tabs drawer-tabs">
          <ReviewCountTab active={activeTab === "NEEDS_DECISION"} icon={<AlertTriangle size={15} />} label="Action Required" count={actionRequiredCount} onClick={() => onTabChange("NEEDS_DECISION")} />
          <ReviewCountTab active={activeTab === "WILL_UPDATE"} label="Will Update FTW" count={willUpdateRows.length} onClick={() => onTabChange("WILL_UPDATE")} />
          <ReviewCountTab active={activeTab === "ALL"} icon={<ListChecks size={15} />} label="All Fields" count={reviewRows.length || totalFields} onClick={() => onTabChange("ALL")} />
        </div>

        <div className="field-drawer-controls">
          <label className="table-search">
            <Search size={16} />
            <input value={search} onChange={(event) => onSearchChange(event.target.value)} placeholder="Search fields..." />
          </label>
          <SelectFilter label="Form" value={formFilter} onChange={setFormFilter} options={["SCHEDULE_A", "FORM_5500"]} />
          <SelectFilter label="Contract" value={contractTypeFilter} onChange={(value) => setContractTypeFilter(value as ContractTypeFilter)} options={["EXPERIENCE_RATED", "NONEXPERIENCE_RATED", "NEEDS_REVIEW", "UNKNOWN"]} />
          <SelectFilter label="Status" value={statusFilter} onChange={setStatusFilter} options={["NEEDS_DECISION", "WILL_UPDATE", "SAME", "MISSING", "LOW_CONFIDENCE"]} />
          <SelectFilter label="Priority" value={priorityFilter} onChange={setPriorityFilter} options={["HIGH", "MEDIUM", "LOW"]} />
          <SelectFilter label="Section" value={sectionFilter} onChange={setSectionFilter} options={sectionOptions} />
          <button className="button secondary table-filter-button" onClick={onResetFilters}>
            <SlidersHorizontal size={16} /> Reset
          </button>
        </div>

        <div className="field-drawer-table">
          <table className="approval-decision-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Extracted</th>
                <th>Current FTW</th>
                <th>Proposed To Send</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {pagedRows.map((row) => (
                <ReviewDecisionTableRow
                  key={row.key}
                  row={row}
                  selected={row.fieldId === selectedFieldId}
                  onInspect={() => onInspect(row)}
                  onAccept={() => onAccept(row)}
                  onKeepFtw={() => onKeepFtw(row)}
                  disabled={disabled}
                  saving={savingFieldId === row.fieldId}
                />
              ))}
            </tbody>
          </table>
          {!displayRows.length ? (
            <div className="empty-state"><SearchX size={18} /> No fields match this view.</div>
          ) : null}
        </div>

        <footer className="field-table-footer drawer-footer">
          <span>Showing {pageStart}-{pageEnd} of {displayRows.length} fields</span>
          <div className="field-pagination">
            <label>
              Rows
              <select value={rowsPerPage} onChange={(event) => onRowsPerPageChange(Number(event.target.value))}>
                <option value={10}>10</option>
                <option value={15}>15</option>
                <option value={25}>25</option>
                <option value={50}>50</option>
              </select>
            </label>
            <button className="button secondary" disabled={currentPage === 1} onClick={() => onPageChange((page) => Math.max(1, page - 1))}>Previous</button>
            {paginationItems(currentPage, totalPages).map((item, index) => (
              item === "..." ? (
                <span key={`${item}-${index}`} className="pagination-ellipsis">...</span>
              ) : (
                <button
                  key={item}
                  className={`pagination-page ${item === currentPage ? "active" : ""}`}
                  onClick={() => onPageChange(item)}
                >
                  {item}
                </button>
              )
            ))}
            <button className="button secondary" disabled={currentPage === totalPages} onClick={() => onPageChange((page) => Math.min(totalPages, page + 1))}>Next</button>
          </div>
        </footer>
      </section>
    </div>
  );
}

function ClientErrorBanner({ error }: { error: ClientFacingError }) {
  const isWarning = error.severity === "warning";
  return (
    <section className={`client-error-banner ${isWarning ? "warning" : ""}`}>
      <div className="client-error-icon">
        <AlertTriangle size={20} />
      </div>
      <div>
        <div className="client-error-heading">
          <strong>{error.title}</strong>
          {error.source ? <span>{error.source}</span> : null}
        </div>
        <p>{error.message}</p>
        {error.reason ? <small>Reason: {error.reason}</small> : null}
        {error.next_action ? <small>Next step: {error.next_action}</small> : null}
        <RejectedFieldsList fields={error.rejected_fields || []} />
      </div>
    </section>
  );
}

function RejectedFieldsList({ fields }: { fields: ClientRejectedField[] }) {
  if (!fields.length) return null;
  return (
    <div className="ftw-rejected-fields">
      {fields.map((field, index) => (
        <div className="ftw-rejected-field-card" key={`${field.tag}-${field.field_id || index}`}>
          <div>
            <strong>{field.label || "FT Williams field"}</strong>
            <span>Needs correction</span>
          </div>
          {field.reason ? <small>{field.reason}</small> : null}
        </div>
      ))}
    </div>
  );
}

function FTWilliamsFailureBanner({
  busy,
  canRetry,
  error,
  review,
  onEditFields,
  onQueryCurrent,
  onRetry,
}: {
  busy: boolean;
  canRetry: boolean;
  error: ClientFacingError | null;
  review: FTWilliamsReview | null;
  onEditFields: () => void;
  onQueryCurrent: () => void;
  onRetry: () => void;
}) {
  const confirmed = review?.update_confirmed_count || 0;
  const remaining = review?.update_remaining_count || 0;
  const partial = confirmed > 0 && remaining > 0;
  const rejectedLabels = (error?.rejected_fields || []).map((field) => field.label).filter(Boolean);
  const message = confirmed
    ? `${confirmed} field${confirmed === 1 ? "" : "s"} updated; ${remaining} still need${remaining === 1 ? "s" : ""} correction.`
    : rejectedLabels.length
      ? `${rejectedLabels.join(", ")} ${rejectedLabels.length === 1 ? "needs" : "need"} correction before retrying.`
      : error?.message || "FT Williams could not verify the remaining update.";
  return (
    <section className={`ftw-failure-banner${partial ? " partial" : ""}`} role={partial ? "status" : "alert"}>
      <div className="ftw-failure-banner-icon">
        <AlertTriangle size={22} />
      </div>
      <div className="ftw-failure-banner-copy">
        <h2>{partial ? "FT Williams partially updated" : "FT Williams needs attention"}</h2>
        <p>{message}</p>
      </div>
      <div className="ftw-failure-banner-actions">
        <button className="button" type="button" disabled={busy || !canRetry} onClick={onRetry}>
          <ShieldCheck size={16} /> Retry Remaining
        </button>
        <button className="button secondary" type="button" disabled={busy} onClick={onQueryCurrent}>
          <Search size={16} /> Refresh FTW
        </button>
        <button className="button secondary" type="button" onClick={onEditFields}>
          <Edit3 size={16} /> Review Fields
        </button>
      </div>
    </section>
  );
}

function clientErrorFromRaw(message: string, source: string): ClientFacingError | null {
  const text = message.trim();
  const lower = text.toLowerCase();
  if (lower.includes("maximum of 5000000 ingested tokens") || lower.includes("subscription limits")) {
    return {
      title: "Extractor token limit reached",
      message: "GroundX rejected extraction because the monthly ingest token limit has been reached.",
      next_action: "Use a different GroundX bucket/key or upgrade/reset the GroundX quota, then retry extraction.",
      severity: "error",
      source,
      technical_details: text,
    };
  }
  if (lower.includes("getaddrinfo") || lower.includes("failed to resolve") || lower.includes("max retries exceeded")) {
    return {
      title: "External service connection failed",
      message: "The app could not reach one of the external services needed for this step.",
      next_action: "Check network access, service credentials, and retry the action.",
      severity: "error",
      source,
      technical_details: text,
    };
  }
  if (lower.includes("sharefile") && (lower.includes("webhook") || lower.includes("register"))) {
    return {
      title: "ShareFile upload event was not received",
      message: "The folder upload was not delivered to the backend webhook.",
      next_action: "Confirm the webhook is registered and online, then re-upload or run folder discovery.",
      severity: "warning",
      source,
      technical_details: text,
    };
  }
  return null;
}

function ApproveConfirmationModal({
  blockers,
  hasBlockers,
  onApprove,
  onClose,
  onReviewFields,
  unresolvedRows,
}: {
  blockers: {
    highPriorityMissing: number;
    lowConfidence: number;
    needsDecision: number;
    unmapped: number;
    willKeepFtw: number;
    willUpdate: number;
  };
  hasBlockers: boolean;
  onApprove: () => void;
  onClose: () => void;
  onReviewFields: () => void;
  unresolvedRows: ReviewDecisionRow[];
}) {
  const previewRows = unresolvedRows.slice(0, 3);
  const dialogRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, dialogRef, onClose);
  return (
    <div className="modal-backdrop approve-confirm-backdrop" role="presentation">
      <section ref={dialogRef} tabIndex={-1} className="approve-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="approve-confirm-title">
        <header className="approve-confirm-header">
          <div>
            <span className="eyebrow">{hasBlockers ? "Approval override" : "Approval confirmation"}</span>
            <h2 id="approve-confirm-title">{hasBlockers ? "Approve with unresolved items?" : "Approve this filing?"}</h2>
            <p>
              {hasBlockers
                ? "This will mark the filing approved even though unresolved fields remain. FT Williams sending will still stay locked unless the current data, Schedule A match, and safe XML checks are complete."
                : "This will mark the filing approved. FT Williams sending will unlock only when the current data, Schedule A match, and safe XML checks are complete."}
            </p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close approval confirmation">
            <X size={18} />
          </button>
        </header>

        <div className="approve-confirm-stats">
          <ApprovalModalStat label="Needs decision" value={blockers.needsDecision} tone="warn" />
          <ApprovalModalStat label="High-priority missing" value={blockers.highPriorityMissing} tone="danger" />
          <ApprovalModalStat label="Unmapped" value={blockers.unmapped} tone="danger" />
          <ApprovalModalStat label="Low confidence" value={blockers.lowConfidence} tone="warn" />
          <ApprovalModalStat label="Will update" value={blockers.willUpdate} tone="ready" />
          <ApprovalModalStat label="Ready / keep FTW" value={blockers.willKeepFtw} tone="info" />
        </div>

        <div className={`approve-confirm-warning ${hasBlockers ? "" : "ready"}`}>
          {hasBlockers ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}
          <span>
            <strong>{hasBlockers ? "Approving does not mean every missing field will be sent." : "Review the prepared values before approving."}</strong>
            <small>
              {hasBlockers
                ? "Fields without a proposed value will remain unchanged or excluded from the FT Williams update payload."
                : "Only approved proposed values are prepared for the FT Williams update payload."}
            </small>
          </span>
        </div>

        <div className="approve-confirm-table-wrap">
          <div className="approve-confirm-table-head">
            <strong>Highest-priority unresolved fields</strong>
            <span>Showing {previewRows.length} of {unresolvedRows.length}</span>
          </div>
          <table className="approve-confirm-table">
            <thead>
              <tr>
                <th>Field</th>
                <th>Extracted</th>
                <th>Current FTW</th>
                <th>Proposed</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {previewRows.map((row) => (
                <tr key={row.key}>
                  <td>
                    <strong>{row.label}</strong>
                    <small>{row.formLabel} / {row.section}</small>
                  </td>
                  <td>{row.extracted || <span className="muted-value">Not found</span>}</td>
                  <td>{row.currentFtw || <span className="muted-value">No current value</span>}</td>
                  <td>{row.proposed || <span className="muted-value">No proposed value</span>}</td>
                  <td><span className={`review-issue-pill issue-${row.group.toLowerCase()}`}>{row.statusLabel}</span></td>
                </tr>
              ))}
              {!previewRows.length ? (
                <tr>
                  <td colSpan={5}>No unresolved fields remain.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <footer className="approve-confirm-actions">
          <button className="button secondary" type="button" onClick={onClose}>Cancel</button>
          <button className="button secondary" type="button" onClick={onReviewFields}>
            <Eye size={16} /> Review Fields
          </button>
          <button className={hasBlockers ? "button button-warn" : "button"} type="button" onClick={onApprove}>
            <CheckCircle2 size={16} /> {hasBlockers ? "Approve with unresolved fields" : "Approve filing"}
          </button>
        </footer>
      </section>
    </div>
  );
}

function UnapproveConfirmationModal({
  filingName,
  onClose,
  onConfirm,
}: {
  filingName: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const dialogRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, dialogRef, onClose);
  return (
    <div className="modal-backdrop approve-confirm-backdrop" role="presentation">
      <section ref={dialogRef} tabIndex={-1} className="approve-confirm-modal unapprove-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="unapprove-confirm-title">
        <header className="approve-confirm-header">
          <div>
            <span className="eyebrow">Approval status</span>
            <h2 id="unapprove-confirm-title">Remove approval?</h2>
            <p>
              This will move the filing out of approved status and lock FT Williams sending until the filing is approved again.
            </p>
          </div>
          <button className="icon-button" type="button" onClick={onClose} aria-label="Close unapprove confirmation">
            <X size={18} />
          </button>
        </header>

        <div className="unapprove-confirm-card">
          <span><Ban size={18} /></span>
          <div>
            <strong>{filingName}</strong>
            <small>Field decisions and prepared FT Williams data are kept. Only the approval state changes.</small>
          </div>
        </div>

        <footer className="approve-confirm-actions">
          <button className="button secondary" type="button" onClick={onClose}>Keep approved</button>
          <button className="button danger" type="button" onClick={onConfirm}>
            <Ban size={16} /> Unapprove filing
          </button>
        </footer>
      </section>
    </div>
  );
}

function ApprovalModalStat({ label, tone, value }: { label: string; tone: "danger" | "info" | "ready" | "warn"; value: number }) {
  return (
    <div className={`approve-confirm-stat stat-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReadinessStep({
  active,
  detail,
  done,
  failed,
  label,
  locked,
}: {
  active?: boolean;
  detail: string;
  done: boolean;
  failed?: boolean;
  label: string;
  locked?: boolean;
}) {
  return (
    <div className={`readiness-step ${done ? "done" : ""} ${active ? "active" : ""} ${failed ? "failed" : ""} ${locked ? "locked" : ""}`}>
      <span>{done ? <Check size={14} /> : failed ? <X size={14} /> : locked ? <Ban size={14} /> : <AlertTriangle size={14} />}</span>
      <div>
        <strong>{label}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

function ScheduleAWorksheetSummaryPanel({ summaries }: { summaries: ScheduleAWorksheetSummary[] }) {
  if (!summaries.length) return null;
  return (
    <section className="schedule-a-source-panel card">
      <div className="schedule-a-source-head">
        <PanelHeading icon={<FileText size={16} />} title="Worksheet source summary" />
        <span>{summaries.length} source{summaries.length === 1 ? "" : "s"}</span>
      </div>
      <div className="schedule-a-source-grid">
        {summaries.map((summary, index) => (
          <div className="schedule-a-source-block" key={`${summary.source}-${summary.account_number || index}`}>
            <div className="schedule-a-source-meta">
              <strong>{summary.source || "Schedule A worksheet"}</strong>
              <span>{[summary.coverage, summary.account_number, summary.period_begin && summary.period_end ? `${summary.period_begin} - ${summary.period_end}` : null].filter(Boolean).join(" / ")}</span>
              <small>{[summary.carrier_name, summary.ein, summary.naic_code ? `NAIC ${summary.naic_code}` : null].filter(Boolean).join(" | ")}</small>
            </div>
            <dl>
              {(summary.values || []).map((item) => (
                <div key={`${item.label}-${item.coverage || ""}-${item.source || ""}`}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
            {summary.benefit_rows?.length ? (
              <div className="schedule-a-source-table-wrap">
                <table className="schedule-a-source-table">
                  <thead>
                    <tr>
                      <th>Benefit</th>
                      <th>Persons</th>
                      <th>Premium</th>
                      <th>Page</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.benefit_rows.map((row, rowIndex) => (
                      <tr key={`${row.benefit_type}-${row.source_page || rowIndex}`}>
                        <td>{row.benefit_type}</td>
                        <td>{row.persons_covered || "-"}</td>
                        <td>{row.premium || "-"}</td>
                        <td>{row.source_page || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function ScheduleABrokerRowsPanel({
  busy,
  matches,
  onConfirm,
  rows,
}: {
  busy: boolean;
  matches: ScheduleABrokerMatch[];
  onConfirm: (extractedIndex: number, ftwIndex?: number, createNew?: boolean) => void;
  rows: ScheduleABrokerRow[];
}) {
  if (!rows.length) return null;
  return (
    <section className="schedule-a-broker-panel card">
      <div className="schedule-a-broker-panel-head">
        <PanelHeading icon={<ListChecks size={16} />} title="Schedule A broker rows" />
        <span>{rows.length} extracted</span>
      </div>
      <div className="schedule-a-broker-table-wrap">
        <table className="schedule-a-broker-table">
          <thead>
            <tr>
              <th>Broker / person</th>
              <th>Address</th>
              <th>Org</th>
              <th>Commissions</th>
              <th>Fees</th>
              <th>FT Williams row</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const match = matches.find((candidate) => candidate.extracted_index === index);
              return (
              <tr key={`${row.name}-${row.zip_code || ""}-${index}`}>
                <td>{row.name}</td>
                <td>{formatBrokerAddress(row)}</td>
                <td>{row.organization_code || "-"}</td>
                <td>{row.commission_total || "0"}</td>
                <td>{row.fee_total || "0"}</td>
                <td>
                  {match?.resolved ? (
                    <span className="broker-match-status broker-match-ready">
                      {match.status === "CONFIRMED_NEW" ? "New broker row" : `Matched to row ${(match.ftw_index ?? 0) + 1}`}
                    </span>
                  ) : match ? (
                    <BrokerMatchDecision match={match} busy={busy} onConfirm={onConfirm} />
                  ) : (
                    <span className="broker-match-status">Load current FTW data to match</span>
                  )}
                  {match?.reason ? <small className="broker-match-reason">{match.reason}</small> : null}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {rows.length > 1 ? (
        <p>Each extracted broker is matched separately. Existing unmatched FT Williams broker rows are preserved.</p>
      ) : null}
    </section>
  );
}

function BrokerMatchDecision({
  busy,
  match,
  onConfirm,
}: {
  busy: boolean;
  match: ScheduleABrokerMatch;
  onConfirm: (extractedIndex: number, ftwIndex?: number, createNew?: boolean) => void;
}) {
  const candidates = match.candidate_ftw_indexes || [];
  const [selected, setSelected] = useState(candidates[0]?.toString() || "");
  return (
    <div className="broker-match-decision">
      {candidates.length ? (
        <>
          <select value={selected} onChange={(event) => setSelected(event.target.value)} disabled={busy} aria-label="FT Williams broker row">
            {candidates.map((index) => <option key={index} value={index}>FT Williams row {index + 1}</option>)}
          </select>
          <button className="button secondary" type="button" disabled={busy || selected === ""} onClick={() => onConfirm(match.extracted_index, Number(selected), false)}>Match row</button>
        </>
      ) : null}
      <button className="button secondary" type="button" disabled={busy} onClick={() => onConfirm(match.extracted_index, undefined, true)}>Add as new</button>
    </div>
  );
}

function formatBrokerAddress(row: ScheduleABrokerRow) {
  return [
    row.address_line_1,
    row.address_line_2,
    [row.city, row.state].filter(Boolean).join(", "),
    row.zip_code,
  ]
    .filter(Boolean)
    .join(" ");
}

function FTWilliamsComparisonPanel({
  busy,
  canSendUpdate,
  onPreparePreview,
  onQueryCurrent,
  onSaveManualMatch,
  onSelectScheduleMatch,
  onSendUpdate,
  review,
  sendBusy,
}: {
  busy: boolean;
  canSendUpdate: boolean;
  onPreparePreview: () => void;
  onQueryCurrent: () => void;
  onSaveManualMatch: (payload: {
    customer_id?: string;
    plan_id?: string;
    ftw_customer_id?: string;
    ftw_plan_id?: string;
    year?: string;
  }) => void;
  onSelectScheduleMatch: (payload: {
    ftw_seq_no?: string;
    carrier?: string;
    carrier_ein?: string;
    contract?: string;
    create_new?: boolean;
    schedule_desc?: string;
  }) => void;
  onSendUpdate: () => void;
  review: FTWilliamsReview | null;
  sendBusy: boolean;
}) {
  const changedFields = review?.fields.filter((field) => field.changed && field.update_included) ?? [];
  const lookup = review?.plan_lookup || null;
  const clientError = review?.active_failure_client_error || review?.client_error || null;
  const rawError = review?.active_failure_reason || review?.error_message || lookup?.error_message || null;
  const scheduleMatch = formatScheduleAMatch(review?.schedule_a_match);
  const currentQueryYear = formatCurrentQueryYear(review);
  const scheduleCandidates = review?.schedule_a_candidates ?? [];
  const updateSent = review?.status === "UPDATE_SENT";
  const updateFailed = Boolean(review?.active_failure || review?.status === "UPDATE_FAILED" || review?.status === "UPDATE_UNKNOWN");
  const [manualMatch, setManualMatch] = useState({
    customer_id: "",
    plan_id: "",
    ftw_customer_id: "",
    ftw_plan_id: "",
    year: "",
  });
  const [scheduleSelection, setScheduleSelection] = useState({
    ftw_seq_no: "",
    carrier: "",
    carrier_ein: "",
    contract: "",
  });

  useEffect(() => {
    const identity = lookup?.matched_identity || {};
    setManualMatch({
      customer_id: textValue(identity.customer_id) || textValue(review?.customer_id),
      plan_id: textValue(identity.plan_id) || textValue(review?.plan_id),
      ftw_customer_id: textValue(identity.ftw_customer_id) || textValue(review?.ftw_customer_id),
      ftw_plan_id: textValue(identity.ftw_plan_id) || textValue(review?.ftw_plan_id),
      year: textValue(lookup?.year) || textValue(review?.year),
    });
    setScheduleSelection({
      ftw_seq_no: textValue(review?.schedule_a_match?.ftw_seq_no) || textValue(review?.ftw_seq_no),
      carrier: textValue(review?.schedule_a_match?.carrier),
      carrier_ein: textValue(review?.schedule_a_match?.carrier_ein),
      contract: textValue(review?.schedule_a_match?.contract),
    });
  }, [lookup?.matched_identity, lookup?.year, review?.customer_id, review?.ftw_customer_id, review?.ftw_plan_id, review?.ftw_seq_no, review?.id, review?.plan_id, review?.schedule_a_match, review?.updated_at, review?.year]);

  function submitManualMatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSaveManualMatch(cleanPayload(manualMatch));
  }

  function submitScheduleMatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scheduleSelection.ftw_seq_no.trim()) return;
    const payload = cleanPayload(scheduleSelection);
    onSelectScheduleMatch({
      ftw_seq_no: scheduleSelection.ftw_seq_no.trim(),
      carrier: payload.carrier,
      carrier_ein: payload.carrier_ein,
      contract: payload.contract,
    });
  }

  function applyScheduleCandidate(candidate: Record<string, unknown>) {
    const nextSelection = {
      ftw_seq_no: textValue(candidate.ftw_seq_no),
      carrier: textValue(candidate.carrier),
      carrier_ein: textValue(candidate.carrier_ein),
      contract: textValue(candidate.contract),
    };
    setScheduleSelection(nextSelection);
    if (!nextSelection.ftw_seq_no.trim() || busy) return;
    onSelectScheduleMatch({
      ftw_seq_no: nextSelection.ftw_seq_no.trim(),
      carrier: nextSelection.carrier || undefined,
      carrier_ein: nextSelection.carrier_ein || undefined,
      contract: nextSelection.contract || undefined,
    });
  }

  function addUploadedScheduleAsNew() {
    if (busy) return;
    onSelectScheduleMatch({
      carrier: scheduleSelection.carrier || undefined,
      carrier_ein: scheduleSelection.carrier_ein || undefined,
      contract: scheduleSelection.contract || undefined,
      create_new: true,
    });
  }

  return (
    <details className="review-xml card ftw-technical-panel">
      <summary>
        <span><ChevronDown size={16} /> FT Williams details and fallback controls</span>
        <em>{changedFields.length} update field{changedFields.length === 1 ? "" : "s"} prepared</em>
      </summary>
      <div className="ftw-technical-content">
        <div className="ftw-comparison-head">
          <div>
            <span className="eyebrow">FT Williams Status</span>
            <h2>Current data and match controls</h2>
            <p>
              {review?.current_query_success
                ? `${changedFields.length} proposed field${changedFields.length === 1 ? "" : "s"} differ from current FT Williams data.`
                : "Query FT Williams current data before sending approved updates."}
            </p>
          </div>
          <div className="ftw-comparison-actions">
            <span className={`field-status ${review?.current_query_success ? "status-matched" : "status-low_confidence"}`}>
              {review?.status?.replaceAll("_", " ") || "Not prepared"}
            </span>
            <button className="button secondary" type="button" disabled={busy} onClick={onPreparePreview}>
              <RefreshCw size={16} /> Preview
            </button>
            <button className="button" type="button" disabled={busy} onClick={onQueryCurrent}>
              <Search size={16} /> Query FTW Current
            </button>
            <button className="button" type="button" disabled={busy || !canSendUpdate} onClick={onSendUpdate}>
              <ShieldCheck size={16} /> {sendBusy ? "Sending..." : "Send Approved XML"}
            </button>
          </div>
        </div>

        {updateSent || updateFailed ? (
          <div className={`ftw-update-result ${updateSent ? "success" : "error"}`}>
            {updateSent ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
            <span>
              <strong>{updateSent ? "FT Williams update completed" : "Last FT Williams update failed"}</strong>
              <small>
                {updateSent
                  ? `Approved data was sent and verified in FT Williams${review?.updated_at ? ` on ${formatDate(review.updated_at)}` : ""}.`
                  : `Review the error details and retry${review?.updated_at ? ` after ${formatDate(review.updated_at)}` : ""}.`}
              </small>
            </span>
          </div>
        ) : null}

        {clientError || rawError ? (
          <div className="ftw-warning">
            <AlertTriangle size={17} />
            <span>
              <strong>{clientError?.title || "FT Williams action needs attention"}</strong>
              <small>{clientError?.message || rawError}</small>
              {clientError?.next_action ? <small>Next step: {clientError.next_action}</small> : null}
              <FTWilliamsDiagnostic
                errorCode={clientError?.code}
                editCheckIssues={
                  review?.edit_check_final_issues?.length
                    ? review.edit_check_final_issues
                    : review?.edit_check_baseline_issues
                }
                message={rawError}
                operations={review?.update_diagnostics}
                technicalDetails={clientError?.technical_details}
              />
            </span>
          </div>
        ) : null}

        <div className="ftw-meta-grid">
          <FTWMeta label="Plan Lookup" value={lookup ? formatFtwLookupStatus(lookup.status) : "Not prepared"} />
          <FTWMeta
            label="Extracted EIN / PN"
            value={lookup?.company_employer_id && lookup?.plan_number ? `${lookup.company_employer_id} / ${lookup.plan_number}` : "Pending"}
          />
          <FTWMeta label="Lookup Year" value={lookup?.year || review?.year || "Pending"} />
          <FTWMeta label="Current Query Year" value={currentQueryYear} />
          <FTWMeta label="Lookup Matches" value={String(lookup?.matches?.length ?? 0)} />
          <FTWMeta label="Configured" value={review?.configured ? "Yes" : "No"} />
          <FTWMeta label="Current Query" value={review?.current_query_success ? "Successful" : review?.current_query_sent ? "Attempted" : "Not sent"} />
          <FTWMeta label="Customer / Plan" value={review?.customer_id && review?.plan_id ? `${review.customer_id} / ${review.plan_id}` : "Pending"} />
          <FTWMeta label="FTW IDs" value={review?.ftw_customer_id && review?.ftw_plan_id ? `${review.ftw_customer_id} / ${review.ftw_plan_id}` : "Pending"} />
          <FTWMeta label="Schedule A Match" value={scheduleMatch} />
        </div>

        <div className="ftw-fallback-grid">
          <form className="ftw-fallback-form" onSubmit={submitManualMatch}>
            <h3>Plan Match</h3>
            <div className="ftw-fallback-inputs">
              <label>
                <span>CustomerID</span>
                <input className="input" value={manualMatch.customer_id} onChange={(event) => setManualMatch((value) => ({ ...value, customer_id: event.target.value }))} />
              </label>
              <label>
                <span>PlanID</span>
                <input className="input" value={manualMatch.plan_id} onChange={(event) => setManualMatch((value) => ({ ...value, plan_id: event.target.value }))} />
              </label>
              <label>
                <span>FTWCustomerID</span>
                <input className="input" value={manualMatch.ftw_customer_id} onChange={(event) => setManualMatch((value) => ({ ...value, ftw_customer_id: event.target.value }))} />
              </label>
              <label>
                <span>FTWPlanID</span>
                <input className="input" value={manualMatch.ftw_plan_id} onChange={(event) => setManualMatch((value) => ({ ...value, ftw_plan_id: event.target.value }))} />
              </label>
              <label>
                <span>Year</span>
                <input className="input" value={manualMatch.year} onChange={(event) => setManualMatch((value) => ({ ...value, year: event.target.value }))} />
              </label>
            </div>
            <button className="button secondary" type="submit" disabled={busy}>
              <CheckCircle2 size={16} /> Save FTW Match
            </button>
          </form>

          <form className="ftw-fallback-form" onSubmit={submitScheduleMatch}>
            <h3>Schedule A Match</h3>
            <div className="ftw-fallback-inputs">
              <label>
                <span>FTWSeqNo</span>
                <input className="input" value={scheduleSelection.ftw_seq_no} onChange={(event) => setScheduleSelection((value) => ({ ...value, ftw_seq_no: event.target.value }))} />
              </label>
              <label>
                <span>Carrier</span>
                <input className="input" value={scheduleSelection.carrier} onChange={(event) => setScheduleSelection((value) => ({ ...value, carrier: event.target.value }))} />
              </label>
              <label>
                <span>Carrier EIN</span>
                <input className="input" value={scheduleSelection.carrier_ein} onChange={(event) => setScheduleSelection((value) => ({ ...value, carrier_ein: event.target.value }))} />
              </label>
              <label>
                <span>Contract</span>
                <input className="input" value={scheduleSelection.contract} onChange={(event) => setScheduleSelection((value) => ({ ...value, contract: event.target.value }))} />
              </label>
            </div>
            <button className="button secondary" type="submit" disabled={busy || !scheduleSelection.ftw_seq_no.trim()}>
              <CheckCircle2 size={16} /> Select Schedule A
            </button>
            <button className="button secondary" type="button" disabled={busy || !review?.current_query_success} onClick={addUploadedScheduleAsNew}>
              <Plus size={16} /> Add as New Schedule A
            </button>
          </form>
        </div>

        {scheduleCandidates.length ? (
          <div className="ftw-schedule-candidates">
            <div className="ftw-schedule-candidates-head">
              <div>
                <h3>FTW Schedule A Candidates</h3>
                <p>Select the FTW schedule that matches the uploaded Schedule A.</p>
              </div>
            </div>
            <div className="ftw-schedule-candidate-list">
              {scheduleCandidates.map((candidate) => {
                const seq = textValue(candidate.ftw_seq_no);
                const selected = seq && seq === scheduleSelection.ftw_seq_no.trim();
                const description = textValue(candidate.description);
                const score = textValue(candidate.score);
                const hasCurrentData = Boolean(candidate.has_current_data);
                const planYearBegin = textValue(candidate.plan_year_begin);
                const planYearEnd = textValue(candidate.plan_year_end);
                return (
                  <button
                    key={seq || JSON.stringify(candidate)}
                    className={`ftw-schedule-candidate ${selected ? "selected" : ""}`}
                    type="button"
                    disabled={busy}
                    onClick={() => applyScheduleCandidate(candidate)}
                  >
                    <strong>{seq ? `Seq ${seq}` : "Candidate"}{description ? ` / ${description}` : ""}</strong>
                    <span>{textValue(candidate.carrier) || "Carrier unavailable"}</span>
                    <small>{textValue(candidate.contract) ? `Contract ${textValue(candidate.contract)}` : "No contract"}</small>
                    {planYearBegin || planYearEnd ? <small>{[planYearBegin, planYearEnd].filter(Boolean).join(" - ")}</small> : null}
                    <em className={hasCurrentData ? "candidate-data-ready" : "candidate-data-weak"}>
                      {hasCurrentData ? `Current data loaded${score ? ` / score ${score}` : ""}` : "Details unavailable. Select only if this is the FTW schedule shown in FT Williams."}
                    </em>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>
    </details>
  );
}

function textValue(value: unknown) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

function cleanPayload<T extends Record<string, string>>(payload: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(payload)
      .map(([key, value]) => [key, value.trim()])
      .filter(([, value]) => value),
  ) as Partial<T>;
}

function formatScheduleAMatch(match?: Record<string, unknown> | null) {
  if (!match) return "Pending";
  const isNew = Boolean(match.create_new);
  const seq = typeof match.ftw_seq_no === "string" || typeof match.ftw_seq_no === "number" ? String(match.ftw_seq_no) : "";
  const carrier = typeof match.carrier === "string" ? match.carrier : "";
  const contract = typeof match.contract === "string" ? match.contract : "";
  const desc = typeof match.schedule_desc === "string" ? match.schedule_desc : "";
  const parts = [isNew ? "New Schedule A" : seq ? `Seq ${seq}` : "", desc, carrier, contract ? `Contract ${contract}` : ""].filter(Boolean);
  return parts.length ? parts.join(" / ") : "Matched";
}

function formatFtwLookupStatus(status: string) {
  if (!status) return "Not prepared";
  return status.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatCurrentQueryYear(review: FTWilliamsReview | null) {
  const comparisonYear = textValue(review?.comparison_year);
  if (!comparisonYear) return "Pending";
  return comparisonYear;
}

function FTWMeta({ label, value }: { label: string; value: string }) {
  return (
    <div className="ftw-meta">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function buildReviewDecisionRows(
  fields: ExtractedField[],
  review: FTWilliamsReview | null,
  contractType: ScheduleAContractType,
  includeExcluded: boolean,
): ReviewDecisionRow[] {
  const fieldById = new Map(fields.map((field) => [field.id, field]));
  const usedFieldIds = new Set<string>();
  const rows: ReviewDecisionRow[] = [];

  (review?.fields ?? []).forEach((comparison, index) => {
    const extractedField = comparison.field_id ? fieldById.get(comparison.field_id) : undefined;
    if (!includeExcluded && !comparisonAllowedForContractType(comparison, extractedField, contractType)) return;
    if (comparison.field_id) usedFieldIds.add(comparison.field_id);
    rows.push(rowFromComparison(comparison, extractedField, index, rejectedFieldForComparison(comparison, review)));
  });

  fields.forEach((field) => {
    if (!includeExcluded && !fieldAllowedForContractType(field, contractType)) return;
    if (field.id && usedFieldIds.has(field.id)) return;
    if (field.status === "MATCHED" || field.status === "EDITED") return;
    rows.push(rowFromExtractedField(field));
  });

  return rows.sort(compareReviewRows);
}

function mergeFieldDecisionReview(
  current: FTWilliamsReview | null | undefined,
  updated: FTWilliamsReview | null | undefined,
  fieldId: string,
): FTWilliamsReview | null {
  if (!updated) return current ?? null;
  if (!current) return updated;

  const replacement = updated.fields.find((field) => field.field_id === fieldId);
  if (!replacement) return { ...updated, fields: current.fields };

  let replaced = false;
  const fields = current.fields.map((field) => {
    if (field.field_id !== fieldId) return field;
    replaced = true;
    return replacement;
  });
  if (!replaced) fields.push(replacement);
  return { ...updated, fields };
}

function rowFromComparison(
  comparison: FTWilliamsComparisonField,
  field: ExtractedField | undefined,
  index: number,
  rejectedField?: ClientRejectedField,
): ReviewDecisionRow {
  const label = comparison.label || field?.mapped_label || field?.source_field_name || "FT Williams field";
  const group = rejectedField ? "NEEDS_DECISION" : groupForComparison(comparison, field);
  return {
    key: comparison.field_id || `${comparison.rule_key || comparison.ftw_tag || label}-${index}`,
    fieldId: comparison.field_id,
    label,
    formLabel: comparison.form_type === "FORM_5500" ? "Form 5500" : "Schedule A",
    section: field ? sectionForField(field) : sectionForLabel(label),
    sourceLabel: sourceLabelForComparison(comparison, field),
    currentFtw: comparison.current_value || "",
    extracted: comparison.extracted_value || field?.value || "",
    proposed: comparison.proposed_value || field?.proposed_value || "",
    issue: rejectedField?.reason || issueForComparison(comparison, field, group),
    statusLabel: rejectedField ? "Rejected by FTW" : reviewedStatusLabel(group, field, comparison),
    group,
    priority: comparison.priority,
    confidence: comparison.confidence,
    extractedField: field,
    failedByFtw: Boolean(rejectedField),
    ftwFailureReason: rejectedField ? rejectedFieldDescription(rejectedField) : undefined,
  };
}

function rowFromExtractedField(field: ExtractedField): ReviewDecisionRow {
  const group = groupForExtractedField(field);
  return {
    key: field.id || `${fieldRuleKey(field) || field.source_field_name}-${field.status}`,
    fieldId: field.id,
    label: field.mapped_label || field.source_field_name,
    formLabel: formLabel(field),
    section: sectionForField(field),
    sourceLabel: sourceLabelForField(field),
    currentFtw: field.ftw_current_value || "",
    extracted: field.value || "",
    proposed: field.proposed_value || "",
    issue: field.status_reason || reasonForField(field),
    statusLabel: reviewedStatusLabel(group, field),
    group,
    priority: field.priority,
    confidence: field.confidence,
    extractedField: field,
  };
}

function groupForComparison(comparison: FTWilliamsComparisonField, field?: ExtractedField): ReviewRowGroup {
  if (comparison.update_exclusion_reason?.startsWith("Managed in the Schedule A broker rows")) return "SAME";
  if (field?.status === "EDITED") return comparison.changed && comparison.update_included ? "WILL_UPDATE" : "SAME";
  if (comparison.extraction_status === "MISSING" || field?.status === "MISSING") return "MISSING";
  if (comparison.extraction_status === "LOW_CONFIDENCE" || field?.status === "LOW_CONFIDENCE") return "LOW_CONFIDENCE";
  if (comparison.extraction_status === "UNMAPPED" || field?.status === "UNMAPPED") return "NEEDS_DECISION";
  if (comparison.changed && comparison.update_included) return "WILL_UPDATE";
  if (comparison.changed) return "NEEDS_DECISION";
  return "SAME";
}

function groupForExtractedField(field: ExtractedField): ReviewRowGroup {
  if (field.status === "MISSING") return "MISSING";
  if (field.status === "LOW_CONFIDENCE") return "LOW_CONFIDENCE";
  if (field.status === "UNMAPPED") return "NEEDS_DECISION";
  return field.status === "EDITED" ? "WILL_UPDATE" : "SAME";
}

function issueForComparison(comparison: FTWilliamsComparisonField, field: ExtractedField | undefined, group: ReviewRowGroup) {
  if (field?.status === "EDITED" && group === "WILL_UPDATE") return "Reviewer confirmed this FT Williams update.";
  if (field?.status === "EDITED" && comparison.changed && !comparison.update_included) {
    if (comparison.update_exclusion_reason) return comparison.update_exclusion_reason;
    return "Reviewed successfully. This field is not supported for FT Williams updates.";
  }
  if (field?.status === "EDITED" && group === "SAME") return "Reviewer confirmed the current FT Williams value.";
  if (field?.status_reason) return field.status_reason;
  if (group === "MISSING") return "Required source value was not found.";
  if (group === "LOW_CONFIDENCE") return `Confidence ${percent(comparison.confidence)} needs review.`;
  if (group === "WILL_UPDATE") return comparison.current_value ? "Proposed value differs from current FTW." : "FTW current value is blank.";
  if (group === "NEEDS_DECISION") return "Reviewer decision required before approval.";
  return "Current FTW and proposed value match.";
}

function statusLabelForGroup(group: ReviewRowGroup) {
  if (group === "WILL_UPDATE") return "Will update";
  if (group === "MISSING") return "Missing";
  if (group === "LOW_CONFIDENCE") return "Low confidence";
  if (group === "NEEDS_DECISION") return "Review";
  return "Same";
}

function reviewedStatusLabel(group: ReviewRowGroup, field?: ExtractedField, comparison?: FTWilliamsComparisonField) {
  if (comparison?.update_exclusion_reason?.startsWith("Managed in the Schedule A broker rows")) return "Managed in broker rows";
  if (field?.status !== "EDITED") return statusLabelForGroup(group);
  if (comparison?.changed && comparison.update_exclusion_reason) return "Review only · more FTW details required";
  if (comparison?.changed && !comparison.update_included) return "Review only · not supported";
  if (group === "WILL_UPDATE") return "Resolved · will update";
  if (group === "SAME") return "Resolved · keeps FTW";
  return "Resolved";
}

function isActionRequiredRow(row: ReviewDecisionRow) {
  if (row.failedByFtw) return true;
  if (row.extractedField?.status === "EDITED") return false;
  return Boolean(
    row.group === "NEEDS_DECISION" ||
    row.group === "LOW_CONFIDENCE" ||
    (row.group === "MISSING" && row.priority === "HIGH")
  );
}

function rejectedFieldForComparison(comparison: FTWilliamsComparisonField, review: FTWilliamsReview | null): ClientRejectedField | undefined {
  const rejectedFields = review?.client_error?.rejected_fields || [];
  if (!rejectedFields.length) return undefined;
  return rejectedFields.find((field) => {
    if (field.field_id && comparison.field_id && field.field_id === comparison.field_id) return true;
    if (field.tag && comparison.ftw_tag && field.tag === comparison.ftw_tag) return true;
    return false;
  });
}

function rejectedFieldDescription(field: ClientRejectedField) {
  const details = [field.reason];
  if (field.value) details.push(`Sent: ${field.value}`);
  if (field.suggested_value) details.push(`Suggested: ${field.suggested_value}`);
  return details.filter(Boolean).join(" ");
}

function sourceLabelForComparison(comparison: FTWilliamsComparisonField, field?: ExtractedField) {
  if (comparison.source_document_type === "PLAN_WORKSHEET" || field?.source_document_type === "PLAN_WORKSHEET") return "Worksheet";
  if (comparison.source_document_type === "SCHEDULE_A" || field?.source_document_type === "SCHEDULE_A") return "Schedule A PDF";
  return "ShareFile";
}

function sourceLabelForField(field: ExtractedField) {
  if (field.source_document_type === "PLAN_WORKSHEET") return "Worksheet";
  if (field.source_document_type === "SCHEDULE_A") return "Schedule A PDF";
  return "ShareFile";
}

function sectionForLabel(label: string) {
  return sectionForField({
    id: "label-only",
    filing_id: "",
    source_field_name: label,
    normalized_field_name: label,
    priority: "LOW",
    value: "",
    proposed_value: "",
    confidence: 0,
    status: "MATCHED",
  } as ExtractedField);
}

function fieldRuleKey(field: ExtractedField) {
  return (field as ExtractedField & { mapped_rule_key?: string | null }).mapped_rule_key || "";
}

function compareReviewRows(a: ReviewDecisionRow, b: ReviewDecisionRow) {
  const groupOrder: Record<ReviewRowGroup, number> = {
    MISSING: 0,
    LOW_CONFIDENCE: 1,
    NEEDS_DECISION: 2,
    WILL_UPDATE: 3,
    SAME: 4,
  };
  const priorityOrder: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2, IGNORE: 3 };
  return (
    groupOrder[a.group] - groupOrder[b.group] ||
    (priorityOrder[a.priority] ?? 9) - (priorityOrder[b.priority] ?? 9) ||
    a.label.localeCompare(b.label)
  );
}

function FieldTableRow({ field, selected, onSelect }: { field: ExtractedField; selected: boolean; onSelect: () => void }) {
  const section = sectionForField(field);
  const title = field.mapped_label || field.source_field_name;
  const subtitle = fieldSubtitle(field, title);
  return (
    <tr className={selected ? "selected" : ""} tabIndex={0} onClick={onSelect} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(); } }}>
      <td>
        <strong>{title}</strong>
        {subtitle ? <small>{subtitle}</small> : null}
      </td>
      <td>
        <span className="form-type-cell">{formLabel(field)}</span>
        <small>{section}</small>
      </td>
      <td>{field.value || <span className="muted-value">Not found</span>}</td>
      <td>{field.proposed_value || <span className="muted-value">-</span>}</td>
      <td><span className={`field-status status-${field.status.toLowerCase()}`}>{field.status.replaceAll("_", " ")}</span></td>
      <td>
        <div className="table-confidence">
          <strong>{percent(field.confidence)}</strong>
          <div className="confidence-track"><i className={`confidence-${confidenceTone(field.confidence)}`} style={{ width: percent(field.confidence) }} /></div>
        </div>
      </td>
      <td><span className={`badge priority-${field.priority.toLowerCase()}`}>{field.priority}</span></td>
      <td>
        <button className="icon-button table-eye-button" type="button" aria-label="Inspect field" onClick={(event) => { event.stopPropagation(); onSelect(); }}>
          <Eye size={17} />
        </button>
      </td>
    </tr>
  );
}

function FieldReviewModal({
  field,
  onClose,
  onSave,
  saving,
}: {
  field: ExtractedField;
  onClose: () => void;
  onSave: (fieldId: string, proposedValue: string, options?: FieldSaveOptions) => Promise<boolean>;
  saving: boolean;
}) {
  const [draft, setDraft] = useState(field?.proposed_value ?? "");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, dialogRef, onClose);

  useEffect(() => {
    setDraft(field?.proposed_value ?? "");
  }, [field?.id, field?.proposed_value]);

  async function save(value: string, options: FieldSaveOptions = {}) {
    const saved = await onSave(field.id, value, options);
    if (saved) onClose();
  }

  return (
    <div className="field-modal-layer" role="presentation">
      <button className="field-modal-scrim" type="button" onClick={onClose} aria-label="Close field review" />
      <section ref={dialogRef} tabIndex={-1} className="field-review-modal card" role="dialog" aria-modal="true" aria-label="Field review details">
        <div className="field-review-modal-head">
          <div>
            <span className="eyebrow">Field Review</span>
            <h2>{field.mapped_label || field.source_field_name}</h2>
            <div className="field-review-pills">
              <span className={`badge priority-${field.priority.toLowerCase()}`}>{priorityLabel(field.priority)}</span>
              <span className={`field-status status-${field.status.toLowerCase()}`}>{field.status.replaceAll("_", " ")}</span>
              <span>{formLabel(field)}</span>
              <span>{sectionForField(field)}</span>
            </div>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close field review modal"><X size={18} /></button>
        </div>

        <div className="field-review-modal-grid field-review-modal-grid-simple">
          <div className="inspector-block field-review-values-block">
            <div className="field-review-section-heading">
              <div>
                <h3>Value comparison</h3>
                <p>Confirm the extracted value before it is prepared for FT Williams.</p>
              </div>
            </div>
            <div className="modal-values-grid">
              <label className="field-review-value-card field-review-value-source">
                <span className="field-review-value-label"><FileText size={15} /> Extracted from ShareFile</span>
                <input className="input" value={field.value || "Not found"} disabled />
                <small>Original value returned by extraction.</small>
              </label>
              <label className="field-review-value-card field-review-value-proposed">
                <span className="field-review-value-label"><Sparkles size={15} /> Proposed to FT Williams</span>
                <input ref={inputRef} className="input" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={saving} />
                <small className="field-edit-hint">Edit if needed, then confirm. Empty values must be marked missing.</small>
              </label>
            </div>
          </div>

          <div className="field-review-insights">
            <div className="field-review-insight-card field-review-confidence-card">
              <div className="inspector-confidence-head">
                <span>Extraction confidence</span>
                <strong>{percent(field.confidence)}</strong>
              </div>
              <div className="confidence-track inspector-confidence">
                <i className={`confidence-${confidenceTone(field.confidence)}`} style={{ width: percent(field.confidence) }} />
              </div>
              <small>Confidence reported for this extracted value.</small>
            </div>

            <div className="field-review-insight-card field-review-rule-card">
              <div className="field-review-insight-head">
                <span>Rule match</span>
                <span className={`field-status status-${field.status.toLowerCase()}`}>{field.status === "MATCHED" ? "Exact Match" : field.status.replaceAll("_", " ")}</span>
              </div>
              <p>{field.status_reason || reasonForField(field)}</p>
            </div>
          </div>
        </div>

        <div className="field-review-modal-actions">
          <div className="field-review-action-group field-review-secondary-actions">
            <button className="button secondary" disabled={saving} onClick={() => { inputRef.current?.focus(); inputRef.current?.select(); }}>
              <Edit3 size={16} /> Edit Value
            </button>
            <button className="button danger" disabled={saving} onClick={() => save("", { markMissing: true })}>
              <Ban size={16} /> Mark Missing
            </button>
          </div>
          <div className="field-review-action-group field-review-primary-actions">
            <button className="button secondary" disabled={saving} onClick={onClose}>Cancel</button>
            <button className="button" disabled={saving || !draft.trim()} onClick={() => save(draft)}>
              {saving ? <InlineLoader label="Saving value" /> : <><CheckCircle2 size={16} /> {draft === (field.proposed_value ?? "") ? "Confirm Proposed" : "Save Value"}</>}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

function contractTypeLabel(type: ScheduleAContractType | string | null | undefined) {
  if (type === "EXPERIENCE_RATED") return "Experience rated";
  if (type === "NONEXPERIENCE_RATED") return "Nonexperience rated";
  if (type === "NEEDS_REVIEW") return "Needs type review";
  return "Type unknown";
}

function filterOptionLabel(option: string) {
  if (option === "SCHEDULE_A") return "ShareFile Schedule A";
  if (option === "FORM_5500") return "ShareFile Plan Worksheet";
  if (["EXPERIENCE_RATED", "NONEXPERIENCE_RATED", "NEEDS_REVIEW", "UNKNOWN"].includes(option)) {
    return contractTypeLabel(option);
  }
  return option.replaceAll("_", " ");
}

function fieldAllowedForContractType(field: ExtractedField, contractType: ScheduleAContractType) {
  if (field.form_type !== "SCHEDULE_A") return true;
  const ruleKey = fieldRuleKey(field);
  const derivedZero = isAutomaticallyDerivedZero(field);
  if (contractType === "EXPERIENCE_RATED" && NONEXPERIENCE_SCHEDULE_A_RULES.has(ruleKey)) return derivedZero;
  if (contractType === "NONEXPERIENCE_RATED" && NONEXPERIENCE_DERIVED_ZERO_RULES.has(ruleKey)) return derivedZero;
  return ruleAllowedForContractType(ruleKey, contractType);
}

function comparisonAllowedForContractType(
  comparison: FTWilliamsComparisonField,
  field: ExtractedField | undefined,
  contractType: ScheduleAContractType,
) {
  if (comparison.form_type !== "SCHEDULE_A") return true;
  if (field) return fieldAllowedForContractType(field, contractType);
  return ruleAllowedForContractType(comparison.rule_key || (field ? fieldRuleKey(field) : ""), contractType);
}

function isAutomaticallyDerivedZero(field: ExtractedField) {
  const numericValue = Number(String(field.proposed_value || "").replace(/[$,]/g, ""));
  return numericValue === 0 && field.status_reason?.startsWith("Automatically derived") === true;
}

function ruleAllowedForContractType(ruleKey: string, contractType: ScheduleAContractType) {
  if (contractType === "EXPERIENCE_RATED") return !NONEXPERIENCE_SCHEDULE_A_RULES.has(ruleKey);
  if (contractType === "NONEXPERIENCE_RATED") return !EXPERIENCE_SCHEDULE_A_RULES.has(ruleKey);
  return !EXPERIENCE_SCHEDULE_A_RULES.has(ruleKey) && !NONEXPERIENCE_SCHEDULE_A_RULES.has(ruleKey);
}

function SelectFilter({ label, value, options, onChange }: { label: string; value: FilterValue; options: string[]; onChange: (value: FilterValue) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const selectOptions = [
    { value: "ALL", label: "All" },
    ...options.map((option) => ({ value: option, label: filterOptionLabel(option) })),
  ];
  const selected = selectOptions.find((option) => option.value === value) || selectOptions[0];

  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, []);

  return (
    <div className="filter-dropdown review-filter-dropdown" ref={ref}>
      <button
        className="filter-dropdown-trigger"
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
      >
        <span className="filter-dropdown-label">{label}</span>
        <strong>{selected.label}</strong>
        <ChevronDown size={16} />
      </button>
      {open ? (
        <div className="filter-dropdown-menu">
          {selectOptions.map((option) => (
            <button
              className={option.value === value ? "selected" : ""}
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {option.value === value ? <Check size={16} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ProcessingPanel({ filing }: { filing: FilingDetail }) {
  const latestJob = (filing.jobs || [])[0];
  const status = filing.status || "UPLOADED";
  const displayStatus = status.replaceAll("_", " ");
  const activeStage = status === "QUEUED" || status === "UPLOADED"
    ? 0
    : status === "EXTRACTING"
      ? 1
      : status === "EXTRACTED"
        ? 2
        : 3;
  const headline = activeStage === 0
    ? "Your filing is in the processing queue"
    : activeStage === 1
      ? "We’re reading your filing"
      : activeStage === 2
        ? "We’re organizing the extracted data"
        : "We’re preparing your review";
  const stages = [
    { label: "File received", detail: "Your filing package is secure and ready.", icon: <FileText size={20} /> },
    { label: "Reading documents", detail: "Key filing details are being extracted.", icon: <Sparkles size={20} /> },
    { label: "Matching filing fields", detail: "Values are being mapped to review rules.", icon: <ListChecks size={20} /> },
    { label: "Preparing review", detail: "Your FT Williams comparison is being assembled.", icon: <ShieldCheck size={20} /> },
  ];

  return (
    <section className="extraction-progress-shell card" role="status" aria-live="polite" aria-label={`Filing extraction in progress: ${latestJob?.status || displayStatus}`}>
      <div className="extraction-progress-glow" aria-hidden="true" />
      <header className="extraction-progress-intro">
        <div className="extraction-progress-mark" aria-hidden="true">
          <Sparkles size={30} />
        </div>
        <div className="extraction-progress-kicker"><span /> Automated review in progress</div>
        <h1>{headline}</h1>
        <p>ERISAPros is securely analyzing the documents and matching the results to FT Williams. This usually takes a few minutes.</p>
        <div className="extraction-progress-live"><span /> Processing now</div>
      </header>

      <div className="extraction-progress-stages" aria-label="Extraction stages">
        {stages.map((stage, index) => {
          const state = index < activeStage ? "complete" : index === activeStage ? "current" : "pending";
          return (
            <div className={`extraction-progress-stage ${state}`} key={stage.label}>
              <div className="extraction-progress-stage-icon" aria-hidden="true">
                {state === "complete"
                  ? <CheckCircle2 size={22} />
                  : state === "current"
                    ? <RefreshCw className="extraction-progress-spinner" size={22} />
                    : stage.icon}
              </div>
              <div>
                <span>Step {index + 1}</span>
                <strong>{stage.label}</strong>
                <small>{stage.detail}</small>
              </div>
            </div>
          );
        })}
      </div>

      <footer className="extraction-progress-footer">
        <div className="extraction-progress-file">
          <FileText size={18} />
          <span><small>Processing filing</small><strong>{formatFilingDisplayName(filing.file_name)}</strong></span>
        </div>
        <div className="extraction-progress-reassurance">
          <ShieldCheck size={18} />
          <span><strong>You can safely leave this page.</strong><small>Processing continues in the background and this review updates automatically.</small></span>
        </div>
      </footer>
    </section>
  );
}

function ExtractionFailurePanel({ busy, onRetry }: { busy: boolean; onRetry: () => void }) {
  return (
    <section className="extraction-failure-shell card" role="alert">
      <div className="extraction-failure-mark" aria-hidden="true"><XCircle size={30} /></div>
      <span className="extraction-failure-kicker">Extraction needs attention</span>
      <h1>We couldn’t finish preparing this filing</h1>
      <p>A temporary issue interrupted document processing. Your original filing is safe, and you can restart extraction without uploading it again.</p>
      <button className="button extraction-retry-button" type="button" disabled={busy} onClick={onRetry}>
        {busy ? <InlineLoader label="Restarting extraction" /> : <><RefreshCw size={17} /> Try extraction again</>}
      </button>
      <small>If the issue continues, contact support and include the filing name.</small>
    </section>
  );
}

function FilingReviewSkeleton() {
  return (
    <div className="review-page approval-workspace-page filing-review-skeleton" role="status" aria-live="polite" aria-label="Loading filing review">
      <main className="approval-workspace">
        <section className="skeleton-workflow card">
          {Array.from({ length: 6 }, (_, index) => <div key={index}><Skeleton className="skeleton-icon" /><span><Skeleton className="skeleton-line skeleton-line-medium" /><Skeleton className="skeleton-line skeleton-line-short" /></span></div>)}
        </section>
        <section className="skeleton-review-banner"><Skeleton className="skeleton-icon-small" /><div><Skeleton className="skeleton-line skeleton-line-wide" /><Skeleton className="skeleton-line skeleton-line-medium" /></div></section>
        <section className="approval-summary-strip skeleton-summary-strip">
          {Array.from({ length: 5 }, (_, index) => <div className="approval-summary-card" key={index}><Skeleton className="skeleton-line skeleton-line-short" /><Skeleton className="skeleton-line skeleton-line-wide" /><Skeleton className="skeleton-line skeleton-line-medium" /></div>)}
        </section>
        <section className="approval-decision-table-shell skeleton-review-table card">
          <div className="skeleton-review-toolbar"><div><Skeleton className="skeleton-line skeleton-line-wide" /><Skeleton className="skeleton-line skeleton-line-medium" /></div><div><Skeleton className="skeleton-button skeleton-button-wide" /><Skeleton className="skeleton-button skeleton-button-wide" /><Skeleton className="skeleton-button" /></div></div>
          <div className="skeleton-review-tabs">{Array.from({ length: 6 }, (_, index) => <Skeleton className="skeleton-pill" key={index} />)}</div>
          <div className="skeleton-review-rows">{Array.from({ length: 6 }, (_, index) => <div key={index}>{Array.from({ length: 6 }, (_, column) => <Skeleton className={`skeleton-line ${column === 0 ? "skeleton-line-wide" : "skeleton-line-medium"}`} key={column} />)}</div>)}</div>
        </section>
      </main>
    </div>
  );
}

function FTWilliamsLoadingPanel({ autoQuery, sendBusy }: { autoQuery: boolean; sendBusy: boolean }) {
  return (
    <section className="processing-panel ftw-loading-panel card" role="status" aria-live="polite">
      <RefreshCw size={20} />
      <div>
        <h2>{sendBusy ? "Sending Approved Data to FT Williams" : "Fetching Current FT Williams Data"}</h2>
        <p>
          {sendBusy
            ? "Buttons are locked while the latest approved XML is sent and the response is saved."
            : autoQuery
              ? "Extraction is complete. ERISAPros is automatically loading current Form 5500 and Schedule A values for comparison."
              : "Buttons are locked while the current Form 5500 and Schedule A values are loaded for comparison."}
        </p>
      </div>
    </section>
  );
}

function sectionForField(field: ExtractedField) {
  const label = `${field.mapped_label || ""} ${field.ftw_field || ""} ${field.source_field_name}`.toLowerCase();
  if (label.includes("premium") || label.includes("claim") || label.includes("reserve") || label.includes("retention") || label.includes("taxes") || label.includes("dividend")) return "Financial / Premium Info";
  if (label.includes("insurance company") || label.includes("naic") || label.includes("carrier")) return "Insurance Carrier Info";
  if (label.includes("contract") || label.includes("policy")) return "Policy / Contract Info";
  if (label.includes("agent") || label.includes("broker") || label.includes("commission") || label.includes("fees") || label.includes("organization code")) return "Broker / Commission Info";
  if (label.includes("sponsor") || label.includes("plan name") || label.includes("plan number") || label.includes("administrator") || label.includes("ein")) return "Plan Sponsor Info";
  if (label.includes("participant") || label.includes("welfare") || label.includes("benefit arrangement") || label.includes("funding arrangement") || label.includes("characteristic")) return "Form 5500 Participant Info";
  return "Other FT Williams Fields";
}

function formLabel(field: ExtractedField) {
  if (field.form_type === "FORM_5500") return "Form 5500";
  if (field.form_type === "SCHEDULE_A") return "Schedule A";
  if (field.source_document_type === "PLAN_WORKSHEET") return "Form 5500";
  return "Schedule A";
}

function hasValue(field: ExtractedField) {
  return Boolean((field.proposed_value || field.value || "").trim());
}

function fieldSubtitle(field: ExtractedField, title: string) {
  const subtitle = field.ftw_field || field.source_field_name;
  return normalizeDisplayText(subtitle) === normalizeDisplayText(title) ? "" : subtitle;
}

function normalizeDisplayText(value: string) {
  return value.replace(/\s+/g, " ").trim().toLowerCase();
}

function compareFields(a: ExtractedField, b: ExtractedField) {
  const statusOrder: Record<string, number> = { MISSING: 0, LOW_CONFIDENCE: 1, UNMAPPED: 2, EDITED: 3, MATCHED: 4 };
  const priorityOrder: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2, IGNORE: 3 };
  return (
    (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9) ||
    (priorityOrder[a.priority] ?? 9) - (priorityOrder[b.priority] ?? 9) ||
    (a.mapped_label || a.source_field_name).localeCompare(b.mapped_label || b.source_field_name)
  );
}

function confidenceTone(value: number) {
  if (value >= 0.9) return "high";
  if (value >= 0.8) return "medium";
  if (value > 0) return "low";
  return "missing";
}

function priorityLabel(priority: ExtractedField["priority"]) {
  if (priority === "HIGH") return "High Priority";
  if (priority === "MEDIUM") return "Medium Priority";
  if (priority === "LOW") return "Low Priority";
  return "Ignore";
}

function reasonForField(field: ExtractedField) {
  if (field.status === "MISSING") return `${field.priority} priority field was not found in the extraction output.`;
  if (field.status === "LOW_CONFIDENCE") return "Extractor returned this value below the confidence threshold.";
  if (field.status === "UNMAPPED") return "Extracted field did not match a FT Williams alias.";
  return "Matched to FT Williams field rule.";
}

function paginationItems(currentPage: number, totalPages: number) {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const pages = new Set([1, totalPages, currentPage, currentPage - 1, currentPage + 1]);
  return [...pages]
    .filter((page) => page >= 1 && page <= totalPages)
    .sort((a, b) => a - b)
    .reduce<(number | "...")[]>((items, page) => {
      const previous = items[items.length - 1];
      if (typeof previous === "number" && page - previous > 1) items.push("...");
      items.push(page);
      return items;
    }, []);
}

function isProcessingStatus(status: FilingDetail["status"]) {
  return ["QUEUED", "UPLOADED", "EXTRACTING", "EXTRACTED", "MAPPED", "QUERYING_FTW_CURRENT"].includes(status);
}
