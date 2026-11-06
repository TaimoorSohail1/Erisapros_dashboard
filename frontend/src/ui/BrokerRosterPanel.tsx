import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { BrokerRosterReviewRequest, ScheduleABrokerRow, ScheduleABrokerRosterReview, FTWilliamsReview } from '../types';

type Draft = { row: ScheduleABrokerRow; source: string };
type Treatment = 'INCLUDED' | 'SUPPORTING_ONLY' | 'UNRESOLVED';

export function BrokerRosterPanel({ busy, currentRows, roster, rows, onSaveReview, onRefreshReview }: {
  busy: boolean;
  currentRows: Record<string, unknown>[];
  roster?: ScheduleABrokerRosterReview | null;
  rows: ScheduleABrokerRow[];
  onSaveReview: (rows: ScheduleABrokerRow[], review: BrokerRosterReviewRequest) => Promise<boolean>;
  onRefreshReview?: () => Promise<FTWilliamsReview>;
}) {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [treatments, setTreatments] = useState<Record<string, Treatment>>({});
  const [reason, setReason] = useState('');
  const [complete, setComplete] = useState(false);
  const [emptyEvidence, setEmptyEvidence] = useState('');
  const [error, setError] = useState('');
  const [context, setContext] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const disclosures = roster?.other_disclosures ?? [];
  const ready = roster?.mode === 'REPLACE';
  const numericMoney = (value?: string | null) => {
    const text = String(value ?? '').replace(/[$,\s]/g, '');
    if (!text) return NaN;
    return Number(/^\(.*\)$/.test(text) ? '-' + text.slice(1, -1) : text);
  };
  const formatMoney = (value?: string | null) => !Number.isFinite(numericMoney(value)) ? 'Unconfirmed' :
    new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(numericMoney(value));
  const purpose = (row: ScheduleABrokerRow) => {
    if ([row.commission_total, row.fee_total].some(v => !Number.isFinite(numericMoney(v)))) return 'Unclassified';
    const commission = numericMoney(row.commission_total) !== 0, fees = numericMoney(row.fee_total) !== 0;
    return commission && fees ? 'COMMISSIONS AND FEES' : fees ? 'FEES' : commission ? 'COMMISSIONS' : 'COMMISSION';
  };
  const address = (row: ScheduleABrokerRow) => [row.address_line_1, row.address_line_2, row.city, row.state, row.zip_code].filter(Boolean).join(', ');
  const currentValue = (row: Record<string, unknown>, name: string) => String(Object.entries(row)
    .find(([tag]) => new RegExp(`^${name}(?:XX|\\d{1,2})$`).test(tag))?.[1] ?? '');

  useEffect(() => { if (open) { heading.current?.focus(); heading.current?.scrollIntoView({ block: 'nearest' }); } }, [open]);
  if (!rows.length && !roster && !currentRows.length) return null;

  async function beginReview(event: React.MouseEvent<HTMLButtonElement>) {
    returnFocus.current = event.currentTarget;
    setError('');
    try {
      const fresh = onRefreshReview ? await onRefreshReview() : null;
      const reviewRoster = fresh?.schedule_a_broker_roster ?? roster;
      const reviewRows = fresh?.schedule_a_broker_rows ?? rows;
      setDrafts(reviewRows.map((row, index) => ({ row: { ...row },
        source: reviewRoster?.reviewer_confirmation?.row_sources[index]?.startsWith('DISCLOSURE:')
          ? reviewRoster.reviewer_confirmation.row_sources[index] : `SOURCE:${index}` })));
      setTreatments(Object.fromEntries((reviewRoster?.other_disclosures ?? []).filter(d => d.id).map(d => [d.id!,
        reviewRoster?.reviewer_confirmation?.disclosure_decisions[d.id!] ?? 'UNRESOLVED'])));
      setReason(''); setComplete(false); setEmptyEvidence('');
      setContext(reviewRoster?.review_context_sha256 ?? ''); setOpen(true);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to refresh broker review. Try again.'); }
  }

  function closeReview() { setOpen(false); returnFocus.current?.focus(); }

  function changeTreatment(id: string, treatment: Treatment) {
    setComplete(false);
    setTreatments(previous => ({ ...previous, [id]: treatment }));
    setDrafts(previous => {
      const retained = previous.filter(d => d.source !== `DISCLOSURE:${id}`);
      if (treatment !== 'INCLUDED') return retained;
      const existing = previous.find(d => d.source === `DISCLOSURE:${id}`);
      const item = disclosures.find(d => d.id === id)!;
      return [...retained, existing ?? { source: `DISCLOSURE:${id}`, row: {
        name: item.name ?? '', address_line_1: '', city: '', state: '', zip_code: '', organization_code: '3',
        commission_total: null, fee_total: null, source_page: item.page,
      } }];
    });
  }

  function edit(index: number, field: keyof ScheduleABrokerRow, value: string) {
    setComplete(false);
    setDrafts(previous => previous.map((item, i) => i === index ? { ...item, row: { ...item.row, [field]: value } } : item));
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setError('');
    if (!context) { setError('Refresh FTWilliam data to load the current source and destination before saving.'); return; }
    if (context !== roster?.review_context_sha256) { setError('Source or FTWilliam data changed. Cancel and reopen this review.'); return; }
    if (!complete || !reason.trim()) { setError('Confirm the complete list and enter the client instruction or review reason.'); return; }
    if (disclosures.some(d => !d.id || treatments[d.id] === 'UNRESOLVED' || !treatments[d.id])) {
      setError('Review each disclosure before confirming the replacement list.'); return;
    }
    if (drafts.some(d => purpose(d.row) === 'Unclassified')) { setError('Enter classified commission and fee amounts. Unknown amounts cannot default to zero.'); return; }
    try {
      const saved = await onSaveReview(drafts.map(d => ({ ...d.row, purpose: purpose(d.row) })), {
        expected_context: context, complete_list_confirmed: complete, reason: reason.trim(), empty_list_evidence: emptyEvidence.trim(),
        row_sources: drafts.map(d => d.source),
        excluded_source_indexes: rows.map((_, i) => i).filter(i => !drafts.some(d => d.source === `SOURCE:${i}`)),
        disclosure_decisions: disclosures.map(d => ({ disclosure_id: d.id!, treatment: treatments[d.id!] })),
      });
      if (saved) closeReview();
      else setError('Review was not saved. Check the error message above, correct the values or refresh FTWilliam data, then retry. Your draft is retained.');
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Review was not saved. Your draft is retained.'); }
  }

  const supporting = disclosures.filter(d => !roster?.reviewer_confirmation ||
    roster.reviewer_confirmation.disclosure_decisions[d.id ?? ''] !== 'INCLUDED');
  return <section className="schedule-a-broker-panel card" id="schedule-a-broker-rows">
    <div className="schedule-a-broker-panel-head"><h3>Schedule A brokers</h3>
      <span>{ready ? roster.replacement_count : 0} verified brokers · {ready ? 0 : Math.max(1, disclosures.length)} items need review</span></div>
    <p role="status">{ready ? `Will replace ${roster.existing_count} existing brokers with ${roster.replacement_count} verified broker${roster.replacement_count === 1 ? '' : 's'}.`
      : 'Paused: confirm payment classification and the complete broker list.'}</p>
    <div className="schedule-a-broker-table-wrap" aria-label="Schedule A broker rows" role="region" tabIndex={0}>
      <table className="schedule-a-broker-table"><thead><tr><th>Broker / person</th><th>Address</th><th>Payment</th><th>Purpose / status</th><th>Action</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={`source-${index}`}>
          <td data-label="Broker / person">{row.name}</td><td data-label="Address">{address(row)}</td>
          <td data-label="Payment">Commissions: {formatMoney(row.commission_total)}<br />Fees: {formatMoney(row.fee_total)}</td>
          <td data-label="Purpose / status">{purpose(row)}{!ready ? ' · Needs review' : ''}</td>
          <td><button type="button" className="button secondary" disabled={busy} onClick={beginReview}>Review &amp; edit</button></td>
        </tr>)}{supporting.map((item, index) => <tr key={item.id ?? `disclosure-${index}`}>
          <td data-label="Broker / person">{item.name}<small className="broker-draft-badge">Service-provider disclosure · Page {item.page}</small></td>
          <td data-label="Address">{item.address}</td><td data-label="Payment">{formatMoney(item.amount)}</td>
          <td data-label="Purpose / status">{roster?.reviewer_confirmation?.disclosure_decisions[item.id ?? ''] === 'SUPPORTING_ONLY' ? 'Supporting information only' : 'Unclassified'}</td>
          <td><button type="button" className="button secondary" disabled={busy} onClick={beginReview}>Review &amp; edit</button></td>
        </tr>)}</tbody></table>
    </div>
    {!rows.length && !supporting.length ? <button type="button" className="button secondary" disabled={busy} onClick={beginReview}>Review &amp; edit</button> : null}
    {!open && error ? <p role="alert">{error}</p> : null}
    {open ? <section className="broker-review-editor" role="region" aria-labelledby="broker-review-heading">
      <div className="broker-edit-toolbar"><h4 id="broker-review-heading" tabIndex={-1} ref={heading}>Review broker replacement</h4>
        <button type="button" className="button secondary" disabled={busy} onClick={closeReview}>Cancel</button></div>
      <p>Confirm which disclosures belong on this Schedule A and review the complete replacement list. Existing brokers stay unchanged until the review passes.</p>
      <details><summary>View evidence</summary>
        {(roster?.reasons ?? []).map((text, i) => <p key={i}>{text}</p>)}
        {disclosures.map((item, i) => <div key={i}><strong>{item.name} · Page {item.page} · Original amount {formatMoney(item.amount)}</strong><pre>{item.source_text}</pre></div>)}
        {(roster?.evidence ?? []).map((item, i) => <p key={`evidence-${i}`}>Page {item.page}: {item.source_text}</p>)}
      </details>
      <details><summary>Current brokers ({currentRows.length})</summary><ul>{currentRows.map((row, i) => <li key={i}>
        {['Name','AddressLine1','AddressLine2','City','State','ZipCode'].map(key => currentValue(row,key)).filter(Boolean).join(', ')}
        {` — Commissions: ${currentValue(row,'CommPdAmt') || 'Blank'}; Fees: ${currentValue(row,'FeesPdAmt') || 'Blank'}; Purpose: ${currentValue(row,'FeesPdText') || 'Blank'}`}
      </li>)}</ul></details>
      <form onSubmit={save}>
        {disclosures.map((item, index) => <label className="broker-disclosure-treatment" key={item.id ?? index}>
          {item.name} · Original amount {formatMoney(item.amount)} · Page {item.page}
          <select aria-label={`Schedule A treatment for ${item.name}`} disabled={busy || !item.id} value={treatments[item.id ?? ''] ?? 'UNRESOLVED'}
            onChange={event => changeTreatment(item.id!,event.target.value as Treatment)}>
            <option value="UNRESOLVED">Unresolved — keep paused</option>
            <option value="INCLUDED">Confirmed for this Schedule A</option>
            <option value="SUPPORTING_ONLY">Supporting information only</option>
          </select><small>This separate disclosure is not a verified Schedule A commission or fee until confirmed.</small>
        </label>)}
        <h4>Proposed replacement list ({drafts.length})</h4>
        {drafts.map(({ row, source }, index) => <fieldset className="broker-edit-form" key={source === 'NEW' ? `new-${index}` : source} disabled={busy}>
          <legend>Broker {index + 1}{row.source_page ? ` · Page ${row.source_page}` : ''}</legend>
          {([['Name','name',35],['Street','address_line_1',35],['Address line 2','address_line_2',35],['City','city',30],['State','state',2],['ZIP','zip_code',10]] as const)
            .map(([label, field, max]) => <label key={field}>{label}<input aria-label={`${label} for broker ${index + 1}`} required={field !== 'address_line_2'} maxLength={max}
              value={row[field] ?? ''} onChange={event => edit(index,field,event.target.value)} /></label>)}
          <label>Organization code<select required value={row.organization_code ?? '3'} onChange={event => edit(index,'organization_code',event.target.value)}>
            <option value="">Use default (3)</option>
            {[['0','Other'],['1','Banking or financial institution'],['2','Trust company'],['3','Insurance agent or broker'],['4','Non-insurance agent or broker'],['5','Third-party administrator'],['6','Investment company or mutual fund'],['7','Investment manager or adviser'],['8','Labor union'],['9','Foreign entity']].map(([code,label]) => <option key={code} value={code}>{code} — {label}</option>)}</select><small>Blank codes default to 3. Existing codes are preserved.</small></label>
          <label>Commission<input aria-label={`Commission for broker ${index + 1}`} required inputMode="decimal" value={row.commission_total ?? ''} onChange={event => edit(index,'commission_total',event.target.value)} /></label>
          <label>Fees<input aria-label={`Fees for broker ${index + 1}`} required inputMode="decimal" value={row.fee_total ?? ''} onChange={event => edit(index,'fee_total',event.target.value)} /></label>
          <label>Purpose<input readOnly value={purpose(row)} /></label>
          <button type="button" className="button danger" onClick={() => {
            setComplete(false);setDrafts(previous => previous.filter((_,i) => i !== index));
            if (source.startsWith('DISCLOSURE:')) setTreatments(previous => ({...previous,[source.slice(11)]:'UNRESOLVED'}));
          }}>Exclude from replacement</button>
        </fieldset>)}
        <button type="button" className="button secondary" disabled={busy} onClick={() => {setComplete(false);setDrafts(previous => [...previous,{source:'NEW',row:{name:'',commission_total:'0',fee_total:'0',organization_code:'3'}}]);}}>Add broker</button>
        {!drafts.length ? <label className="broker-review-confirmation">Evidence of no reportable Schedule A brokers<textarea required value={emptyEvidence} onChange={event => {setComplete(false);setEmptyEvidence(event.target.value);}} /></label> : null}
        <label className="broker-review-confirmation">Client instruction or review reason<textarea required maxLength={2000} disabled={busy} value={reason} onChange={event => {setComplete(false);setReason(event.target.value);}} /></label>
        <label className="broker-review-confirmation"><input type="checkbox" disabled={busy} checked={complete} onChange={event => setComplete(event.target.checked)} />I confirm this is the complete broker list for the selected Schedule A, including exclusions and payment classifications.</label>
        <p>Confirmed unpaid brokers are excluded from the outgoing replacement. Saving does not manually send; normal automatic processing may resume when all reviews pass.</p>
        {error ? <p role="alert">{error}</p> : null}
        <button className="button primary" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save review'}</button>
      </form>
    </section> : null}
  </section>;
}
