"""Carrier-neutral readers for explicitly labelled recipient columns/blocks.

Only source text is accepted here. No provider answer or current FTW value is
used to infer a missing address, recipient or payment category.
"""
from decimal import Decimal
import re

from app.models import ExtractionValidationResult, ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence

PAYMENT_COLUMNS = 'native_labelled_broker_payment_columns'
SECTION_TOTALS = 'native_printed_broker_section_totals'


def mark_payment_columns(row):
    """The caller decoded separately labelled columns, not nearby numbers."""
    row.validation_results.append(ExtractionValidationResult(validator=PAYMENT_COLUMNS, status='PASS',
        reason='Recipient payment categories were read from explicit labelled source columns.'))
    return row


def mark_section_totals(rows, commission, fees, evidence):
    if (not rows or _amount(commission) is None or _amount(fees) is None
            or any(_amount(r.commission_total) is None or _amount(r.fee_total) is None for r in rows)):
        return
    if (sum(Decimal(_amount(r.commission_total)) for r in rows) != Decimal(_amount(commission))
            or sum(Decimal(_amount(r.fee_total)) for r in rows) != Decimal(_amount(fees))):
        for row in rows:
            row.validation_results.append(ExtractionValidationResult(validator=SECTION_TOTALS, status='ERROR',
                reason='Printed source category totals do not reconcile to all recipient rows.'))
            row.decision = 'REVIEW_REQUIRED'
        return
    for row in rows:
        mark_payment_columns(row)
        row.validation_results.append(ExtractionValidationResult(validator=SECTION_TOTALS, status='PASS',
            normalized_value=_amount(commission)+':'+_amount(fees),
            reason='All classified source rows reconcile to both explicitly printed section totals.'))
        row.evidence.extend(evidence)


def recipient_key(row):
    # Used only to corroborate repair against a complete provider recipient set.
    # Moving a printed unit/city between address fields does not change the
    # full physical address; source columns supply the correct split.
    normalize = lambda value: re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
    return (normalize(row.name), normalize(' '.join(str(getattr(row, key) or '') for key in
        ('address_line_1', 'address_line_2', 'city', 'state', 'zip_code'))))


def recipient_sets_corroborate(provider_rows, source_rows):
    prior, source = {recipient_key(r) for r in provider_rows}, {recipient_key(r) for r in source_rows}
    if not prior or not source:
        return False
    def supported(key, candidates):
        name, address = key
        if key in candidates:
            return True
        # A source recipient with no printed address remains a separate blank
        # row. It may be corroborated by a unique provider identity of the same
        # exact name, without copying the provider's address or merging payments.
        same_name = {a for n, a in candidates if n == name}
        return not address and len(same_name) == 1
    return all(supported(key, prior) for key in source) and all(
        key in source or (key[0], '') in source and len({a for n,a in prior if n == key[0]}) == 1 for key in prior)


def provider_supports_labelled_payments(result, source_rows):
    """Corroborate corrected categories with provider recipients AND its totals.

    This is narrower than accepting a local parser as its own verifier. Both
    independent totals and the entire recipient set must already agree. An
    unexplained provider/source payment disagreement remains a review hold.
    """
    from app.models import ScheduleABrokerRow
    stage = next((s for s in (result.raw or {}).get('extraction_stages', [])
                  if s.get('stage') == 'structured_provider_normalized'), {})
    try:
        prior = [ScheduleABrokerRow.model_validate(r) for r in stage.get('broker_rows', [])]
    except (ValueError, TypeError):
        return False
    if (not source_rows or not prior or any(not r.source_page or not any(e.page == r.source_page and e.source_text
            for e in r.evidence) or not any(v.validator == PAYMENT_COLUMNS and v.status == 'PASS'
            for v in r.validation_results) for r in source_rows)
            or not recipient_sets_corroborate(prior, source_rows)):
        return False
    for prefix, key in (('3b.', 'commission_total'), ('3c.', 'fee_total')):
        totals = {_amount(f.get('value')) for f in stage.get('fields', [])
                  if f.get('field_name', '').startswith(prefix) and f.get('decision') != 'REVIEW_REQUIRED'}
        values = [_amount(getattr(r, key)) for r in source_rows]
        if None in values or len(totals) != 1 or None in totals:
            return False
        if sum(map(Decimal, values)) != Decimal(next(iter(totals))):
            return False
    return True


def _amount(value):
    from app.services.schedule_a_broker_roster import _money
    return _money(value)


def _row(name, address, commission, fees, page, source, *, provider):
    from app.services.extractor import _compensation_address, is_probable_person_or_entity_name
    if not is_probable_person_or_entity_name(name) or not name.strip():
        return None
    parts = _compensation_address(address)
    return mark_payment_columns(ScheduleABrokerRow(name=name.strip(), address_line_1=parts[0], address_line_2=parts[1],
        city=parts[2], state=parts[3], zip_code=parts[4],
        commission_total=commission, fee_total=fees, organization_code='3', organization_code_defaulted=True,
        commission_rows=[ScheduleABrokerMoneyRow(amount=commission, purpose='COMMISSIONS')],
        fee_rows=[ScheduleABrokerMoneyRow(amount=fees, purpose='FEES')], source_page=page, confidence=.99,
        commission_source_text=source, fee_source_text=source,
        evidence=[SourceEvidence(provider=provider, page=page, source_text=source)]))


def labelled_payee_rows(pages):
    """Keep explicit Address / City / State / ZIP cells separate, including units."""
    rows = []
    header = re.compile(r'Payee\s+Name\s{2,}Contract\s+or\s+ID\s*#?\s{2,}Address\s+Line\s+1'
                        r'\s{2,}City\s{2,}State\s+Zip\s+Code\s{2,}Amount', re.I)
    for page, text in pages:
        match = header.search(text)
        if not match:
            continue
        body = text[match.end():]
        total = re.search(r'\bTotal:\s*\$\s*([\d,]+\.\d{2})', body, re.I)
        if total is None:
            return []
        packet = []
        for line in body[:total.start()].splitlines():
            if not line.strip():
                continue
            cells = re.split(r'\s{2,}', line.strip())
            if (len(cells) != 7 or not re.fullmatch(r'[A-Z0-9/-]+', cells[1], re.I)
                    or not re.fullmatch(r'[A-Z]{2}', cells[4], re.I)
                    or not re.fullmatch(r'\d{5}(?:-\d{4})?', cells[5])
                    or not re.fullmatch(r'\$[\d,]+\.\d{2}', cells[6])):
                return []
            name, contract, street, city, state, zipcode, paid = cells
            row = _row(name, [], _amount(paid), '0', page, match[0]+'\n'+line,
                       provider='Labelled recipient address columns')
            if row is None:
                return []
            row.address_line_1, row.city, row.state, row.zip_code = street, city, state.upper(), zipcode
            row.commission_rows[0].coverage = contract
            if re.search(r'commissions\s+or\s+fees', text[:match.start()], re.I):
                row.decision = 'REVIEW_REQUIRED'
                row.validation_results = [v for v in row.validation_results if v.validator != PAYMENT_COLUMNS]
                row.validation_results.append(ExtractionValidationResult(validator='payment_classification_ambiguity', status='ERROR',
                    reason='The source labels one combined commissions-or-fees amount; its payment category needs confirmation.'))
            packet.append(row)
        if not packet or sum(Decimal(r.commission_total) for r in packet) != Decimal(_amount(total[1])):
            return []
        for row in packet:
            row.evidence.append(SourceEvidence(provider='Printed recipient section total', page=page, source_text=total[0]))
        rows.extend(packet)
    return rows


def labelled_recipient_blocks(pages):
    """Read numbered recipients with separately labelled commission and fee amounts."""
    joined = '\n'.join(text for _, text in pages)
    commission_total = re.findall(r'Total\s+Amount\s+of\s+Commissions\s+Paid:\s*\$([\d,]+\.\d{2})', joined, re.I)
    fee_total = re.findall(r'Total\s+Amount\s+of\s+Fees\s+Paid:\s*\$([\d,]+\.\d{2})', joined, re.I)
    if len(commission_total) != 1 or len(fee_total) != 1:
        return []
    rows = []
    heading = re.compile(r'\d+\)\s*Name\s+and\s+address\s+of\s+the\s+agent,\s*broker,\s*or\s+other\s+person'
                         r'\s+to\s+whom\s+commissions\s+or\s+fees\s+were\s+paid\s*:', re.I)
    for page, text in pages:
        starts = list(heading.finditer(text))
        for index, start in enumerate(starts):
            block = text[start.end():starts[index+1].start() if index+1 < len(starts) else len(text)]
            comm = re.search(r'Amount\s+of\s+sales\s+and\s+base\s+commissions\s+paid\s+to\s+([^\n$]+?)\s*:?\s*\$([\d,]+\.\d{2})', block, re.I)
            fee = re.search(r'Fees\s+and\s+other\s+compensation\s+paid\s+to\s+([^\n$]+?)\s*:?\s*\$([\d,]+\.\d{2})', block, re.I)
            if not comm or not fee:
                return []
            lines = [line.strip() for line in block[:comm.start()].splitlines() if line.strip()]
            if not lines:
                return []
            if lines[0].casefold() in {'none', 'n/a'}:
                if any(Decimal(_amount(m[2])) for m in (comm, fee)):
                    return []
                continue
            name = lines[0]
            norm = lambda v: re.sub(r'[^a-z0-9]', '', v.casefold())
            if any(norm(m[1]) != norm(name) for m in (comm, fee)):
                return []
            row = _row(name, lines[1:], _amount(comm[2]), _amount(fee[2]), page,
                       start[0]+'\n'+block[:fee.end()], provider='Labelled recipient payment block')
            if row is None:
                return []
            rows.append(row)
    if (sum((Decimal(r.commission_total) for r in rows), Decimal(0)) != Decimal(_amount(commission_total[0]))
            or sum((Decimal(r.fee_total) for r in rows), Decimal(0)) != Decimal(_amount(fee_total[0]))):
        return []
    mark_section_totals(rows, commission_total[0], fee_total[0], [SourceEvidence(
        provider='Printed commission and fee section totals', page=pages[0][0],
        source_text=f'Total Amount of Commissions Paid: ${commission_total[0]}\nTotal Amount of Fees Paid: ${fee_total[0]}')])
    return rows


def commission_payee_bullets(pages):
    """An explicitly commission-labelled payee and postal block (not a Schedule C disclosure)."""
    rows = []
    for page, text in pages:
        section = re.search(r'Commissions\s+Paid\s*:', text, re.I)
        if not section or re.search(r'Schedule\s+C\b', text, re.I):
            continue
        body = text[section.end():]
        payees = list(re.finditer(r'Payee:\s*([^\n]+?)\s*:\s*\$([\d,]+\.\d{2})', body, re.I))
        for index, payee in enumerate(payees):
            tail = body[payee.end():payees[index+1].start() if index+1 < len(payees) else len(body)]
            address = re.search(r'([^\n]+?),\s*([A-Z .\'-]+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)', tail, re.I)
            if not address:
                return []
            row = _row(payee[1], [address[1].strip(), f'{address[2]}, {address[3]} {address[4]}'],
                       _amount(payee[2]), '0', page, section[0]+'\n'+payee[0]+'\n'+address[0],
                       provider='Labelled commission payee block')
            if row:
                rows.append(row)
    return rows


def labelled_payee_compensation(pages):
    """Read repeated Payee Code/Name/Address blocks and their labelled totals.

    A Payee Code identifies the producer; it is not an organization-code cell.
    Validate each block's commission + fees against its compensation total.
    """
    rows = []
    for page, text in pages:
        starts = list(re.finditer(r'Payee\s+Code\s*:\s*(\S+)\s+Total\s+Commission\s*:\s*\$([\d,]+\.\d{2})', text, re.I))
        for index, start in enumerate(starts):
            body = text[start.end():starts[index+1].start() if index+1 < len(starts) else len(text)]
            name = re.search(r'Payee\s+Name\s*:\s*([^\n]+?)\s+Total\s+Administrative\s+and\s+Other\s+Fees\s*:\s*\$([\d,]+\.\d{2})', body, re.I)
            street = re.search(r'Payee\s+Address\s*:\s*([^\n]+?)\s+Total\s+Amount\s+of\s+Compensation\s*:\s*\$([\d,]+\.\d{2})', body, re.I)
            if not name or not street:
                return []
            if Decimal(_amount(start[2])) + Decimal(_amount(name[2])) != Decimal(_amount(street[2])):
                return []
            locality = next((line.strip(' |_') for line in body[street.end():].splitlines() if line.strip(' |_')), '')
            address = [street[1].strip(' |_'), locality]
            row = _row(name[1].strip(' |_'), address, _amount(start[2]), _amount(name[2]), page,
                start[0]+'\n'+body[:street.end()]+'\n'+locality, provider='Labelled payee commission and fee blocks')
            if not row or not all((row.address_line_1, row.city, row.state, row.zip_code)):
                return []
            rows.append(row)
    return rows


def labelled_payee_totals_table(pages):
    """Multi-line Payee/Addr1/CSZ rows with separate B, C and E columns."""
    rows = []
    for page, text in pages:
        lines = text.splitlines()
        header_index = next((i for i, line in enumerate(lines) if '(A) Name & Address' in line
            and '(B) Amount' in line and 'Fees Paid' in line and '(E) Org' in line), None)
        if header_index is None:
            continue
        header = lines[header_index]
        name_end, comm_col, fee_col, org_col = (header.index(label) for label in
            ('Base Plan', '(B) Amount', 'Fees Paid', '(E) Org'))
        starts = [i for i in range(header_index+1, len(lines)) if re.match(r'\s*Payee:', lines[i])]
        for index, start in enumerate(starts):
            body = lines[start:starts[index+1] if index+1 < len(starts) else len(lines)]
            total_index = next((i for i, line in enumerate(body) if re.match(r'\s*Totals\b', line)), None)
            if total_index is None:
                return []
            total = body[total_index]
            commission = _amount(total[comm_col:fee_col].strip())
            fees = _amount(total[fee_col:org_col].strip())
            cells = [line[:name_end].strip() for line in body[:total_index]]
            addr_index = next((i for i, cell in enumerate(cells) if cell.startswith('Addr1:')), None)
            city_index = next((i for i, cell in enumerate(cells) if cell.startswith('CSZ:')), None)
            if None in (commission, fees, addr_index, city_index):
                return []
            name = ' '.join(cell.removeprefix('Payee:').strip() for cell in cells[:addr_index] if cell)
            address = [' '.join(cell.removeprefix('Addr1:').strip() for cell in cells[addr_index:city_index] if cell),
                       cells[city_index].removeprefix('CSZ:').strip()]
            codes = {m[1] for line in body[:total_index] if (m := re.match(r'\s*([0-9])\s*-\s*Ins\b', line[org_col:], re.I))}
            if len(codes) != 1:
                return []
            row = _row(name, address, commission, fees, page, header+'\n'+'\n'.join(body[:total_index+1]),
                       provider='Labelled Payee/Addr1/CSZ and category total columns')
            if row is None:
                return []
            row.organization_code, row.organization_code_defaulted = next(iter(codes)), False
            rows.append(row)
    return rows


def labelled_id_payment_table(pages):
    """Recipient ID followed by commission, contingent, override, fee and org cells."""
    rows = []
    for page, text in pages:
        if not (re.search(r'ID,\s*NAME\s*&\s*ADDRESS', text, re.I)
                and re.search(r'COMM\.\s+CONT\.\s*COMP\*?\s+GA\s+OVR\.', text, re.I)):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            cells = re.split(r'\s{2,}', line.strip())
            if len(cells) != 6 or not re.fullmatch(r'\d{5,}', cells[0]):
                continue
            if not all(re.fullmatch(r'\$[\d,]+\.\d{2}', value) for value in cells[1:5]) or not re.fullmatch(r'[0-9]', cells[5]):
                return []
            tail = [s.strip() for s in lines[i+1:i+7] if s.strip()]
            locality = next((j for j, s in enumerate(tail) if re.fullmatch(r'.+?,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?', s)), None)
            if locality is None or locality < 2:
                return []
            row = _row(tail[0], tail[1:locality+1], format(sum(Decimal(_amount(v)) for v in cells[1:4]), 'f'),
                _amount(cells[4]), page, line+'\n'+'\n'.join(tail[:locality+1]), provider='Labelled ID and classified compensation columns')
            if row is None:
                return []
            row.organization_code, row.organization_code_defaulted = cells[5], False
            rows.append(row)
    return rows


def labelled_contract_payment_table(pages):
    """Contract/name/address, B commission, C fees, D purpose, E code columns."""
    rows = []
    for page, text in pages:
        lines = text.splitlines()
        header_index = next((i for i, line in enumerate(lines) if '(a) Name and address' in line
            and '(b)' in line and '(c)' in line and '(d)' in line and '(e)' in line), None)
        if header_index is None:
            continue
        header = lines[header_index]
        name_col, comm_col, fee_col, purpose_col, code_col = (header.index(f'({letter})') for letter in 'abcde')
        starts = []
        for i in range(header_index+1, len(lines)):
            line = lines[i]
            if (re.fullmatch(r'[A-Z0-9-]{4,}', line[:name_col].strip())
                    and _amount(line[comm_col:fee_col].strip()) is not None
                    and line[name_col:comm_col].strip()):
                starts.append(i)
        for index, start in enumerate(starts):
            body = lines[start:starts[index+1] if index+1 < len(starts) else len(lines)]
            first = body[0]
            cells = [line[name_col:comm_col].strip() for line in body if line[name_col:comm_col].strip()]
            locality = next((i for i, cell in enumerate(cells) if re.fullmatch(r'.+?,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?', cell)), None)
            street = next((i for i, cell in enumerate(cells[:locality]) if re.match(r'\d+\s|P\.?\s*O\.?\s+BOX\b', cell, re.I)), None) if locality is not None else None
            if locality is None or street is None or street == 0:
                return []
            commission = _amount(first[comm_col:fee_col].strip() or '0')
            fees = _amount(first[fee_col:purpose_col].strip() or '0')
            code = first[code_col:].strip()
            if commission is None or fees is None or not re.fullmatch(r'[0-9]', code):
                return []
            row = _row(' '.join(cells[:street]), cells[street:locality+1], commission, fees, page,
                header+'\n'+'\n'.join(body[:locality+3]), provider='Labelled contract and recipient payment columns')
            if row is None:
                return []
            row.organization_code, row.organization_code_defaulted = code, False
            contract = first[:name_col].strip()
            row.commission_rows[0].coverage = contract
            prior = next((r for r in rows if recipient_key(r) == recipient_key(row)
                and r.organization_code == code and r.commission_rows[0].coverage == contract), None)
            if prior:
                prior.commission_total = format(Decimal(prior.commission_total)+Decimal(commission), 'f')
                prior.fee_total = format(Decimal(prior.fee_total)+Decimal(fees), 'f')
                prior.commission_rows.extend(row.commission_rows)
                prior.fee_rows.extend(row.fee_rows)
                prior.evidence.extend(row.evidence)
            else:
                rows.append(row)
    return rows


def approved_commission_recipient_blocks(pages):
    """Read the approved combined-column label, retaining wrapped recipient names."""
    from app.services.schedule_a_vsp_rules import approved_vsp_commission_label
    rows = []
    for page, text in pages:
        if not approved_vsp_commission_label(text):
            continue
        section = re.search(r'Agent\s+or\s+Broker', text, re.I)
        if not section:
            continue
        body = text[section.end():]
        starts = list(re.finditer(r'(?m)^\s*([A-Za-z][^\n$]+?)\s{2,}\$\s*([\d,]+\.\d{2})\s*$', body))
        for index, start in enumerate(starts):
            tail = body[start.end():starts[index+1].start() if index+1 < len(starts) else len(body)]
            lines = [s.strip() for s in tail.splitlines() if s.strip()]
            locality = next((i for i, line in enumerate(lines) if re.fullmatch(r'.+?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?', line)), None)
            if locality is None:
                return []
            street = next((i for i, line in enumerate(lines[:locality]) if re.match(r'\d+\s|P\.?\s*O\.?\s+Box\b', line, re.I)), None)
            if street is None:
                return []
            name = ' '.join([start[1].strip(), *lines[:street]])
            row = _row(name, lines[street:locality+1], _amount(start[2]), '0', page,
                       'Approved commissions/fees paid for policy or contract year\n'+start[0]+'\n'+'\n'.join(lines[:locality+1]),
                       provider='Approved combined-column recipient block')
            if row is None:
                return []
            rows.append(row)
    return rows


def labelled_single_recipient(pages):
    """One explicitly labelled recipient beside commission, fee and code cells."""
    rows = []
    for page, text in pages:
        heading = re.search(r'Name\s+and\s+Address\s+of\s+Recipient\s+of\s+Fees\s+or\s+Commissions', text, re.I)
        if not heading or re.search(r'Schedule\s+C\b', text, re.I):
            continue
        lines = [s.strip() for s in text[heading.end():].splitlines() if s.strip()]
        if len(lines) < 3:
            return []
        name = re.fullmatch(r'(.+?)\s+Organization\s+Code\s+([0-9])', lines[0], re.I)
        street = re.fullmatch(r'(.+?)\s+Amount\s+of\s+Commissions\s+Paid\s*:\s*\$([\d,]+\.\d{2})', lines[1], re.I)
        locality = re.fullmatch(r'(.+?)\s+Fees\s+Paid\s*/\s*Amount\s*:?\s*\$([\d,]+\.\d{2})', lines[2], re.I)
        if not all((name, street, locality)):
            return []
        row = _row(name[1], [street[1], locality[1]], _amount(street[2]), _amount(locality[2]), page,
            heading[0]+'\n'+'\n'.join(lines[:3]), provider='Labelled recipient and commission/fee cells')
        if not row:
            return []
        row.organization_code, row.organization_code_defaulted = name[2], False
        comm_total = re.findall(r'Total\s+Amount\s+of\s+Commissions\s+Paid\s*:\s*\$([\d,]+\.\d{2})', text, re.I)
        fee_total = re.findall(r'Total\s+Fees\s+Paid\s*/\s*Amount\s*:\s*\$([\d,]+\.\d{2})', text, re.I)
        if len(comm_total) != 1 or len(fee_total) != 1:
            return []
        mark_section_totals([row], comm_total[0], fee_total[0], [SourceEvidence(provider='Printed commission and fee section totals',
            page=page, source_text=f'Total Amount of Commissions Paid: ${comm_total[0]}\nTotal Fees Paid/Amount: ${fee_total[0]}')])
        if not any(v.validator == SECTION_TOTALS and v.status == 'PASS' for v in row.validation_results):
            return []
        rows.append(row)
    return rows


def bordered_recipient_tables(content):
    """Decode ruled recipient tables using their physical header/cell roles.

    The agent-number column is kept as evidence, never used as an org code.
    Wrapped names/addresses are read within their cells, and the beneficiary
    summary rows must reconcile to every recipient row on each page.
    """
    from io import BytesIO
    import pdfplumber
    from app.services.schedule_a_pdf_source import source_page
    normalize = lambda v: re.sub(r'[^a-z0-9]', '', str(v or '').casefold())
    result, grand_totals = [], []
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            for page_number, physical in enumerate(pdf.pages, 1):
                for table in source_page(physical).extract_tables():
                    headers = [(i, row) for i,row in enumerate(table) if any('nameandaddressoftheagents' in normalize(cell) for cell in row)]
                    if not headers:
                        continue
                    if len(headers) != 1:
                        return []
                    index, header = headers[0]
                    def column(predicate):
                        matches = [i for i,cell in enumerate(header) if predicate(normalize(cell))]
                        return matches[0] if len(matches) == 1 else None
                    name_col = column(lambda s: 'nameandaddressoftheagents' in s)
                    comm_col = column(lambda s: 'totalcommissionspaid' in s)
                    fee_col = column(lambda s: 'totalamountoffeespaid' in s)
                    agent_col = column(lambda s: s == 'agentnumber')
                    if None in (name_col, comm_col, fee_col, agent_col):
                        return []
                    rows, printed = [], []
                    for cells in table[index+1:]:
                        if len(cells) != len(header):
                            return []
                        commission, fees = _amount(cells[comm_col]), _amount(cells[fee_col])
                        if commission is None or fees is None:
                            # Section labels and spacer rows have no payments.
                            if cells[name_col] or cells[agent_col]:
                                return []
                            continue
                        if not cells[name_col] and not cells[agent_col]:
                            totals = (Decimal(commission), Decimal(fees))
                            if any(normalize(cell) == 'grandtotal' for cell in cells[:name_col]):
                                grand_totals.append(totals)
                            else:
                                printed.append(totals)
                            continue
                        block = [line.strip() for line in (cells[name_col] or '').splitlines() if line.strip()]
                        street = next((i for i,line in enumerate(block) if re.match(r'\d+\s|P\.?\s*O\.?\s+BOX\b', line, re.I)), None)
                        if street is None or street == 0:
                            return []
                        source = ' | '.join(str(cell or '').replace('\n', '; ') for cell in header)+'\n'+' | '.join(str(cell or '') for cell in cells)
                        row = _row(' '.join(block[:street]), block[street:], commission, fees, page_number,
                                   source, provider='Physical ruled recipient table cells')
                        if row is None or not all((row.city, row.state, row.zip_code)):
                            return []
                        rows.append(row)
                    if (not rows or not printed or sum(Decimal(r.commission_total) for r in rows) != sum(c for c,f in printed)
                            or sum(Decimal(r.fee_total) for r in rows) != sum(f for c,f in printed)):
                        return []
                    for row in rows:
                        existing = next((old for old in result if recipient_key(old) == recipient_key(row)), None)
                        if existing:
                            existing.commission_total = format(Decimal(existing.commission_total)+Decimal(row.commission_total), 'f')
                            existing.fee_total = format(Decimal(existing.fee_total)+Decimal(row.fee_total), 'f')
                            existing.commission_rows.extend(row.commission_rows); existing.fee_rows.extend(row.fee_rows)
                            existing.evidence.extend(row.evidence)
                        else:
                            result.append(row)
    except Exception:
        return []
    if grand_totals and (len(set(grand_totals)) != 1 or grand_totals[0] != (
            sum(Decimal(r.commission_total) for r in result), sum(Decimal(r.fee_total) for r in result))):
        return []
    return result


def labelled_benefit_compensation(pages):
    """Read repeated benefit statements with a reconciled commission/fee row."""
    rows = []
    for page,text in pages:
        marker = re.search(r'Persons\s+Receiving\s+Commissions\s*:', text, re.I)
        total = re.search(r'Total\s+Amount\s+of\s+Commissions\s+and\s+Fees\s+\$([\d,]+\.\d{2})', text, re.I)
        contract = re.search(r'Contract\s+or\s+Identification\s+Number\s*:\s*([A-Z0-9-]+)', text, re.I)
        if not marker or not total or not contract or re.search(r'Schedule\s+C\b', text, re.I):
            continue
        header = text[max(0,marker.start()-600):marker.end()+300]
        comm_columns = [m.start() for line in header.splitlines() if (m := re.search(r'\bCommission\b', line))]
        fee_columns = [m.start() for line in header.splitlines() if (m := re.search(r'Total\s+Fees', line, re.I))]
        if len(comm_columns) != 1 or len(fee_columns) != 1 or comm_columns[0] >= fee_columns[0]:
            return []
        match = re.search(r'(?m)^\s*([A-Z][A-Z &.,\'-]+?)\s{2,}\$([\d,]+\.\d{2})(?:\s+\$([\d,]+\.\d{2}))?\s*$', text[marker.end():])
        if not match:
            return []
        row_line_start = text[marker.end():].rfind('\n', 0, match.start(2)) + 1
        if not comm_columns[0] <= match.start(2) - row_line_start < fee_columns[0]:
            return []
        tail = text[marker.end()+match.end():]
        address = [s.strip() for s in tail.splitlines() if s.strip()][:2]
        row = _row(match[1], address, _amount(match[2]), _amount(match[3] or '0'), page,
            header+'\n'+match[0]+'\n'+'\n'.join(address), provider='Labelled benefit recipient compensation cells')
        if row is None or not row.city or Decimal(row.commission_total)+Decimal(row.fee_total) != Decimal(_amount(total[1])):
            return []
        row.commission_rows[0].coverage = contract[1]
        prior = next((r for r in rows if recipient_key(r) == recipient_key(row) and r.commission_rows[0].coverage == contract[1]), None)
        if prior:
            prior.commission_total = format(Decimal(prior.commission_total)+Decimal(row.commission_total), 'f')
            prior.fee_total = format(Decimal(prior.fee_total)+Decimal(row.fee_total), 'f')
            prior.evidence.extend(row.evidence); prior.commission_rows.extend(row.commission_rows); prior.fee_rows.extend(row.fee_rows)
        else:
            rows.append(row)
    return rows


def monthly_commission_recipient(pages):
    """A named payee and a month-by-month, totalled Commission column."""
    rows = []
    for page,text in pages:
        lines = text.splitlines()
        heading = next((i for i,line in enumerate(lines) if re.search(r'Commissions\s+paid\s+to\s*:', line, re.I)), None)
        table = next((i for i,line in enumerate(lines) if re.search(r'Invoice\s+Month\s+Premium\s+Paid\s+Commission\s*$', line, re.I)), None)
        if heading is None or table is None or heading >= table:
            continue
        label = re.search(r'Commissions\s+paid\s+to\s*:', lines[heading], re.I)
        gap = re.search(r'\s{2,}Commissions\s+paid\s+to', lines[heading], re.I)
        column = (gap.start()+label.start())//2 if gap else label.start()
        block = [line[column:].strip() for line in lines[heading+1:table] if line[column:].strip()]
        street = next((i for i,line in enumerate(block) if re.match(r'\d+\s|P\.?\s*O\.?\s+BOX\b', line, re.I)), None)
        if not street:
            return []
        name = ' '.join(block[:street])
        # A printed DBA is part of the recipient identity, not a second payee.
        tail = lines[table+1:]
        totals = next((re.fullmatch(r'\s*Totals:\s*\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})\s*', line) for line in tail if re.match(r'\s*Totals:', line)), None)
        if totals is None:
            return []
        months = [re.fullmatch(r'\s*[A-Za-z]{3}-\d{4}\s+\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})\s*', line) for line in tail if re.match(r'\s*[A-Za-z]{3}-\d{4}', line)]
        if (not months or any(m is None for m in months)
                or any(sum(Decimal(_amount(m[i])) for m in months) != Decimal(_amount(totals[i])) for i in (1,2))):
            return []
        source = lines[heading]+'\n'+'\n'.join(block)+'\n'+'\n'.join(lines[table:])
        row = _row(name, block[street:], _amount(totals[2]), '0', page, source,
                   provider='Named payee and reconciled monthly commission column')
        if row is None or not row.city:
            return []
        rows.append(row)
    return rows


def header_named_fee_rows(pages):
    """A single account row under a named 'Agent Fees Paid to' column."""
    rows = []
    for page,text in pages:
        lines = text.splitlines()
        heading = next((i for i,line in enumerate(lines) if re.search(r'Agent\s+Fees\s+Paid\s+to', line, re.I)), None)
        if (heading is None or not re.search(r'\bAcct\b', text, re.I)
                or not re.search(r'(?m)^\s*Code\s+Group\s*#', text, re.I) or 'Paid Premium' not in text):
            continue
        table_rows = [(i,line) for i,line in enumerate(lines[heading+1:],heading+1)
            if len(re.findall(r'\$[\d,]+\.\d{2}', line)) == 2 and re.match(r'\s*[A-Z0-9]+\s+\d+\s+\d+\s+', line)]
        if len(table_rows) != 1:
            return []
        index,line = table_rows[0]
        name = ' '.join(re.split(r'\s{2,}', raw.strip())[-1] for raw in lines[heading+1:index] if raw.strip())
        paid = re.findall(r'\$[\d,]+\.\d{2}', line)[1]
        row = _row(name, [], '0', _amount(paid), page, '\n'.join(lines[heading:index+1]),
                   provider='Named agent fee column; account code is not an organization code')
        if row is None:
            return []
        rows.append(row)
    return rows
