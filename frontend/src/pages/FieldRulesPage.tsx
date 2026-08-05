import { CheckCircle2, ChevronRight, Database, Layers3, Search, ShieldCheck, SlidersHorizontal, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { listFieldRules } from "../api";
import type { FieldRule } from "../types";

const PRIORITIES = ["ALL", "HIGH", "MEDIUM", "LOW"] as const;

export function FieldRulesPage() {
  const [rules, setRules] = useState<FieldRule[]>([]);
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState("ALL");
  const [section, setSection] = useState("ALL");
  const [type, setType] = useState("ALL");
  const [selectedRule, setSelectedRule] = useState<FieldRule | null>(null);

  useEffect(() => {
    listFieldRules().then(setRules).catch((error) => setMessage(error.message));
  }, []);

  const highCount = rules.filter((rule) => rule.priority === "HIGH").length;
  const mediumCount = rules.filter((rule) => rule.priority === "MEDIUM").length;
  const lowCount = rules.filter((rule) => rule.priority === "LOW").length;
  const updateCount = rules.filter((rule) => [rule.existing_behavior, rule.new_behavior].includes("Update")).length;

  const sections = useMemo(() => uniqueValues(rules.map((rule) => rule.form_section || rule.source)), [rules]);
  const types = useMemo(() => uniqueValues(rules.map((rule) => rule.field_type)), [rules]);

  const filteredRules = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return rules.filter((rule) => {
      const haystack = [
        rule.label,
        rule.form_section,
        rule.field_type,
        rule.priority,
        rule.existing_behavior,
        rule.new_behavior,
        rule.notes,
        rule.client_notes,
        ...rule.aliases
      ].join(" ").toLowerCase();

      return (
        (!normalizedQuery || haystack.includes(normalizedQuery)) &&
        (priority === "ALL" || rule.priority === priority) &&
        (section === "ALL" || (rule.form_section || rule.source) === section) &&
        (type === "ALL" || rule.field_type === type)
      );
    });
  }, [query, priority, section, type, rules]);

  const groupedRules = useMemo(() => {
    return filteredRules.reduce<Record<string, FieldRule[]>>((groups, rule) => {
      const groupName = rule.form_section || rule.source || "Other";
      groups[groupName] = groups[groupName] || [];
      groups[groupName].push(rule);
      return groups;
    }, {});
  }, [filteredRules]);

  const sectionStats = useMemo(() => {
    return sections.map((name) => ({
      name,
      count: rules.filter((rule) => (rule.form_section || rule.source) === name).length
    }));
  }, [rules, sections]);

  function resetFilters() {
    setQuery("");
    setPriority("ALL");
    setSection("ALL");
    setType("ALL");
  }

  function aliasPreview(rule: FieldRule) {
    const visible = rule.aliases.slice(0, 3).join(", ");
    const remaining = rule.aliases.length - Math.min(rule.aliases.length, 3);
    return (
      <span>
        {visible || "-"}
        {remaining > 0 ? <span className="more-count">+{remaining}</span> : null}
      </span>
    );
  }

  function notePreview(rule: FieldRule) {
    const value = rule.client_notes || rule.notes || "-";
    return value.length > 90 ? `${value.slice(0, 90).trim()}...` : value;
  }

  return (
    <div className="rules-page">
      <div className="rules-hero">
        <div className="rules-hero-copy">
          <span className="eyebrow">FT Williams Source Of Truth</span>
          <h1 className="page-title">Field Rules</h1>
          <div className="subtle">
            Official Schedule A and Form 5500 field inventory, normalized with client aliases for extraction matching.
          </div>
        </div>
        <div className="rules-hero-panel">
          <div>
            <span>Visible Fields</span>
            <strong>{filteredRules.length}</strong>
          </div>
          <div>
            <span>Total Active Rules</span>
            <strong>{rules.length}</strong>
          </div>
        </div>
      </div>

      <div className="rules-kpi-grid">
        <MetricCard icon={<Database size={20} />} label="Total Active Fields" value={rules.length} detail="No ignored rows shown" tone="green" />
        <MetricCard icon={<ShieldCheck size={20} />} label="High Priority" value={highCount} detail="Must be captured or reviewed" tone="red" />
        <MetricCard icon={<Layers3 size={20} />} label="Medium + Low" value={mediumCount + lowCount} detail={`${mediumCount} medium, ${lowCount} low`} tone="blue" />
        <MetricCard icon={<CheckCircle2 size={20} />} label="Update-Capable" value={updateCount} detail="Existing or new behavior includes update" tone="amber" />
      </div>

      <div className="card rules-toolbar">
        <div className="search-field">
          <Search size={17} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search field, alias, note, or section" />
        </div>
        <div className="filter-group" aria-label="Priority filter">
          {PRIORITIES.map((item) => (
            <button key={item} type="button" className={priority === item ? "active" : ""} onClick={() => setPriority(item)}>
              {item === "ALL" ? "All" : item}
            </button>
          ))}
        </div>
        <select className="select-filter" value={section} onChange={(event) => setSection(event.target.value)} aria-label="Section">
          <option value="ALL">All sections</option>
          {sections.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <select className="select-filter" value={type} onChange={(event) => setType(event.target.value)} aria-label="Field type">
          <option value="ALL">All types</option>
          {types.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <button type="button" className="toolbar-reset" onClick={resetFilters}><SlidersHorizontal size={16} /> Reset filters</button>
      </div>

      <div className="section-strip" aria-label="Field coverage by section">
        {sectionStats.map((item) => (
          <button
            type="button"
            key={item.name}
            className={section === item.name ? "active" : ""}
            onClick={() => setSection(item.name)}
          >
            <span>{item.name}</span>
            <strong>{item.count}</strong>
          </button>
        ))}
      </div>

      {message ? <div className="card card-pad">{message}</div> : null}
      <div className="card table-wrap rules-table-card">
        <table className="rules-table">
          <thead>
            <tr>
              <th className="rules-field">Official FT Williams Field</th>
              <th className="rules-type">Type</th>
              <th className="rules-priority">Priority</th>
              <th className="rules-behavior">Behavior</th>
              <th className="rules-aliases">Aliases</th>
              <th className="rules-notes">Notes</th>
              <th className="rules-open"></th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(groupedRules).map(([groupName, groupRules]) => (
              <Fragment key={groupName}>
                <tr className="section-row" key={`${groupName}:section`}>
                  <td colSpan={7}>
                    <span>{groupName}</span>
                    <strong>{groupRules.length} fields</strong>
                  </td>
                </tr>
                {groupRules.map((rule) => (
                  <tr key={rule.key} className={`clickable-row row-${rule.priority.toLowerCase()}`} onClick={() => setSelectedRule(rule)}>
                    <td>
                      <div className="field-title-line">
                        <span className={`priority-dot dot-${rule.priority.toLowerCase()}`} />
                        <strong>{rule.label}</strong>
                      </div>
                      <code className="field-code" title={rule.xml_tag || ""}>{rule.xml_tag}</code>
                    </td>
                    <td>{rule.field_type}</td>
                    <td><span className={`badge priority-${rule.priority.toLowerCase()}`}>{rule.priority}</span></td>
                    <td>
                      <div className="behavior-stack">
                        <span>Existing: <strong>{rule.existing_behavior || "-"}</strong></span>
                        <span>New: <strong>{rule.new_behavior || "-"}</strong></span>
                      </div>
                    </td>
                    <td className="subtle">{aliasPreview(rule)}</td>
                    <td className="subtle">{notePreview(rule)}</td>
                    <td><ChevronRight size={18} className="row-chevron" /></td>
                  </tr>
                ))}
              </Fragment>
            ))}
            {!filteredRules.length ? (
              <tr>
                <td colSpan={7} className="empty-state">No field rules match the current filters.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {selectedRule ? <RuleDrawer rule={selectedRule} onClose={() => setSelectedRule(null)} /> : null}
    </div>
  );
}

function MetricCard({ icon, label, value, detail, tone }: { icon: ReactNode; label: string; value: number; detail: string; tone: string }) {
  return (
    <div className={`metric-card metric-${tone}`}>
      <div className="metric-icon">{icon}</div>
      <div>
        <div className="kpi-label">{label}</div>
        <div className="kpi-value">{value}</div>
        <div className="metric-detail">{detail}</div>
      </div>
    </div>
  );
}

function RuleDrawer({ rule, onClose }: { rule: FieldRule; onClose: () => void }) {
  return (
    <div className="drawer-layer" role="dialog" aria-modal="true">
      <button className="drawer-scrim" type="button" onClick={onClose} aria-label="Close field details" />
      <aside className="rule-drawer">
        <div className={`drawer-head drawer-${rule.priority.toLowerCase()}`}>
          <div>
            <div className="subtle">{rule.form_section || rule.source}</div>
            <h2>{rule.label}</h2>
            <code className="field-code" title={rule.xml_tag || ""}>{rule.xml_tag}</code>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button>
        </div>
        <div className="drawer-grid">
          <Info label="Priority" value={rule.priority} />
          <Info label="Field Type" value={rule.field_type} />
          <Info label="Existing" value={rule.existing_behavior || "-"} />
          <Info label="New" value={rule.new_behavior || "-"} />
        </div>
        <section className="drawer-section rule-purpose">
          <h3>Why This Rule Exists</h3>
          <p>
            This is the canonical FT Williams field the extraction output must map into before the reviewer approves the filing.
          </p>
        </section>
        <section className="drawer-section">
          <h3>Aliases Used For Matching <span>{rule.aliases.length}</span></h3>
          <div className="alias-chips">
            {rule.aliases.length ? rule.aliases.map((alias) => <span key={alias}>{alias}</span>) : <em>No aliases configured.</em>}
          </div>
        </section>
        <section className="drawer-section">
          <h3>Notes</h3>
          <p>{rule.notes || "-"}</p>
        </section>
        <section className="drawer-section">
          <h3>Client Notes</h3>
          <p>{rule.client_notes || "-"}</p>
        </section>
      </aside>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function uniqueValues(values: Array<string | null | undefined>) {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort((a, b) => a.localeCompare(b));
}
