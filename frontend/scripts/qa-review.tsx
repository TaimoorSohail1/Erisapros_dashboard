// Local-only visual harness: real review components, synthetic responses, no remote writes.
import ReactDOM from "react-dom/client";
import { FilingReviewPage } from "../src/pages/FilingReviewPage";
import { AppShell } from "../src/ui/AppShell";
import "../src/styles.css";

const scenario = new URLSearchParams(location.search).get("scenario") || "missing";
const fields = Array.from({ length: 14 }, (_, index) => ({
  id: `field-${index}`, filing_id: "layout-qa", source_field_name: `Field ${index + 1}`,
  mapped_label: `Schedule A field ${index + 1}`, ftw_field: `QAField${index}`, xml_tag: `QAField${index}`,
  mapped_rule_key: "",
  priority: "HIGH", value: index < 2 ? "123" : "", proposed_value: index < 2 ? "123" : "",
  confidence: 0.95, status: index < 2 ? "MATCHED" : "MISSING", form_type: "SCHEDULE_A",
  source_document_type: "SCHEDULE_A", ftw_current_value: "", source_text: "Synthetic QA evidence",
}));
const filing = {
  id: "layout-qa", file_name: "Schedule A — synthetic responsive QA filing.pdf", original_filename: "Schedule A — synthetic responsive QA filing.pdf", filename: "Schedule A — synthetic responsive QA filing.pdf",
  status: scenario === "locked" ? "APPROVED" : "NEEDS_REVIEW", automation_status: scenario === "locked" ? "DISABLED" : "ACTION_NEEDED", automation_next_action: "RESOLVE_ISSUES",
  automation_reasons: ["Review required"], fields, total_fields: 14, found_field_count: 2,
  overall_confidence: 0.95, created_at: "2026-09-16T07:00:00Z", updated_at: "2026-09-16T07:00:00Z",
  schedule_a_broker_rows: [
    { name: "Example Insurance Broker", address_line_1: "101 Sample Street", city: "Boston", state: "MA", zip_code: "02199", organization_code: "", commission_total: "125", fee_total: "0" },
    { name: "Second Example Broker", address_line_1: "100 Main Street", city: "Rochester", state: "NY", zip_code: "14625", organization_code: "3", commission_total: "100", fee_total: "10" },
  ],
  ftw_review: {
    status: "CURRENT_QUERIED", current_query_success: scenario !== "failed", current_year_exists: scenario === "locked",
    bring_forward_required: scenario === "missing", query_state: scenario === "missing" ? "SCHEDULE_A_MISSING" : scenario === "failed" ? "QUERY_FAILED" : "CURRENT_READY",
    ftw_editable: scenario !== "locked", ftw_locked_status: scenario === "locked" ? "Locked" : "",
    year: "2025", updated_at: "2026-09-16T07:00:00Z", comparison_fields: [],
    plan_lookup: { status: "FOUND", matches: [], error_message: scenario === "failed" ? "Synthetic connection error. Retry the query." : "" },
  },
};
if (scenario === "customer-defaults") {
  Object.assign(filing.schedule_a_broker_rows[0], { organization_code: "3", organization_code_defaulted: true });
  Object.assign(filing.schedule_a_broker_rows[1], { organization_code: "6", organization_code_defaulted: false });
}
if (scenario === "verified-update") {
  Object.assign(filing, {
    status: "APPROVED",
    automation_status: "COMPLETED",
    automation_next_action: null,
    automation_reasons: [],
  });
  Object.assign(filing.ftw_review, {
    configured: true,
    status: "UPDATE_SENT",
    current_query_success: true,
    current_query_complete: true,
    current_year_exists: true,
    bring_forward_required: false,
    query_state: "MATCHED",
    update_verification_attempted: true,
    update_verification_success: true,
    update_attempted_count: 3,
    update_confirmed_count: 3,
    update_remaining_count: 0,
    will_update_count: 3,
    kept_current_count: 25,
    skipped_empty_count: 10,
    decision_required_count: 0,
    ftw_browser_customer_id: "111",
    ftw_browser_plan_id: "222",
    audit_pdf_status: "AVAILABLE",
    audit_pdf_sha256: "4fcbda4c7f637104d4caab2b3734ad1a26206ca2bc8947fc3bd396cec46554a1",
    update_receipt: {
      receipt_id: "qa-receipt-001",
      action: "SCHEDULE_A_UPDATED",
      verified_at: "2026-09-30T20:45:00Z",
      year: "2025",
      ftw_seq_no: "2",
      schedule_desc: "BCBS-O",
      carrier_name: "BlueCross BlueShield of Oklahoma",
      carrier_ein: "36-1236610",
      contract_number: "Y00979",
      plan_name: "Crest Discount Foods Flexible Benefits Plan",
      plan_number: "501",
      ftw_plan_url: "https://ftwilliam.com/cgi-bin/index.cgi?#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&plan=111,222&Year=2025",
    },
    update_results: [
      { field_id: "field-1", label: "14. Active participants at end", sent_value: "101", returned_value: "101", status: "VERIFIED" },
      { field_id: "field-2", label: "10a. Total premiums", sent_value: "100", returned_value: "100", status: "VERIFIED" },
      { field_id: "field-3", label: "No commissions or fees paid", sent_value: "Checked", returned_value: "Checked", status: "VERIFIED" },
    ],
  });
}
if (scenario === "selected") {
  fields.slice(0, 2).forEach((field, index) => Object.assign(field, {
    form_type: "FORM_5500", mapped_label: index ? "Selected sponsor change" : "Selected participant change",
    mapped_rule_key: index ? "form_5500_part_i_1d_plan_sponsor_name" : "form_5500_part_ii_14_active_participants_at_end",
    value: index ? "New sponsor" : "101", proposed_value: index ? "New sponsor" : "101", status: "EDITED",
  }));
  Object.assign(filing.ftw_review, {
    configured: true, current_query_success: true, current_query_complete: true, current_year_exists: true,
    bring_forward_required: false, query_state: "CURRENT_READY",
    fields: fields.slice(0, 2).map((field, index) => ({
      field_id: field.id, label: field.mapped_label, form_type: "FORM_5500", rule_key: field.mapped_rule_key,
      current_value: index ? "Existing sponsor" : "100", proposed_value: field.proposed_value,
      extracted_value: field.value, changed: true, update_included: true, validation_blocking: false,
    })),
  });
}
history.replaceState(null, "", "/filings/layout-qa");
window.fetch = async (input, options = {}) => {
  if (scenario === "selected" && String(input).includes("/ftw/send-update") && options.method === "POST") {
    const payload = JSON.parse(String(options.body || "{}"));
    return new Response(JSON.stringify({ detail: `Synthetic send only — no FT Williams write. Selected fields: ${(payload.selected_field_ids || []).join(", ") || "none"}. Broker updates: ${Boolean(payload.include_broker_updates)}.` }), { status: 400 });
  }
  if (options.method && options.method !== "GET") return new Response(JSON.stringify({ detail: "QA is read-only; changes are disabled." }), { status: 400 });
  const url = String(input);
  const payload = url.includes("local-agent/status") ? { enabled: true, connected: true, status: "CONNECTED", device_count: 1 }
    : url.includes("failure-notifications") ? { notifications: [], items: [], total: 0, unread_count: 0 }
    : url.includes("/filings/") ? filing : {};
  return new Response(JSON.stringify(payload), { headers: { "Content-Type": "application/json" } });
};
ReactDOM.createRoot(document.getElementById("root")!).render(<AppShell><FilingReviewPage /></AppShell>);
