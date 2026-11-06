import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { BrokerRosterReviewRequest, ScheduleABrokerRow, ScheduleABrokerRosterReview, FTWilliamsReview } from '../types';

type Disclosure = ScheduleABrokerRosterReview['other_disclosures'][number];
type Item = { source: string; row: ScheduleABrokerRow; included: boolean; disclosure?: Disclosure;
  treatment?: 'INCLUDED' | 'SUPPORTING_ONLY' | 'UNRESOLVED' };
type Draft = { items: Item[]; sourceRows: ScheduleABrokerRow[]; roster?: ScheduleABrokerRosterReview | null;
  context: string; dirty: boolean; emptyEvidence: string };
const moneyText = (value?: string | null) => {
  const text = String(value ?? '').replace(/[$,\s]/g, '');
  return /^\(.*\)$/.test(text) ? '-' + text.slice(1, -1) : text;
};
const cents = (value?: string | null): bigint | null => {
  const match = /^([+-]?)(\d+)(?:\.(\d{1,2}))?$/.exec(moneyText(value));
  return match ? (match[1] === '-' ? -1n : 1n) * (BigInt(match[2]) * 100n + BigInt((match[3] ?? '').padEnd(2, '0'))) : null;
};
const formatMoney = (value?: string | null) => {
  const amount = cents(value);
  if (amount === null) return 'Unconfirmed';
  const absolute = amount < 0n ? -amount : amount;
  return `${amount < 0n ? '-' : ''}$${new Intl.NumberFormat('en-US').format(absolute / 100n)}.${String(absolute % 100n).padStart(2,'0')}`;
};
const purpose = (row: ScheduleABrokerRow) => {
  const commission = cents(row.commission_total), fees = cents(row.fee_total);
  if (commission === null || fees === null) return 'Unclassified';
  return commission !== 0n && fees !== 0n ? 'COMMISSIONS AND FEES' : fees !== 0n ? 'FEES' : commission !== 0n ? 'COMMISSIONS' : 'COMMISSION';
};
const address = (row: ScheduleABrokerRow) => [row.address_line_1, row.address_line_2, row.city, row.state, row.zip_code].filter(Boolean).join(', ');
const currentValue = (row: Record<string, unknown>, name: string) => String(Object.entries(row)
  .find(([tag]) => new RegExp(`^${name}(?:XX|\\d{1,2})$`).test(tag))?.[1] ?? '');

function makeDraft(rows: ScheduleABrokerRow[], roster?: ScheduleABrokerRosterReview | null): Draft {
  const disclosures = roster?.other_disclosures ?? [];
  const items: Item[] = rows.map((row, index) => {
    const previous = roster?.reviewer_confirmation?.row_sources[index];
    const disclosure = previous?.startsWith('DISCLOSURE:') ? disclosures.find(d => `DISCLOSURE:${d.id}` === previous) : undefined;
    return { source: disclosure ? previous! : `SOURCE:${index}`, row: { ...row }, included: true,
      disclosure, treatment: disclosure ? 'INCLUDED' : undefined };
  });
  disclosures.forEach((disclosure, index) => {
    if (items.some(item => item.source === `DISCLOSURE:${disclosure.id}`)) return;
    const treatment = roster?.reviewer_confirmation?.disclosure_decisions[disclosure.id ?? ''] ?? 'UNRESOLVED';
    items.push({ source: `DISCLOSURE:${disclosure.id ?? `missing-${index}`}`, disclosure, treatment, included: false,
      row: { name: disclosure.name ?? '', commission_total: null, fee_total: null, organization_code: '3', source_page: disclosure.page } });
  });
  return { items, sourceRows: rows, roster, context: roster?.review_context_sha256 ?? '', dirty: false, emptyEvidence: '' };
}
function itemProblem(item: Item): string | null {
  if (item.disclosure && (!item.disclosure.id || item.treatment === 'UNRESOLVED')) return 'Confirm this payment’s Schedule A treatment and classification.';
  if (!item.included) return null;
  const commission = cents(item.row.commission_total), fees = cents(item.row.fee_total);
  if (commission === null || fees === null) return 'Confirm commission and fee amounts. Unknown amounts stay unconfirmed.';
  if (item.disclosure && (cents(item.disclosure.amount) === null || commission + fees !== cents(item.disclosure.amount))) return `Commission plus fees must equal the original ${formatMoney(item.disclosure.amount)}.`;
  if (['name', 'address_line_1', 'city', 'state', 'zip_code'].some(key => !String(item.row[key as keyof ScheduleABrokerRow] ?? '').trim())) return 'Complete the broker name and address.';
  return null;
}

export function BrokerRosterPanel({ busy, currentRows, roster, rows, onSaveReview, onRefreshReview, onReviewDestination }: {
  busy: boolean; currentRows: Record<string, unknown>[]; roster?: ScheduleABrokerRosterReview | null; rows: ScheduleABrokerRow[];
  onSaveReview: (rows: ScheduleABrokerRow[], review: BrokerRosterReviewRequest) => Promise<boolean>;
  onRefreshReview?: () => Promise<FTWilliamsReview>;
  onReviewDestination?: () => void;
}) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<{ index: number; item: Item | null; emptyEvidence: string } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [approvedContext, setApprovedContext] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const approvalInFlight = useRef(false);
  const view = draft ?? makeDraft(rows, roster);
  const stale = Boolean(draft && draft.context !== roster?.review_context_sha256);
  const destinationProblem = view.roster?.destination_ready === false ? view.roster.destination_reason || 'Select and refresh the correct Schedule A before approving brokers.' : null;
  const approved = !destinationProblem && !draft?.dirty && (roster?.mode === 'REPLACE' || Boolean(approvedContext && approvedContext === roster?.review_context_sha256));
  const problem = stale ? 'Source or FTWilliam data changed. Cancel edits and reopen Edit to refresh.' :
    destinationProblem ?? view.items.map(itemProblem).find(Boolean) ?? (!view.items.some(item => item.included) && !view.emptyEvidence.trim() ? 'Confirm the complete source broker list before replacing current brokers.' : null);
  const disabled = busy || pending;
  useEffect(() => { if (editing) { heading.current?.focus(); heading.current?.scrollIntoView({ block: 'nearest' }); } }, [Boolean(editing)]);
  if (!rows.length && !roster && !currentRows.length) return null;

  async function refreshedDraft(): Promise<Draft> {
    const fresh = onRefreshReview ? await onRefreshReview() : null;
    const next = makeDraft(fresh?.schedule_a_broker_rows ?? rows, fresh?.schedule_a_broker_roster ?? roster);
    if (draft?.dirty && draft.context !== next.context) throw new Error('Source or FTWilliam data changed. Your edits are retained. Cancel edits, then reopen Edit to refresh.');
    return draft?.dirty ? { ...draft, roster: next.roster } : next;
  }
  async function beginEdit(event: React.MouseEvent<HTMLButtonElement>, index: number) {
    returnFocus.current = event.currentTarget; setPending(true); setError(''); setMessage('');
    try {
      const next = await refreshedDraft(); setDraft(next);
      if (next.context !== view.context) throw new Error('Broker data refreshed. Check the displayed rows and click Edit again.');
      setEditing({ index, item: next.items[index] ? { ...next.items[index], row: { ...next.items[index].row } } : null, emptyEvidence: next.emptyEvidence });
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to load the broker. Try again.'); }
    finally { setPending(false); }
  }
  function closeEdit() { setEditing(null); setError(''); returnFocus.current?.focus(); }
  function editRow(field: keyof ScheduleABrokerRow, value: string) {
    setEditing(previous => previous?.item ? { ...previous, item: { ...previous.item, row: { ...previous.item.row, [field]: value } } } : previous);
  }
  function saveChanges(event: FormEvent) {
    event.preventDefault();
    if (!draft || !editing) return;
    const issue = editing.item && itemProblem(editing.item);
    if (issue) { setError(issue); return; }
    setDraft({ ...draft, dirty: true, emptyEvidence: editing.emptyEvidence,
      items: draft.items.map((item, index) => index === editing.index && editing.item ? editing.item : item) });
    closeEdit(); setMessage('Changes ready. Click Approve to save the review and continue.');
  }
  async function approve() {
    if (disabled || approved || problem || editing || approvalInFlight.current) return;
    approvalInFlight.current = true;
    setPending(true); setError(''); setMessage('');
    try {
      const next = await refreshedDraft();
      if (next.roster?.destination_ready === false) { setDraft(next); throw new Error(next.roster.destination_reason || 'Select and refresh the correct Schedule A before approving brokers.'); }
      if (next.context !== view.context || !next.context) { setDraft(next); throw new Error('Broker data refreshed. Check the displayed list before approving.'); }
      const issue = next.items.map(itemProblem).find(Boolean);
      if (issue) throw new Error(issue);
      const included = next.items.filter(item => item.included);
      const saved = await onSaveReview(included.map(item => ({ ...item.row, purpose: purpose(item.row) })), {
        expected_context: next.context, complete_list_confirmed: true,
        reason: 'Reviewer approved the complete displayed Schedule A broker list, including exclusions and payment classifications.',
        empty_list_evidence: next.emptyEvidence.trim(), row_sources: included.map(item => item.source),
        excluded_source_indexes: next.sourceRows.map((_, index) => index).filter(index => !included.some(item => item.source === `SOURCE:${index}`)),
        disclosure_decisions: next.items.filter(item => item.disclosure).map(item => ({ disclosure_id: item.disclosure!.id!, treatment: item.treatment! })),
      });
      if (!saved) throw new Error('Approval was not saved. Your edits are retained. Check the error above and try again.');
      setApprovedContext(next.context); setDraft(null); setMessage('Approved. Automatic processing continues when the other filing reviews are clear.');
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Approval was not saved. Your edits are retained.'); }
    finally { approvalInFlight.current = false; setPending(false); }
  }
  const approveButton = <button type="button" className="button primary" aria-describedby="broker-approval-scope"
    disabled={disabled || approved || Boolean(problem) || Boolean(editing)} onClick={approve}>{pending ? 'Please wait…' : approved ? 'Approved' : 'Approve'}</button>;
  const editItem = editing?.item;
  const editedItems = view.items.map((item,index) => index === editing?.index && editing.item ? editing.item : item);
  const emptyAfterEdit = editing && editedItems.every(item => !item.included && (!item.disclosure || item.treatment === 'SUPPORTING_ONLY'));
  return <section className="schedule-a-broker-panel card" id="schedule-a-broker-rows">
    <div className="schedule-a-broker-panel-head"><h3>Schedule A brokers</h3><span>{view.items.length} item{view.items.length === 1 ? '' : 's'}</span></div>
    <p role="status" className="broker-short-status">{approved ? 'Broker list approved. Normal automatic-update controls apply.' : problem ? `Paused: ${problem}` : 'Ready for approval.'}</p>
    {destinationProblem && onReviewDestination ? <button type="button" className="button secondary" disabled={disabled} onClick={onReviewDestination}>Review Schedule A</button> : null}
    <div className="schedule-a-broker-table-wrap" aria-label="Schedule A broker rows" role="region" tabIndex={0}>
      <table className="schedule-a-broker-table"><thead><tr><th>Broker / person</th><th>Address</th><th>Payment</th><th>Purpose / status</th><th>Action</th></tr></thead><tbody>
        {view.items.map((item, index) => <tr key={item.source}>
          <td data-label="Broker / person">{item.row.name}<small className="broker-draft-badge">{item.disclosure ? 'Separate service-provider disclosure' : 'Schedule A broker'}{item.row.source_page ? ` · Page ${item.row.source_page}` : ''}</small></td>
          <td data-label="Address">{address(item.row) || item.disclosure?.address}</td>
          <td data-label="Payment">{item.disclosure ? <strong>Original {formatMoney(item.disclosure.amount)}<br /></strong> : null}Commission: {formatMoney(item.row.commission_total)}<br />Fees: {formatMoney(item.row.fee_total)}</td>
          <td data-label="Purpose / status">{item.treatment === 'SUPPORTING_ONLY' ? 'Supporting information only' : !item.included && !item.disclosure ? 'Excluded' : purpose(item.row)}</td>
          <td><div className="broker-simple-actions"><button type="button" className="button secondary" disabled={disabled} onClick={event => beginEdit(event,index)}>Edit</button>{view.items.length === 1 ? approveButton : null}</div></td>
        </tr>)}
        {!view.items.length ? <tr><td colSpan={5}>No verified broker list. Current brokers remain unchanged. <button type="button" className="button secondary" disabled={disabled} onClick={event => beginEdit(event,-1)}>Edit</button></td></tr> : null}
      </tbody></table>
    </div>
    <div className="broker-approval-bar"><small id="broker-approval-scope">Approve confirms the complete displayed broker list, exclusions and payment classifications. Other filing reviews still apply.</small>{view.items.length !== 1 ? approveButton : null}</div>
    {message ? <p role="status">{message}</p> : null}
    {error ? <p role="alert" className="broker-review-error">{error}</p> : null}
    {draft?.dirty && !editing ? <button type="button" className="button secondary" disabled={disabled} onClick={() => {setDraft(null);setError('');setMessage('');}}>Cancel edits</button> : null}
    {editing ? <section className="broker-review-editor" role="region" aria-labelledby="broker-review-heading">
      <div className="broker-edit-toolbar"><h4 id="broker-review-heading" tabIndex={-1} ref={heading}>Edit broker</h4><button type="button" className="button secondary" disabled={disabled} onClick={closeEdit}>Cancel</button></div>
      {editItem?.disclosure ? <p>{editItem.disclosure.name} · Original {formatMoney(editItem.disclosure.amount)} · Page {editItem.disclosure.page}</p> : null}
      <form onSubmit={saveChanges}>
        {editItem?.disclosure ? <fieldset className="broker-payment-treatment" disabled={disabled}><legend>Does this payment belong on this Schedule A?</legend>
          {([['INCLUDED','Use on this Schedule A'],['SUPPORTING_ONLY','Supporting information only']] as const).map(([value,label]) => <label key={value}><input type="radio" name="broker-treatment" checked={editItem.treatment === value} onChange={() => setEditing(previous => previous?.item ? { ...previous, item: { ...previous.item, treatment: value, included: value === 'INCLUDED' } } : previous)} />{label}</label>)}
          <small>Only choose a commission or fee classification supported by the source or confirmed instructions.</small>
        </fieldset> : editItem ? <label><input type="checkbox" disabled={disabled} checked={!editItem.included} onChange={event => setEditing(previous => previous?.item ? { ...previous, item: { ...previous.item, included: !event.target.checked } } : previous)} />Exclude from replacement</label> : null}
        {editItem?.included ? <fieldset className="broker-edit-form" disabled={disabled}>
          <legend>{editItem.row.name || 'Extracted broker'}</legend>
          <label>Name<input aria-label="Broker name" required maxLength={35} value={editItem.row.name} onChange={event => editRow('name',event.target.value)} /></label>
          <label>Commission<input aria-label="Commission" required inputMode="decimal" value={editItem.row.commission_total ?? ''} onChange={event => editRow('commission_total',event.target.value)} /></label>
          <label>Fees<input aria-label="Fees" required inputMode="decimal" value={editItem.row.fee_total ?? ''} onChange={event => editRow('fee_total',event.target.value)} /></label>
          <label>Purpose<input aria-label="Purpose" readOnly value={purpose(editItem.row)} /></label>
          <details className="broker-address-details" open={!editItem.row.address_line_1 || !editItem.row.city || !editItem.row.state || !editItem.row.zip_code}><summary>Address and organization</summary>
            {editItem.disclosure?.address ? <p>Source address: {editItem.disclosure.address}</p> : null}
            {([['Street','address_line_1',35],['Address line 2','address_line_2',35],['City','city',30],['State','state',2],['ZIP','zip_code',10]] as const).map(([label,key,max]) => <label key={key}>{label}<input aria-label={label} required={key !== 'address_line_2'} maxLength={max} value={editItem.row[key] ?? ''} onChange={event => editRow(key,event.target.value)} /></label>)}
            <label>Organization code<select aria-label="Organization code" value={editItem.row.organization_code?.trim() || '3'} onChange={event => editRow('organization_code',event.target.value)}><option value="">Use default (3)</option>{[['0','Other'],['1','Banking or financial institution'],['2','Trust company'],['3','Insurance agent or broker'],['4','Non-insurance agent or broker'],['5','Third-party administrator'],['6','Investment company or mutual fund'],['7','Investment manager or adviser'],['8','Labor union'],['9','Foreign entity']].map(([value,label]) => <option key={value} value={value}>{value} — {label}</option>)}</select><small>Blank codes default to 3. Existing codes are preserved.</small></label>
          </details>
        </fieldset> : null}
        {emptyAfterEdit ? <label className="broker-empty-evidence">Evidence of no reportable Schedule A brokers<textarea maxLength={2000} disabled={disabled} value={editing.emptyEvidence} onChange={event => setEditing(previous => previous ? { ...previous, emptyEvidence: event.target.value } : previous)} /><small>Required before approving an empty replacement, which removes current brokers. A blank table alone is not evidence.</small></label> : null}
        <details><summary>View evidence</summary>{(view.roster?.reasons ?? []).map((text,index) => <p key={index}>{text}</p>)}{editItem?.disclosure ? <pre>{editItem.disclosure.source_text}</pre> : null}{(view.roster?.evidence ?? []).map((item,index) => <p key={index}>Page {item.page}: {item.source_text}</p>)}</details>
        <details><summary>Current brokers ({currentRows.length})</summary><ul>{currentRows.map((row,index) => <li key={index}>{['Name','AddressLine1','AddressLine2','City','State','ZipCode'].map(key => currentValue(row,key)).filter(Boolean).join(', ')}{` — Commission ${currentValue(row,'CommPdAmt') || 'Blank'}; Fees ${currentValue(row,'FeesPdAmt') || 'Blank'}`}</li>)}</ul></details>
        <div className="broker-simple-actions"><button className="button primary" type="submit" disabled={disabled}>Save changes</button><small>Saves your draft only. Approve continues the update flow.</small></div>
      </form>
    </section> : null}
  </section>;
}
