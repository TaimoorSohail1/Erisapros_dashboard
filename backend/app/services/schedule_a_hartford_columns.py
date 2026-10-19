"""Hartford's interleaved producer columns, without sample client constants."""
from decimal import Decimal
import re

from app.models import (
    ExtractionValidationResult, ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence,
)


def recover_hartford_compensation_pages(page_texts, raw):
    """Use reconciled X-Ray cells when local OCR lost producer tables.

    Only actual provider output chunks are eligible, never normalized fields
    or our own stage trace. Other layouts and insurance pages are unchanged.
    Each accepted row must reconcile every column to its printed total.
    """
    if not isinstance(raw, dict) or not any(
        'ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION' in text.upper()
        for _, text in page_texts
    ):
        return page_texts
    chunks = {}
    for output in raw.get('outputs') or []:
        if not isinstance(output, dict):
            continue
        candidates = list(output.get('chunks') or [])
        for page in output.get('documentPages') or []:
            if isinstance(page, dict):
                candidates.extend(page.get('chunks') or [])
        for chunk in candidates:
            if not isinstance(chunk, dict) or 'table' not in (chunk.get('contentType') or []):
                continue
            pages = chunk.get('pageNumbers') or []
            text = chunk.get('text') or ''
            if len(pages) != 1 or 'Producer and Address' not in text:
                continue
            chunks.setdefault(int(pages[0]), [])
            if text not in chunks[int(pages[0])]:
                chunks[int(pages[0])].append(text)
    recovered = []
    marker = next(text for _, text in page_texts
                  if 'ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION' in text.upper())
    for page, texts in chunks.items():
        source = '\n'.join(texts)
        provider_rows = extract_hartford_columnar_brokers([(0, marker), (page, source)])
        provider_rows = [row for row in provider_rows if row.source_page == page]
        current = extract_hartford_columnar_brokers(page_texts)
        current = [row for row in current if row.source_page == page]
        key = lambda row: tuple(re.sub(r'\W', '', str(getattr(row, attr) or '')).casefold()
                                for attr in ('name', 'address_line_1', 'zip_code'))
        by_key = {key(row): row for row in provider_rows}
        # Never overwrite an independently read, conflicting source answer.
        conflict = any(key(row) not in by_key or any(
            _decimal(getattr(row, attr)) != _decimal(getattr(by_key[key(row)], attr))
            for attr in ('commission_total', 'fee_total')) for row in current)
        if not provider_rows or conflict or any(row.decision != 'AUTOMATIC' for row in provider_rows):
            continue
        if len(by_key) <= len({key(row) for row in current}):
            continue
        original = next((text for number, text in page_texts if number == page), '')
        prefix = re.split(r'Producer\s+and\s+Address', original, maxsplit=1, flags=re.I)[0]
        footer = re.search(r'\(1\)\s*Bonus Paid represents.*', original, re.I | re.S)
        recovered.append((page, prefix + '\n' + source + ('\n' + footer.group(0) if footer else '')))
    replaced = {page for page, _ in recovered}
    return sorted([(page, text) for page, text in page_texts if page not in replaced] + recovered)

_AMOUNT = r"[-+]?\s*(?:\$|S)?\s*[-+]?\s*\d[\d,]*\.\d{2}(?:\s*\$(?=\s*\$|\s*$))?"
_POLICY_ROW = re.compile(
    r"^(?P<identity>.*?)\b(?P<policy>[A-Z0-9]+\s*-\s*[A-Z0-9-]+)\s+"
    + r"\s+".join(rf"(?P<{key}>{_AMOUNT})" for key in ("commission", "fee", "bonus", "additional"))
    + r"\s*$", re.IGNORECASE,
)
_TOTAL = re.compile(
    r"\bTotal\s+" + r"\s+".join(rf"(?P<{key}>{_AMOUNT})" for key in ("commission", "fee", "bonus", "additional")),
    re.IGNORECASE,
)


def _decimal(value):
    return Decimal(re.sub(r"[$S,\s]", "", value))


def _flat_producer_columns(page, text, components=None):
    """Read column-major OCR only when every component and total is present."""
    rows = []
    money = r"[-+]?\s*[$S]\s*[-+]?\s*\d[\d,]*\.\d{2}"
    headings = (
        ("commission", r"Commissions\s+(?:Fees\s+)?Paid(?:\s+Paid)?"),
        ("fee", r"Fees\s+Paid"),
        ("bonus", r"\(\s*1\s*\)\s*(?:Paid\s+Bonus|Bonus\s+Paid)"),
        ("additional", r"(?:\(?\s*2\s*\)?\s*\)?\s*Additional\s*\(?\s*Compensation\s+Paid)"),
    )
    for block in re.split(r"Producer\s+and\s+Address\s+", text, flags=re.I)[1:]:
        identity = re.match(
            r"(?P<name>.+?)\s+(?P<address>(?:\d{1,6}\s+.+?\b(?:AVE|AVENUE|RD|ROAD|ST|STREET|BLVD|BOULEVARD|DR|DRIVE|CIRCLE|CIR|WAY)\b(?:\s+(?:STE|SUITE)\s+\w+)?|P\.?\s*O\.?\s+BOX\s+\d+))\s+"
            r"(?P<city>[A-Z][A-Z .'-]+?)\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s+"
            r"Org\s+Code\s+(?P<code>\d{1,2})\s+Policy\s+Number\s+"
            r"(?P<policies>.+?)\s+Total\s+", block, re.I,
        )
        if identity is None:
            continue
        policies = re.findall(r"[A-Z0-9]+\s*-\s*[A-Z0-9-]+", identity.group("policies"), re.I)
        if not policies:
            continue
        tail = block[identity.end():]
        columns = {}
        literals = {}
        position = 0
        for key, heading in headings:
            if key in columns:
                continue
            combined = key == 'commission' and re.match(r"\s*Commissions\s+Fees\s+Paid\s+Paid", tail, re.I)
            if combined:
                values = re.match(r"\s*(?P<values>" + money + r"(?:\s+" + money + r")*)", tail[combined.end():], re.I)
                amounts = [_decimal(value) for value in re.findall(money, values.group('values'), re.I)] if values else []
                width = len(policies) + 1
                if len(amounts) != 2 * width:
                    break
                left, right = amounts[:width], amounts[width:]
                if sum(left[:-1], Decimal(0)) != left[-1] or sum(right[:-1], Decimal(0)) != right[-1]:
                    break
                columns.update(commission=left[:-1], fee=right[:-1])
                raw_values = re.findall(money, values.group('values'), re.I)
                literals.update(commission=raw_values[:width-1], fee=raw_values[width:-1])
                position = combined.end() + values.end()
                continue
            match = re.search(heading, tail[position:], re.I)
            if match is None:
                break
            start = position + match.end()
            values = re.match(r"\s*(?P<values>" + money + r"(?:\s+" + money + r")*)", tail[start:], re.I)
            if values is None:
                break
            amounts = [_decimal(value) for value in re.findall(money, values.group("values"), re.I)]
            if len(amounts) != len(policies) + 1 or sum(amounts[:-1], Decimal(0)) != amounts[-1]:
                break
            columns[key] = amounts[:-1]
            literals[key] = re.findall(money, values.group('values'), re.I)[:-1]
            position = start + values.end()
        if len(columns) != 4:
            continue
        source = "Producer and Address " + block[:identity.end() + position]
        if components is not None:
            for key, purpose in (('commission', 'COMMISSIONS'), ('fee', 'FEES'), ('bonus', 'BONUS PAID'), ('additional', 'ADDITIONAL COMPENSATION')):
                for policy, amount, literal in zip(policies, columns[key], literals[key]):
                    components.append({
                        'broker_name':identity.group('name'), 'address_line_1':identity.group('address'),
                        'city':identity.group('city').strip(), 'state':identity.group('state').upper(), 'zip_code':identity.group('zip'),
                        'coverage':re.sub(r'\s+', '', policy), 'literal':literal.strip(),
                        'amount':format(amount,'.2f'), 'category':'additional_compensation' if key=='additional' else key,
                        'purpose':purpose, 'page':page, 'source_text':source,
                    })
        fee_rows = [ScheduleABrokerMoneyRow(
            coverage=re.sub(r"\s+", "", policy), amount=format(amount, '.2f'), purpose=purpose,
        ) for key, purpose in (("fee", "FEES"), ("bonus", "BONUS PAID"), ("additional", "ADDITIONAL COMPENSATION"))
          for policy, amount in zip(policies, columns[key]) if amount != 0]
        rows.append(ScheduleABrokerRow(
            name=identity.group('name'), address_line_1=identity.group('address'),
            city=identity.group('city').strip(), state=identity.group('state').upper(), zip_code=identity.group('zip'),
            organization_code=identity.group('code'), source_page=page,
            commission_total=format(sum(columns['commission'], Decimal(0)), '.2f'),
            fee_total=format(sum((sum(columns[key], Decimal(0)) for key in ('fee', 'bonus', 'additional')), Decimal(0)), '.2f'),
            commission_rows=[ScheduleABrokerMoneyRow(coverage=re.sub(r"\s+", "", policy), amount=format(amount, '.2f'), purpose='COMMISSIONS')
                             for policy, amount in zip(policies, columns['commission']) if amount != 0],
            fee_rows=fee_rows, purpose='; '.join(dict.fromkeys(item.purpose for item in fee_rows)) or None,
            commission_source_text=source, fee_source_text=source, confidence=.99, decision='AUTOMATIC',
            evidence=[SourceEvidence(provider='Reconciled Hartford column-major table', page=page, source_text=source)],
        ))
    return rows


def extract_hartford_columnar_brokers(page_texts):
    rows = []
    if not any("ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION" in text.upper() for _, text in page_texts):
        return rows
    for page, text in page_texts:
        rows.extend(_flat_producer_columns(page, text))
        for block in re.split(r"Producer and Address[^\n]*\n", text, flags=re.IGNORECASE)[1:]:
            total = _TOTAL.search(block)
            if total is None:
                continue
            body = block[:total.start()]
            identities, components = [], []
            started = False
            code = None
            for line in body.splitlines():
                line = line.strip()
                match = _POLICY_ROW.fullmatch(line)
                if match:
                    prefix = match.group("identity").strip()
                    # Organization code shares the recipient's first row.
                    if not started:
                        explicit_code = re.search(r"\s+([0-9]{1,2})$", prefix)
                        if explicit_code:
                            code = explicit_code.group(1)
                            prefix = prefix[:explicit_code.start()].strip()
                    if prefix:
                        identities.append(prefix)
                    components.append(match)
                    started = True
                elif started and line:
                    identities.append(line)
            if not components or not identities:
                continue  # The existing vertically extracted shape has its own reader.
            city_index = next((i for i in range(len(identities)-1, -1, -1)
                               if re.fullmatch(r".+?,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?", identities[i])), None)
            if city_index is None:
                continue
            address_index = next((i for i, value in enumerate(identities[:city_index])
                                  if re.match(r"^(?:\d|P\.?\s*O\.?\s*BOX\b)", value, re.I)), None)
            if address_index is None or address_index == 0:
                continue
            city = re.fullmatch(r"(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", identities[city_index])
            sums = {key: sum((_decimal(c.group(key)) for c in components), Decimal(0))
                    for key in ("commission", "fee", "bonus", "additional")}
            mismatches = [key for key in sums if sums[key] != _decimal(total.group(key))]
            source = block[:total.end()].strip()
            fee_rows = [ScheduleABrokerMoneyRow(
                coverage=c.group("policy"), amount=format(_decimal(c.group(key)), ".2f"), purpose=purpose,
            ) for c in components for key, purpose in (
                ("fee", "FEES"), ("bonus", "BONUS PAID"), ("additional", "ADDITIONAL COMPENSATION"),
            ) if _decimal(c.group(key)) != 0]
            fee_purposes = list(dict.fromkeys(item.purpose for item in fee_rows if item.purpose))
            rows.append(ScheduleABrokerRow(
                name=" ".join(identities[:address_index]),
                address_line_1=" ".join(identities[address_index:city_index]),
                city=city.group(1).rstrip(","), state=city.group(2), zip_code=city.group(3),
                organization_code=code,
                purpose="; ".join(fee_purposes) if fee_purposes else None,
                commission_rows=[ScheduleABrokerMoneyRow(
                    coverage=c.group("policy"), amount=format(_decimal(c.group("commission")), ".2f"), purpose="COMMISSIONS",
                ) for c in components if _decimal(c.group("commission")) != 0],
                fee_rows=fee_rows,
                commission_total=format(sums["commission"], ".2f"),
                fee_total=format(sums["fee"]+sums["bonus"]+sums["additional"], ".2f"),
                commission_source_text=source, fee_source_text=source, source_page=page,
                confidence=.99, decision="REVIEW_REQUIRED" if mismatches else "AUTOMATIC",
                validation_results=[ExtractionValidationResult(
                    validator="producer_component_reconciliation", status="ERROR",
                    reason="Printed total differs from component sum: " + ", ".join(mismatches),
                )] if mismatches else [],
                evidence=[SourceEvidence(provider="Hartford columnar producer parser", page=page, source_text=source)],
            ))
    return rows


def extract_hartford_compensation_components(page_texts):
    """Expose every printed money cell, including zeros, for source review."""
    components = []
    if any('ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION' in text.upper() for _, text in page_texts):
        for page, text in page_texts:
            _flat_producer_columns(page, text, components)
    for row in extract_hartford_columnar_brokers(page_texts):
        for line in str(row.commission_source_text or "").splitlines():
            match = _POLICY_ROW.fullmatch(line.strip())
            if match is None:
                continue
            for key, category, purpose in (
                ("commission", "commission", "COMMISSIONS"), ("fee", "fee", "FEES"),
                ("bonus", "bonus", "BONUS PAID"), ("additional", "additional_compensation", "ADDITIONAL COMPENSATION"),
            ):
                components.append({"broker_name": row.name, "address_line_1": row.address_line_1,
                    "city": row.city, "state": row.state, "zip_code": row.zip_code,
                    "coverage": match.group("policy"), "literal": match.group(key),
                    "amount": format(_decimal(match.group(key)), ".2f"), "category": category, "purpose": purpose,
                    "page": row.source_page, "source_text": line.strip()})
    return components
