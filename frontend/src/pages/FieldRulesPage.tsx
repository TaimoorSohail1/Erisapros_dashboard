import {
  Archive,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Database,
  Edit3,
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
import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  disableFieldRule,
  getFieldRuleHistory,
  listFieldRules,
  publishFieldRule,
  rollbackFieldRule,
  saveFieldRuleDraft,
  testFieldRule,
} from "../api";
import type { FieldRule } from "../types";

const PRIORITIES = ["ALL", "HIGH", "MEDIUM", "LOW"] as const;
const STATUS_TABS = ["ALL", "PUBLISHED", "DRAFT", "DISABLED"] as const;

type EditorMode = "add" | "edit" | "clone";
type RuleEditorState = { mode: EditorMode; rule: FieldRule };

export function FieldRulesPage() {
  const [rules, setRules] = useState<FieldRule[]>([]);
  const [publishedVersion, setPublishedVersion] = useState("");
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

  const refresh = useCallback(async (preferredKey?: string) => {
    setLoading(true);
    setMessage("");
    try {
      const payload = await listFieldRules();
      setRules(payload.field_rules);
      setPublishedVersion(payload.published_version);
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
  const updateCount = activeRules.filter((rule) => [rule.existing_behavior, rule.new_behavior].includes("Update")).length;
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
      copy.key = `${copy.key}_copy`;
      copy.label = `${copy.label} (Copy)`;
      copy.status = "DRAFT";
      copy.version = 1;
    }
    setEditor({ mode, rule: copy });
  }

  function flash(value: string) {
    setNotice(value);
    window.setTimeout(() => setNotice(""), 5000);
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
          <p className="rules-intro">Control how EyeLevel values are normalized, reviewed, and safely proposed to FT Williams.</p>
          <div className="rules-version-line">
            <span className="status-dot-live" /> Published rule set <code>{publishedVersion || "loading"}</code>
            <span>Changes use a draft, test, and publish workflow.</span>
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
        <MetricCard icon={<Database size={20} />} label="Active rules" value={activeRules.length} detail="Published mapping inventory" tone="green" />
        <MetricCard icon={<ShieldCheck size={20} />} label="High priority" value={highCount} detail="Require capture or review" tone="red" />
        <MetricCard icon={<Edit3 size={20} />} label="Draft changes" value={draftCount} detail={draftCount ? "Awaiting test and publish" : "No unpublished changes"} tone="blue" />
        <MetricCard icon={<Layers3 size={20} />} label="Update-capable" value={updateCount} detail="May propose FTW changes" tone="amber" />
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

      <div className="rules-table-summary"><strong>{filteredRules.length}</strong> rules shown <span>•</span> Select a row to inspect mapping details and history.</div>
      <div className="card table-wrap rules-table-card rules-admin-table-card">
        <table className="rules-table rules-admin-table">
          <thead><tr><th>Official FT Williams field</th><th>Applicability</th><th>Priority</th><th>Behavior</th><th>Aliases</th><th>Status</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {Object.entries(groupedRules).map(([groupName, groupRules]) => (
              <Fragment key={groupName}>
                <tr className="section-row"><td colSpan={8}><span>{groupName}</span><strong>{groupRules.length} fields</strong></td></tr>
                {groupRules.map((rule) => (
                  <tr key={`${rule.key}:${rule.version}:${rule.status}`} className={`clickable-row row-${rule.priority.toLowerCase()}`} onClick={() => setSelectedRule(rule)}>
                    <td><div className="field-title-line"><span className={`priority-dot dot-${rule.priority.toLowerCase()}`} /><strong>{rule.label}</strong></div><code className="field-code">{rule.xml_tag || rule.key}</code></td>
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
            {loading ? <tr><td colSpan={8} className="empty-state">Loading the published mapping inventory…</td></tr> : null}
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
          onClose={() => setEditor(null)}
          onSaved={async (rule) => { setEditor(null); flash("Draft saved. Test and publish it when ready."); await refresh(rule.key); }}
        />
      ) : null}
    </div>
  );
}

function MetricCard({ icon, label, value, detail, tone }: { icon: ReactNode; label: string; value: number; detail: string; tone: string }) {
  return <div className={`metric-card metric-${tone}`}><div className="metric-icon">{icon}</div><div><div className="kpi-label">{label}</div><div className="kpi-value">{value}</div><div className="metric-detail">{detail}</div></div></div>;
}

function RuleDrawer({ rule, canManage, onClose, onEdit, onClone, onChanged }: {
  rule: FieldRule; canManage: boolean; onClose: () => void; onEdit: () => void; onClone: () => void; onChanged: (message: string) => Promise<void>;
}) {
  const [history, setHistory] = useState<FieldRule[]>([]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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

  return <div className="drawer-layer" role="dialog" aria-modal="true">
    <button className="drawer-scrim" type="button" onClick={onClose} aria-label="Close field details" />
    <aside className="rule-drawer rule-admin-drawer">
      <div className={`drawer-head drawer-${rule.priority.toLowerCase()}`}><div><div className="drawer-status-line"><RuleStatus status={rule.status} /><span>Version {rule.version}</span></div><h2>{rule.label}</h2><code className="field-code">{rule.xml_tag || rule.key}</code></div><button type="button" className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button></div>
      {canManage ? <div className="drawer-action-bar"><button type="button" onClick={onEdit}><Edit3 size={16} /> Edit</button><button type="button" onClick={onClone}><Copy size={16} /> Clone</button></div> : null}
      <div className="drawer-grid"><Info label="Priority" value={rule.priority} /><Info label="Applicability" value={applicabilityLabel(rule.applicability)} /><Info label="Existing record" value={rule.existing_behavior || "—"} /><Info label="New record" value={rule.new_behavior || "—"} /></div>
      <section className="drawer-section"><h3>Mapping identity</h3><Definition label="Stable key" value={rule.key} /><Definition label="FT Williams field" value={rule.ftw_field} /><Definition label="Form section" value={rule.form_section || rule.source} /><Definition label="Field type" value={rule.field_type} /></section>
      <section className="drawer-section"><h3>Aliases used by the agent <span>{rule.aliases.length}</span></h3><div className="alias-chips">{rule.aliases.length ? rule.aliases.map((alias) => <span key={alias}>{alias}</span>) : <em>No aliases configured.</em>}</div></section>
      <section className="drawer-section"><h3>Rule guidance</h3><p>{rule.client_notes || rule.notes || "No guidance has been added."}</p></section>
      {canManage ? <section className="drawer-section publish-panel"><h3>Controlled change</h3><p>Every publish, disable, or rollback requires a reason and is preserved in history.</p><textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why is this change required?" />{error ? <div className="form-error">{error}</div> : null}<div className="publish-actions">{rule.status === "DRAFT" ? <button className="button" disabled={busy} onClick={() => void perform("publish")}><Sparkles size={16} /> Publish draft</button> : null}{rule.status === "PUBLISHED" ? <button className="button button-danger-soft" disabled={busy} onClick={() => void perform("disable")}><Archive size={16} /> Disable rule</button> : null}</div></section> : null}
      {canManage ? <section className="drawer-section"><h3><History size={17} /> Version history</h3><div className="rule-history-list">{history.map((item) => <div key={item.id || `${item.version}:${item.status}:${item.created_at}`}><div><strong>v{item.version} · {titleCase(item.status)}</strong><span>{item.updated_by || "System"} · {formatDate(item.updated_at)}</span><p>{item.change_reason || "No reason recorded."}</p></div>{item.status === "PUBLISHED" && item.version !== rule.version ? <button type="button" disabled={busy || !reason.trim()} onClick={() => void perform("rollback", item.version)}>Restore</button> : null}</div>)}</div></section> : null}
    </aside>
  </div>;
}

function RuleEditor({ state, onClose, onSaved }: { state: RuleEditorState; onClose: () => void; onSaved: (rule: FieldRule) => Promise<void> }) {
  const [rule, setRule] = useState<FieldRule>(state.rule);
  const [reason, setReason] = useState(state.mode === "add" ? "Add a new field mapping." : state.mode === "clone" ? "Clone an existing field mapping." : "Update field mapping guidance.");
  const [aliases, setAliases] = useState(state.rule.aliases.join("\n"));
  const [sample, setSample] = useState(state.rule.aliases[0] || state.rule.label);
  const [testResult, setTestResult] = useState<{ valid: boolean; matched: boolean; message: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const isNewIdentity = state.mode !== "edit";

  function update<K extends keyof FieldRule>(key: K, value: FieldRule[K]) { setRule((current) => ({ ...current, [key]: value })); setTestResult(null); }
  function preparedRule(): FieldRule { return { ...rule, aliases: aliases.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean), status: "DRAFT" }; }

  async function testCurrentRule() {
    setBusy(true); setError("");
    try { setTestResult(await testFieldRule(preparedRule(), sample)); }
    catch (testError) { setError(testError instanceof Error ? testError.message : "Rule test failed."); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!rule.key.trim() || !rule.label.trim() || !rule.ftw_field.trim()) return setError("Stable key, official label, and FT Williams field are required.");
    if (!reason.trim()) return setError("A change reason is required.");
    setBusy(true); setError("");
    try { await onSaved(await saveFieldRuleDraft(preparedRule(), reason)); }
    catch (saveError) { setError(saveError instanceof Error ? saveError.message : "Draft could not be saved."); }
    finally { setBusy(false); }
  }

  return <div className="drawer-layer editor-layer" role="dialog" aria-modal="true">
    <button className="drawer-scrim" type="button" onClick={onClose} aria-label="Close editor" />
    <aside className="rule-editor-panel">
      <header><div><span className="eyebrow">{state.mode === "add" ? "New mapping" : state.mode === "clone" ? "Clone mapping" : "Create next version"}</span><h2>{state.mode === "add" ? "Add field rule" : state.mode === "clone" ? "Clone field rule" : `Edit ${state.rule.label}`}</h2><p>Save as a draft, validate with a real source label, then publish from the rule details.</p></div><button className="icon-button" type="button" onClick={onClose}><X size={20} /></button></header>
      <div className="rule-editor-body">
        <EditorSection number="1" title="Field identity" description="The canonical destination and stable internal identity.">
          <div className="form-grid"><Field label="Official FT Williams label"><input value={rule.label} onChange={(event) => update("label", event.target.value)} /></Field><Field label="Stable key" hint={isNewIdentity ? "Use lowercase words separated by underscores." : "Locked after creation."}><input value={rule.key} disabled={!isNewIdentity} onChange={(event) => update("key", slugKey(event.target.value))} /></Field><Field label="FT Williams field"><input value={rule.ftw_field} onChange={(event) => update("ftw_field", event.target.value)} /></Field><Field label="FT Williams XML tag"><input value={rule.xml_tag || ""} onChange={(event) => update("xml_tag", event.target.value)} /></Field></div>
        </EditorSection>
        <EditorSection number="2" title="Scope and behavior" description="Controls where the field appears and whether it can propose an update.">
          <div className="form-grid"><Field label="Form section"><input value={rule.form_section || ""} onChange={(event) => update("form_section", event.target.value)} placeholder="Schedule A - Part III" /></Field><Field label="Field type"><select value={rule.field_type} onChange={(event) => update("field_type", event.target.value)}><option>Dynamic</option><option>Static</option><option>Calculated</option></select></Field><Field label="Applicability"><select value={rule.applicability} onChange={(event) => update("applicability", event.target.value as FieldRule["applicability"])}><option value="BOTH">Both contract types</option><option value="EXPERIENCE">Experience rated</option><option value="NONEXPERIENCE">Nonexperience rated</option><option value="FORM_5500">Form 5500</option></select></Field><Field label="Priority"><select value={rule.priority} onChange={(event) => update("priority", event.target.value as FieldRule["priority"])}><option>HIGH</option><option>MEDIUM</option><option>LOW</option><option>IGNORE</option></select></Field><Field label="Existing record behavior"><select value={rule.existing_behavior || "Review Only"} onChange={(event) => update("existing_behavior", event.target.value)}><option>Update</option><option>Keep FTW</option><option>Review Only</option><option>Add</option></select></Field><Field label="New record behavior"><select value={rule.new_behavior || "Add"} onChange={(event) => update("new_behavior", event.target.value)}><option>Add</option><option>Update</option><option>Review Only</option><option>Keep FTW</option></select></Field></div>
        </EditorSection>
        <EditorSection number="3" title="Agent matching" description="One alias per line. Exact labels are preferred before normalized and AI-assisted matching.">
          <Field label="Aliases"><textarea className="aliases-editor" value={aliases} onChange={(event) => { setAliases(event.target.value); setTestResult(null); }} placeholder="Insurance Carrier EIN&#10;Carrier federal EIN" /></Field>
          <div className="rule-test-box"><div><FlaskConical size={18} /><div><strong>Test this mapping</strong><span>Paste a source label exactly as EyeLevel may return it.</span></div></div><div className="rule-test-controls"><input value={sample} onChange={(event) => setSample(event.target.value)} placeholder="Sample extracted field name" /><button type="button" disabled={busy || !sample.trim()} onClick={() => void testCurrentRule()}>Run test</button></div>{testResult ? <div className={`test-result ${testResult.valid && testResult.matched ? "pass" : "fail"}`}>{testResult.valid && testResult.matched ? <CheckCircle2 size={17} /> : <X size={17} />} {testResult.message}</div> : null}</div>
        </EditorSection>
        <EditorSection number="4" title="Guidance and audit" description="Explain the business rule and why this version is needed.">
          <Field label="Rule notes"><textarea value={rule.client_notes || rule.notes || ""} onChange={(event) => update("client_notes", event.target.value)} placeholder="Explain when reviewers and the agent should use this field." /></Field><Field label="Required change reason"><textarea value={reason} onChange={(event) => setReason(event.target.value)} /></Field>
        </EditorSection>
        {error ? <div className="form-error editor-error">{error}</div> : null}
      </div>
      <footer><button type="button" className="button-secondary" onClick={onClose}>Cancel</button><button type="button" className="button" disabled={busy} onClick={() => void save()}><Save size={17} /> {busy ? "Saving…" : "Save draft"}</button></footer>
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
function slugKey(value: string) { return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""); }
function emptyRule(): FieldRule { return { key: "", label: "", ftw_field: "", xml_tag: "", priority: "MEDIUM", source: "Schedule A", form_section: "Schedule A - Part I", field_type: "Dynamic", existing_or_new: "BOTH", existing_behavior: "Review Only", new_behavior: "Add", notes: "", client_notes: "", aliases: [], required: false, order: 0, applicability: "BOTH", status: "DRAFT", version: 1 }; }
