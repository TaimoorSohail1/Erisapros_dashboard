import {
  Archive,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Database,
  Edit3,
  FileSearch,
  FlaskConical,
  History,
  Layers3,
  Plus,
  Save,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  disableFieldRule,
  getFieldRuleHistory,
  listFieldRules,
  publishFieldRule,
  rollbackFieldRule,
  saveFieldRuleDraft,
  testFieldRule,
  runFieldRuleExtractionQA,
} from "../api";
import { useDialogFocus } from "../ui/useDialogFocus";
import type { FieldRule, FieldRuleQAResult, FTWFieldCatalogEntry } from "../types";
import { InlineLoader, Skeleton } from "../ui/Loading";

const PRIORITIES = ["ALL", "HIGH", "MEDIUM", "LOW"] as const;
const STATUS_TABS = ["ALL", "PUBLISHED", "DRAFT", "DISABLED"] as const;

type EditorMode = "add" | "edit" | "clone";
type RuleEditorState = { mode: EditorMode; rule: FieldRule };

export function FieldRulesPage() {
  const [rules, setRules] = useState<FieldRule[]>([]);
  const [publishedVersion, setPublishedVersion] = useState("");
  const [catalog, setCatalog] = useState<FTWFieldCatalogEntry[]>([]);
  const [catalogVersion, setCatalogVersion] = useState("");
  const [canManage, setCanManage] = useState(false);
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState("ALL");
  const [section, setSection] = useState("ALL");
  const [type, setType] = useState("ALL");
  const [status, setStatus] = useState("ALL");
  const [selectedRule, setSelectedRule] = useState<FieldRule | null>(null);
  const [editor, setEditor] = useState<RuleEditorState | null>(null);
  const [qaDocumentType, setQaDocumentType] = useState<"SCHEDULE_A" | "PLAN_WORKSHEET">("SCHEDULE_A");
  const [qaFile, setQaFile] = useState<File | null>(null);
  const [qaResult, setQaResult] = useState<FieldRuleQAResult | null>(null);
  const [qaBusy, setQaBusy] = useState(false);
  const [qaError, setQaError] = useState("");

  const refresh = useCallback(async (preferredKey?: string) => {
    setLoading(true);
    setMessage("");
    try {
      const payload = await listFieldRules();
      setRules(payload.field_rules);
      setPublishedVersion(payload.published_version);
      setCatalog(payload.field_catalog || []);
      setCatalogVersion(payload.catalog_version || "");
      setCanManage(payload.can_manage);
      if (preferredKey) setSelectedRule(payload.field_rules.find((rule) => rule.key === preferredKey && rule.status === "DRAFT") || payload.field_rules.find((rule) => rule.key === preferredKey) || null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Field rules could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const activeRules = rules.filter((rule) => rule.status === "PUBLISHED");
  const highCount = activeRules.filter((rule) => rule.priority === "HIGH").length;
  const draftCount = rules.filter((rule) => rule.status === "DRAFT").length;
  const updateCount = activeRules.filter((rule) => rule.update_supported).length;
  const sections = useMemo(() => uniqueValues(rules.map((rule) => rule.form_section || rule.source)), [rules]);
  const types = useMemo(() => uniqueValues(rules.map((rule) => rule.field_type)), [rules]);

  const filteredRules = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return rules.filter((rule) => {
      const haystack = [
        rule.label, rule.key, rule.xml_tag, rule.form_section, rule.field_type, rule.priority,
        rule.status, rule.applicability, rule.existing_behavior, rule.new_behavior, rule.notes,
        rule.client_notes, ...rule.aliases,
      ].join(" ").toLowerCase();
      return (
        (!normalizedQuery || haystack.includes(normalizedQuery)) &&
        (priority === "ALL" || rule.priority === priority) &&
        (status === "ALL" || rule.status === status) &&
        (section === "ALL" || (rule.form_section || rule.source) === section) &&
        (type === "ALL" || rule.field_type === type)
      );
    });
  }, [query, priority, status, section, type, rules]);

  const groupedRules = useMemo(() => filteredRules.reduce<Record<string, FieldRule[]>>((groups, rule) => {
    const groupName = rule.form_section || rule.source || "Other";
    groups[groupName] = groups[groupName] || [];
    groups[groupName].push(rule);
    return groups;
  }, {}), [filteredRules]);

  function resetFilters() {
    setQuery(""); setPriority("ALL"); setSection("ALL"); setType("ALL"); setStatus("ALL");
  }

  function openEditor(mode: EditorMode, source?: FieldRule) {
    if (!canManage) return;
    if (!source) return setEditor({ mode, rule: emptyRule() });
    const copy = structuredClone(source);
    if (mode === "clone") {
      copy.id = null;
      copy.status = "DRAFT";
      copy.version = 1;
    }
    setEditor({ mode, rule: copy });
  }

  function flash(value: string) {
    setNotice(value);
    window.setTimeout(() => setNotice(""), 5000);
  }

  async function runDocumentQA() {
    if (!qaFile) return setQaError("Choose a synthetic or approved QA document first.");
    setQaBusy(true); setQaError(""); setQaResult(null);
    try { setQaResult(await runFieldRuleExtractionQA(qaFile, qaDocumentType)); }
    catch (error) { setQaError(error instanceof Error ? error.message : "Document extraction QA failed."); }
    finally { setQaBusy(false); }
  }

  return (
    <div className="rules-page rules-admin-page">
      <div className="rules-command-hero">
        <div className="rules-hero-copy">
          <div className="rules-title-row">
            <span className="rules-title-icon"><ShieldCheck size={23} /></span>
            <div>
              <span className="eyebrow">FT Williams mapping control center</span>
              <h1 className="page-title">Field Rules</h1>
            </div>
          </div>
          <p className="rules-intro">Control how document values are extracted, normalized, reviewed, and safely mapped to FT Williams.</p>
          <div className="rules-version-line">
            <span className="status-dot-live" /> Published rule set {loading ? <Skeleton className="skeleton-version" /> : <code>{publishedVersion}</code>}
            <span>FTW catalog {catalogVersion ? <code>{catalogVersion}</code> : "loading"} · {catalog.length || "—"} fields</span>
            <span>Plan Worksheet labels are fixed; Schedule A aliases use a draft, test, and publish workflow.</span>
          </div>
        </div>
        <div className="rules-hero-actions">
          <div className={`admin-access-pill ${canManage ? "is-admin" : "is-readonly"}`}>
            {canManage ? <ShieldCheck size={16} /> : <ClipboardCheck size={16} />}
            {canManage ? "Admin workspace" : "Read-only workspace"}
          </div>
          {canManage ? <button className="button rules-add-button" type="button" onClick={() => openEditor("add")}><Plus size={18} /> Add field rule</button> : null}
        </div>
      </div>

      <div className="rules-kpi-grid rules-admin-kpis">
        <MetricCard loading={loading} icon={<Database size={20} />} label="Active rules" value={activeRules.length} detail="Published mapping inventory" tone="green" />
        <MetricCard loading={loading} icon={<ShieldCheck size={20} />} label="High priority" value={highCount} detail="Require capture or review" tone="red" />
        <MetricCard loading={loading} icon={<Edit3 size={20} />} label="Draft changes" value={draftCount} detail={draftCount ? "Awaiting test and publish" : "No unpublished changes"} tone="blue" />
        <MetricCard loading={loading} icon={<Layers3 size={20} />} label="Update-capable" value={updateCount} detail="May propose FTW changes" tone="amber" />
      </div>

      {notice ? <div className="rules-notice" role="status"><CheckCircle2 size={18} /> {notice}</div> : null}
      {message ? <div className="card card-pad rules-error" role="alert">{message}</div> : null}

      <div className="card rules-control-card">
        <div className="rules-status-tabs" role="tablist" aria-label="Rule status">
          {STATUS_TABS.map((item) => (
            <button key={item} type="button" className={status === item ? "active" : ""} onClick={() => setStatus(item)}>
              {titleCase(item)} <span>{item === "ALL" ? rules.length : rules.filter((rule) => rule.status === item).length}</span>
            </button>
          ))}
        </div>
        <div className="rules-toolbar rules-toolbar-modern">
          <div className="search-field"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search field, alias, FTW tag, or note" /></div>
          <div className="filter-group" aria-label="Priority filter">
            {PRIORITIES.map((item) => <button key={item} type="button" className={priority === item ? "active" : ""} onClick={() => setPriority(item)}>{item === "ALL" ? "All priority" : titleCase(item)}</button>)}
          </div>
          <select className="select-filter" value={section} onChange={(event) => setSection(event.target.value)} aria-label="Section"><option value="ALL">All sections</option>{sections.map((item) => <option key={item} value={item}>{item}</option>)}</select>
          <select className="select-filter" value={type} onChange={(event) => setType(event.target.value)} aria-label="Field type"><option value="ALL">All types</option>{types.map((item) => <option key={item} value={item}>{item}</option>)}</select>
          <button type="button" className="toolbar-reset" onClick={resetFilters}><SlidersHorizontal size={16} /> Reset</button>
        </div>
      </div>

      {canManage ? <section className="card field-rule-qa-card" aria-busy={qaBusy}>
        <div className="field-rule-qa-heading"><div className="field-rule-qa-icon"><FileSearch size={21} /></div><div><span className="eyebrow">Safe extraction QA</span><h2>Test a document against published rules</h2><p>Checks aliases and FTW behavior without creating a filing or sending anything to FT Williams.</p></div></div>
        <div className="field-rule-qa-controls">
          <label><span>Document family</span><select value={qaDocumentType} onChange={(event) => { setQaDocumentType(event.target.value as typeof qaDocumentType); setQaResult(null); }}><option value="SCHEDULE_A">ShareFile Schedule A</option><option value="PLAN_WORKSHEET">ShareFile Plan Worksheet</option></select></label>
          <label className="field-rule-qa-file"><span>QA document</span><input type="file" accept=".pdf,.docx,.xlsx,.xlsm,.csv,.txt" onChange={(event) => { setQaFile(event.target.files?.[0] || null); setQaResult(null); setQaError(""); }} /></label>
          <button className="button" type="button" disabled={qaBusy || !qaFile} onClick={() => void runDocumentQA()}>{qaBusy ? <InlineLoader label="Testing document" /> : <><FlaskConical size={17} /> Run extraction test</>}</button>
        </div>
        {qaError ? <div className="field-rule-qa-error" role="alert">{qaError}</div> : null}
        {qaResult ? <div className="field-rule-qa-results">
          <div className="field-rule-qa-summary"><strong>{qaResult.file_name}</strong><span>{qaResult.provider}</span><span>Rule set <code>{qaResult.rule_set_version}</code></span><b>{qaResult.summary.matched}/{qaResult.summary.extracted} matched</b>{qaResult.summary.extraction_only ? <b>{qaResult.summary.extraction_only} extraction-only</b> : null}{qaResult.summary.unmatched ? <b className="qa-warning">{qaResult.summary.unmatched} unmatched</b> : null}</div>
          <div className="field-rule-qa-table-wrap"><table><thead><tr><th>Extracted field</th><th>Value</th><th>Matched alias</th><th>Rule</th><th>FT Williams behavior</th></tr></thead><tbody>{qaResult.fields.map((field, index) => <tr key={`${field.field_name}:${index}`}><td><strong>{field.field_name}</strong><span>{Math.round(field.confidence * 100)}% confidence</span></td><td>{field.value || "—"}</td><td>{field.matched_alias || <em>Unmatched</em>}</td><td>{field.mapped_label || "No rule"}</td><td>{field.mapping_mode === "EXTRACTION_ONLY" ? <span className="qa-safe-chip">Review only · never sent</span> : field.will_send_to_ftw ? <span className="qa-ftw-chip">Approved FTW tag · {field.ftw_tag}</span> : field.matched ? <span className="qa-readonly-chip">FTW read-only</span> : <span className="qa-warning-chip">Needs a rule</span>}</td></tr>)}</tbody></table></div>
        </div> : null}
      </section> : null}

      <div className="rules-table-summary">{loading ? <InlineLoader label="Loading field rules" /> : <><strong>{filteredRules.length}</strong> rules shown <span>•</span> Select a row to inspect mapping details and history.</>}</div>
      <div className="card table-wrap rules-table-card rules-admin-table-card" aria-busy={loading}>
        <table className="rules-table rules-admin-table">
          <thead><tr><th>Field rule</th><th>Applicability</th><th>Priority</th><th>Behavior</th><th>Aliases</th><th>Status</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {Object.entries(groupedRules).map(([groupName, groupRules]) => (
              <Fragment key={groupName}>
                <tr className="section-row"><td colSpan={8}><span>{groupName}</span><strong>{groupRules.length} fields</strong></td></tr>
                {groupRules.map((rule) => (
                  <tr key={`${rule.key}:${rule.version}:${rule.status}`} className={`clickable-row row-${rule.priority.toLowerCase()}`} tabIndex={0} onClick={() => setSelectedRule(rule)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedRule(rule); } }}>
                    <td><div className="field-title-line"><span className={`priority-dot dot-${rule.priority.toLowerCase()}`} /><strong>{rule.label}</strong></div><code className="field-code">{rule.mapping_mode === "EXTRACTION_ONLY" ? "EXTRACTION ONLY" : rule.xml_tag || rule.key}</code></td>
                    <td><span className={`applicability-chip applicability-${rule.applicability.toLowerCase()}`}>{applicabilityLabel(rule.applicability)}</span></td>
                    <td><span className={`badge priority-${rule.priority.toLowerCase()}`}>{rule.priority}</span></td>
                    <td><div className="behavior-stack"><span>Existing <strong>{rule.existing_behavior || "—"}</strong></span><span>New <strong>{rule.new_behavior || "—"}</strong></span></div></td>
                    <td><div className="alias-preview">{rule.aliases.slice(0, 2).map((alias) => <span key={alias}>{alias}</span>)}{rule.aliases.length > 2 ? <em>+{rule.aliases.length - 2}</em> : null}</div></td>
                    <td><RuleStatus status={rule.status} /></td>
                    <td><div className="rule-updated"><strong>v{rule.version}</strong><span>{formatDate(rule.updated_at)}</span></div></td>
                    <td><ChevronRight size={18} className="row-chevron" /></td>
                  </tr>
                ))}
              </Fragment>
            ))}
            {!loading && !filteredRules.length ? <tr><td colSpan={8} className="empty-state">No field rules match the current filters.</td></tr> : null}
            {loading ? Array.from({ length: 6 }, (_, index) => <FieldRuleSkeletonRow key={index} />) : null}
          </tbody>
        </table>
      </div>

      {selectedRule ? (
        <RuleDrawer
          rule={selectedRule}
          canManage={canManage}
          onClose={() => setSelectedRule(null)}
          onEdit={() => openEditor("edit", selectedRule)}
          onClone={() => openEditor("clone", selectedRule)}
          onChanged={async (text) => { flash(text); await refresh(selectedRule.key); }}
        />
      ) : null}
      {editor ? (
        <RuleEditor
          state={editor}
          approvedRules={rules.filter((rule) => rule.status === "PUBLISHED")}
          catalog={catalog}
          onClose={() => setEditor(null)}
          onSaved={async (rule) => { setEditor(null); flash("Draft saved. Test and publish it when ready."); await refresh(rule.key); }}
        />
      ) : null}
    </div>
  );
}

function MetricCard({ icon, label, value, detail, tone, loading = false }: { icon: ReactNode; label: string; value: number; detail: string; tone: string; loading?: boolean }) {
  return <div className={`metric-card metric-${tone}`}><div className="metric-icon">{icon}</div><div><div className="kpi-label">{label}</div>{loading ? <Skeleton className="skeleton-kpi-value" /> : <div className="kpi-value">{value}</div>}{loading ? <Skeleton className="skeleton-kpi-note" /> : <div className="metric-detail">{detail}</div>}</div></div>;
}

function FieldRuleSkeletonRow() {
  return (
    <tr className="field-rule-skeleton-row">
      <td><Skeleton className="skeleton-line skeleton-line-wide" /><Skeleton className="skeleton-line skeleton-line-medium" /></td>
      <td><Skeleton className="skeleton-pill" /></td>
      <td><Skeleton className="skeleton-pill skeleton-pill-small" /></td>
      <td><Skeleton className="skeleton-line skeleton-line-medium" /><Skeleton className="skeleton-line skeleton-line-short" /></td>
      <td><Skeleton className="skeleton-line skeleton-line-wide" /><Skeleton className="skeleton-pill skeleton-pill-small" /></td>
      <td><Skeleton className="skeleton-pill" /></td>
      <td><Skeleton className="skeleton-line skeleton-line-short" /></td>
      <td><Skeleton className="skeleton-icon-small" /></td>
    </tr>
  );
}

function RuleDrawer({ rule, canManage, onClose, onEdit, onClone, onChanged }: {
  rule: FieldRule; canManage: boolean; onClose: () => void; onEdit: () => void; onClone: () => void; onChanged: (message: string) => Promise<void>;
}) {
  const [history, setHistory] = useState<FieldRule[]>([]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const drawerRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, drawerRef, onClose);

  useEffect(() => {
    if (!canManage) return;
    getFieldRuleHistory(rule.key).then(setHistory).catch(() => setHistory([]));
  }, [rule.key, rule.version, rule.status, canManage]);

  async function perform(action: "publish" | "disable" | "rollback", version?: number) {
    if (!reason.trim()) return setError("Add a short change reason before continuing.");
    setBusy(true); setError("");
    try {
      if (action === "publish") await publishFieldRule(rule.key, reason);
      if (action === "disable") await disableFieldRule(rule.key, reason);
      if (action === "rollback" && version) await rollbackFieldRule(rule.key, version, reason);
      await onChanged(action === "publish" ? "Rule published. New extraction jobs will use it." : action === "disable" ? "Rule disabled for new extraction jobs." : `Rule restored from version ${version}.`);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "The action could not be completed.");
    } finally { setBusy(false); }
  }

  return <div className="drawer-layer" role="presentation">
    <button className="drawer-scrim" type="button" onClick={onClose} aria-label="Close field details" />
    <aside ref={drawerRef} tabIndex={-1} className="rule-drawer rule-admin-drawer" role="dialog" aria-modal="true" aria-label="Field rule details">
      <div className={`drawer-head drawer-${rule.priority.toLowerCase()}`}><div><div className="drawer-status-line"><RuleStatus status={rule.status} /><span>Version {rule.version}</span></div><h2>{rule.label}</h2><code className="field-code">{rule.xml_tag || rule.key}</code></div><button type="button" className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button></div>
      {canManage ? <div className="drawer-action-bar"><button type="button" onClick={onEdit}><Edit3 size={16} /> Edit</button>{rule.mapping_mode !== "EXTRACTION_ONLY" ? <button type="button" onClick={onClone}><Copy size={16} /> Clone</button> : null}</div> : null}
      <div className="drawer-grid"><Info label="Priority" value={rule.priority} /><Info label="Applicability" value={applicabilityLabel(rule.applicability)} /><Info label="Existing record" value={rule.existing_behavior || "—"} /><Info label="New record" value={rule.new_behavior || "—"} /></div>
      <section className="drawer-section"><h3>Mapping identity</h3><Definition label="Stable key" value={rule.key} /><Definition label="Rule type" value={rule.mapping_mode === "EXTRACTION_ONLY" ? "Extraction only — never sent to FT Williams" : rule.update_supported ? "Verified FT Williams update mapping" : "FT Williams comparison field — never sent"} /><Definition label="FT Williams field" value={rule.ftw_field} /><Definition label="Form section" value={rule.form_section || rule.source} /><Definition label="Field type" value={rule.field_type} /></section>
      <section className="drawer-section"><h3>Aliases used by the agent <span>{rule.aliases.length}</span></h3><div className="alias-chips">{rule.aliases.length ? rule.aliases.map((alias) => <span key={alias}>{alias}</span>) : <em>No aliases configured.</em>}</div></section>
      <section className="drawer-section"><h3>Rule guidance</h3><p>{rule.client_notes || rule.notes || "No guidance has been added."}</p></section>
      {canManage ? <section className="drawer-section publish-panel"><h3>Controlled change</h3><p>Every publish, disable, or rollback requires a reason and is preserved in history.</p><textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why is this change required?" />{error ? <div className="form-error">{error}</div> : null}<div className="publish-actions">{rule.status === "DRAFT" ? <button className="button" disabled={busy} onClick={() => void perform("publish")}>{busy ? <InlineLoader label="Publishing" /> : <><Sparkles size={16} /> Publish draft</>}</button> : null}{rule.status === "PUBLISHED" ? <button className="button button-danger-soft" disabled={busy} onClick={() => void perform("disable")}>{busy ? <InlineLoader label="Disabling" /> : <><Archive size={16} /> Disable rule</>}</button> : null}</div></section> : null}
      {canManage ? <section className="drawer-section"><h3><History size={17} /> Version history</h3><div className="rule-history-list">{history.map((item) => <div key={item.id || `${item.version}:${item.status}:${item.created_at}`}><div><strong>v{item.version} · {titleCase(item.status)}</strong><span>{item.updated_by || "System"} · {formatDate(item.updated_at)}</span><p>{item.change_reason || "No reason recorded."}</p></div>{item.status === "PUBLISHED" && item.version !== rule.version ? <button type="button" disabled={busy || !reason.trim()} onClick={() => void perform("rollback", item.version)}>Restore</button> : null}</div>)}</div></section> : null}
    </aside>
  </div>;
}

function RuleEditor({ state, approvedRules, catalog, onClose, onSaved }: { state: RuleEditorState; approvedRules: FieldRule[]; catalog: FTWFieldCatalogEntry[]; onClose: () => void; onSaved: (rule: FieldRule) => Promise<void> }) {
  const [rule, setRule] = useState<FieldRule>(state.rule);
  const [reason, setReason] = useState(state.mode === "add" ? "Add a new field mapping." : state.mode === "clone" ? "Clone an existing field mapping." : "Update field mapping guidance.");
  const [aliases, setAliases] = useState(state.rule.aliases.join("\n"));
  const [sample, setSample] = useState(state.rule.aliases[0] || state.rule.label);
  const [testResult, setTestResult] = useState<{ valid: boolean; matched: boolean; message: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [catalogQuery, setCatalogQuery] = useState("");
  const isNewIdentity = state.mode !== "edit";
  const isExtractionOnly = rule.mapping_mode === "EXTRACTION_ONLY";
  const catalogEntry = catalog.find((entry) => entry.key === rule.key);
  const isDiscoveredField = catalogEntry?.catalog_tier === "DISCOVERED";
  const isFixedWorksheetField = !isExtractionOnly && catalogEntry?.form_type === "FORM_5500";
  const canEditFieldName = isExtractionOnly || Boolean(isDiscoveredField && catalogEntry?.form_type === "SCHEDULE_A");
  const filteredCatalog = useMemo(() => {
    const query = catalogQuery.trim().toLowerCase();
    if (!query) return catalog;
    return catalog.filter((entry) => [entry.label, entry.current_tag, entry.form_type, entry.catalog_tier].join(" ").toLowerCase().includes(query));
  }, [catalog, catalogQuery]);
  const editorRef = useRef<HTMLElement | null>(null);
  useDialogFocus(true, editorRef, onClose);

  function update<K extends keyof FieldRule>(key: K, value: FieldRule[K]) { setRule((current) => ({ ...current, [key]: value })); }
  function selectApprovedRule(key: string) {
    const selected = approvedRules.find((item) => item.key === key);
    const entry = catalog.find((item) => item.key === key);
    if (!entry) return setRule(emptyRule());
    const prepared: FieldRule = selected ? {
      ...structuredClone(selected),
      id: null,
      status: "DRAFT" as const,
      version: 1,
      ...(selected.update_supported ? {} : { existing_behavior: "Review Only", new_behavior: "Keep FTW" }),
    } : {
      ...emptyRule(),
      key: entry.key,
      label: entry.label,
      ftw_field: entry.label,
      xml_tag: entry.current_tag,
      source: entry.form_type === "FORM_5500" ? "Form 5500" : "Schedule A",
      form_section: entry.form_section,
      applicability: entry.form_type === "FORM_5500" ? "FORM_5500" : "BOTH",
      existing_behavior: "Review Only",
      new_behavior: "Keep FTW",
      update_supported: false,
      approved_update_tag: null,
      aliases: entry.form_type === "FORM_5500" ? [entry.label] : [],
    };
    setRule(prepared);
    setAliases(prepared.aliases.join("\n"));
    setSample(prepared.aliases[0] || prepared.label);
    setCatalogQuery("");
    setTestResult(null);
  }

  function preparedRule(): FieldRule {
    const extractionKey = `custom_${rule.label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")}`;
    return {
      ...rule,
      key: isExtractionOnly ? extractionKey : rule.key.trim(),
      ftw_field: isExtractionOnly ? "" : rule.ftw_field.trim(),
      xml_tag: isExtractionOnly ? null : rule.xml_tag?.trim() || null,
      existing_behavior: isExtractionOnly ? "Review Only" : rule.existing_behavior,
      new_behavior: isExtractionOnly ? "Keep FTW" : rule.new_behavior,
      aliases: aliases.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean),
      status: "DRAFT",
    };
  }

  async function testCurrentRule() {
    setBusy(true); setError("");
    try { setTestResult(await testFieldRule(preparedRule(), sample)); }
    catch (testError) { setError(testError instanceof Error ? testError.message : "Rule test failed."); }
    finally { setBusy(false); }
  }

  async function save() {
    const candidate = preparedRule();
    if (!candidate.key.trim()) return setError(isExtractionOnly ? "Enter a field name." : "Select an FT Williams field.");
    if (!rule.label.trim()) return setError("Field name is required.");
    if (!aliases.split(/\r?\n|,/).some((item) => item.trim())) return setError("Add at least one EyeLevel alias.");
    if (isNewIdentity && (!testResult?.valid || !testResult.matched)) return setError("Run a successful mapping test before saving this new rule.");
    if (!reason.trim()) return setError("A change reason is required.");
    setBusy(true); setError("");
    try { await onSaved(await saveFieldRuleDraft(candidate, reason)); }
    catch (saveError) { setError(saveError instanceof Error ? saveError.message : "Draft could not be saved."); }
    finally { setBusy(false); }
  }

  return <div className="drawer-layer editor-layer" role="presentation">
    <button className="drawer-scrim" type="button" onClick={onClose} aria-label="Close editor" />
    <aside ref={editorRef} tabIndex={-1} className="rule-editor-panel" role="dialog" aria-modal="true" aria-label="Field rule editor">
      <header><div><span className="eyebrow">{state.mode === "add" ? "New mapping" : state.mode === "clone" ? "Clone mapping" : "Create next version"}</span><h2>{state.mode === "add" ? "Add field rule" : state.mode === "clone" ? "Clone field rule" : `Edit ${state.rule.label}`}</h2><p>Add the field name and aliases, test the match, then save the rule as a draft.</p></div><button className="icon-button" type="button" onClick={onClose} aria-label="Close field rule editor"><X size={20} /></button></header>
      <div className="rule-editor-body">
        <EditorSection number="1" title="Field setup" description="Choose what the field is called and where it applies.">
          <div className="form-grid client-rule-grid">
            {isNewIdentity && !isExtractionOnly ? <><Field label="Find FT Williams field" hint="Search the verified and read-only fields returned by FT Williams."><input value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} placeholder="Search name or FTW tag" /></Field><Field label="FT Williams field" hint="Technical tags are protected. Discovered fields remain comparison-only until their update contract is verified."><select value={rule.key} onChange={(event) => selectApprovedRule(event.target.value)}><option value="">Select an FT Williams field</option>{filteredCatalog.map((entry) => <option key={entry.key} value={entry.key}>{entry.label} · {entry.form_type === "FORM_5500" ? "Form 5500" : "Schedule A"}{entry.update_supported ? "" : " (comparison only)"}</option>)}</select></Field></> : null}
            <Field label="Field name" hint={canEditFieldName ? "Name shown to reviewers and used with the aliases below." : "Official fixed field label"}><input value={rule.label} readOnly={!canEditFieldName} onChange={(event) => update("label", event.target.value)} placeholder={canEditFieldName ? "Enter the field name" : "Select an FT Williams field"} /></Field>
            {isExtractionOnly ? <Field label="Document family" hint="Plan Worksheet fields come only from the protected fixed catalog."><select value="Schedule A - Custom" disabled><option>Schedule A - Custom</option></select></Field> : null}
            <Field label="Applies to"><select value={rule.applicability} onChange={(event) => update("applicability", event.target.value as FieldRule["applicability"])}><option value="BOTH">Both contract types</option><option value="EXPERIENCE">Experience rated only</option><option value="NONEXPERIENCE">Nonexperience rated only</option><option value="FORM_5500">Form 5500 only</option></select></Field>
            <Field label="Priority"><select value={rule.priority} onChange={(event) => update("priority", event.target.value as FieldRule["priority"])}><option>HIGH</option><option>MEDIUM</option><option>LOW</option><option>IGNORE</option></select></Field>
          </div>
        </EditorSection>
        <EditorSection number="2" title="EyeLevel aliases" description="Add the names EyeLevel may return, one per line, then test a real example.">
          <Field label="Aliases" hint={isFixedWorksheetField ? "Plan Worksheet labels are fixed and do not need client aliases." : "Add Schedule A label variations returned by the extraction service."}><textarea className="aliases-editor" value={aliases} readOnly={isFixedWorksheetField} onChange={(event) => { setAliases(event.target.value); setTestResult(null); }} placeholder="Insurance Carrier EIN&#10;Carrier federal EIN" /></Field>
          <div className="rule-test-box"><div><FlaskConical size={18} /><div><strong>Test the match</strong><span>Paste one field name exactly as EyeLevel returned it.</span></div></div><div className="rule-test-controls"><input value={sample} onChange={(event) => { setSample(event.target.value); setTestResult(null); }} placeholder="Sample EyeLevel field name" /><button type="button" disabled={busy || !sample.trim()} onClick={() => void testCurrentRule()}>{busy ? <InlineLoader label="Testing" /> : "Run test"}</button></div>{testResult ? <div className={`test-result ${testResult.valid && testResult.matched ? "pass" : "fail"}`}>{testResult.valid && testResult.matched ? <CheckCircle2 size={17} /> : <X size={17} />} {testResult.message}</div> : null}</div>
        </EditorSection>
        <EditorSection number="3" title="Review notes" description="Briefly explain when this rule should be used and why it is changing.">
          <Field label="Instructions for reviewers" hint="Optional"><textarea value={rule.client_notes || rule.notes || ""} onChange={(event) => update("client_notes", event.target.value)} placeholder="Explain when reviewers and the agent should use this field." /></Field><Field label="Change reason"><textarea value={reason} onChange={(event) => setReason(event.target.value)} /></Field>
        </EditorSection>
        <details className="advanced-rule-settings">
          <summary><div><strong>Advanced settings</strong><span>Technical values are generated automatically. Only change them if instructed by a technical administrator.</span></div><ChevronRight size={18} /></summary>
          <div className="advanced-rule-settings-content form-grid">
            <Field label="Stable key" hint="Controlled by the FT Williams catalog field."><input value={rule.key} readOnly /></Field>
            <Field label="FT Williams field"><input value={isExtractionOnly ? "Not mapped" : rule.ftw_field} readOnly /></Field>
            <Field label="FT Williams update tag"><input value={isExtractionOnly ? "Never sent" : rule.approved_update_tag || "Read-only field"} readOnly /></Field>
            <Field label="Supported years"><input value={catalogEntry?.supported_years.join(", ") || (isExtractionOnly ? "Extraction only" : "Select a catalog field")} readOnly /></Field>
            <Field label="FTW value format"><input value={catalogEntry ? `${catalogEntry.value_type} — ${catalogEntry.format_hint}` : "Not applicable"} readOnly /></Field>
            <Field label="FTW current tag"><input value={catalogEntry?.current_tag || (isExtractionOnly ? "Never queried" : "Resolved by FT Williams adapter")} readOnly /></Field>
            <Field label="Form section"><input value={rule.form_section || ""} readOnly /></Field>
            <Field label="Field type"><select value={rule.field_type} onChange={(event) => update("field_type", event.target.value)}><option>Dynamic</option><option>Static</option><option>Calculated</option></select></Field>
            <Field label="Existing FTW value"><select disabled={Boolean(catalogEntry && !catalogEntry.update_supported)} value={rule.existing_behavior || "Review Only"} onChange={(event) => update("existing_behavior", event.target.value)}><option>Update</option><option>Keep FTW</option><option>Review Only</option><option>Add</option></select></Field>
            <Field label="Empty FTW value"><select disabled={Boolean(catalogEntry && !catalogEntry.update_supported)} value={rule.new_behavior || "Add"} onChange={(event) => update("new_behavior", event.target.value)}><option>Add</option><option>Update</option><option>Review Only</option><option>Keep FTW</option></select></Field>
          </div>
        </details>
        {error ? <div className="form-error editor-error">{error}</div> : null}
      </div>
      <footer><button type="button" className="button-secondary" onClick={onClose}>Cancel</button><button type="button" className="button" disabled={busy} onClick={() => void save()}>{busy ? <InlineLoader label="Saving draft" /> : <><Save size={17} /> Save draft</>}</button></footer>
    </aside>
  </div>;
}

function EditorSection({ number, title, description, children }: { number: string; title: string; description: string; children: ReactNode }) { return <section className="editor-section"><div className="editor-section-heading"><span>{number}</span><div><h3>{title}</h3><p>{description}</p></div></div><div className="editor-section-content">{children}</div></section>; }
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) { return <label className="editor-field"><span>{label}</span>{children}{hint ? <small>{hint}</small> : null}</label>; }
function Info({ label, value }: { label: string; value: string }) { return <div className="info-card"><span>{label}</span><strong>{value}</strong></div>; }
function Definition({ label, value }: { label: string; value: string }) { return <div className="rule-definition"><span>{label}</span><strong>{value || "—"}</strong></div>; }
function RuleStatus({ status }: { status: FieldRule["status"] }) { return <span className={`rule-status rule-status-${status.toLowerCase()}`}>{titleCase(status)}</span>; }
function uniqueValues(values: string[]) { return Array.from(new Set(values.filter(Boolean))).sort(); }
function titleCase(value: string) { return value.toLowerCase().replace(/(^|_|\s)\w/g, (letter) => letter.toUpperCase()).replaceAll("_", " "); }
function applicabilityLabel(value: FieldRule["applicability"]) { return value === "BOTH" ? "Both types" : value === "EXPERIENCE" ? "Experience" : value === "NONEXPERIENCE" ? "Nonexperience" : "Form 5500"; }
function formatDate(value?: string) { if (!value) return "—"; return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value)); }
function emptyRule(): FieldRule { return { key: "", label: "", ftw_field: "", xml_tag: "", mapping_mode: "FTW_MAPPED", priority: "MEDIUM", source: "Schedule A", form_section: "Schedule A - Part I", field_type: "Dynamic", existing_or_new: "BOTH", existing_behavior: "Review Only", new_behavior: "Add", notes: "", client_notes: "", aliases: [], required: false, order: 0, applicability: "BOTH", status: "DRAFT", version: 1 }; }
