"""Read explicitly labelled parties and reconciled broker payment blocks."""
from decimal import Decimal
import re

from app.models import NormalizedExtractionField, ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence

_DOLLARS = r'(?:\(\$[\d,]+\.\d{2}\)|-?\$[\d,]+\.\d{2})'
_PARTY = re.compile(
    r'(?:Name\s+of\s+(?:Service\s+Organization|Insurance\s+Company|Insurance\s+Carrier)|Insurance\s+Carrier)'
    r'\s*:\s*(?P<name>[^\n:]{2,120}?)\s*'
    r'(?:Tax\s*ID\s*#?|Federal\s+Tax\s*ID|EIN)\s*:?\s*(?P<ein>\d{2}\s*-\s*\d{7})(?!\d)', re.I)
_HEADER = re.compile(r'Commissions?(?:\s+and\s+bonus)?\s+amounts?\s+paid\s+to\s+brokers?(?:\(s\))?', re.I)
_TOTAL = re.compile(r'Total\s+Compensation\s+Paid\s+to\s+Brokers?[^\n$]*?(?P<amount>'+_DOLLARS+r')', re.I)
_BONUS = re.compile(r'Bonuses\s+and\s+miscellaneous\s+compensation[^\n$]*?(?P<amount>'+_DOLLARS+r')', re.I)


def statement_identity_fields(pages):
    from app.services.extractor import is_probable_person_or_entity_name
    pairs = {}
    for page,text in pages:
        if not re.search(r'\bSchedule\s*A\b',text,re.I) or re.search(r'\bSchedule\s*C\b|Service Provider Information',text,re.I):
            continue
        for match in _PARTY.finditer(text):
            name = re.sub(r'\s+',' ',match['name']).strip()
            ein = re.sub(r'\s','',match['ein'])
            if is_probable_person_or_entity_name(name):
                pairs[(name.casefold(),ein)] = (page,name,ein,match[0])
    if len(pairs)!=1:
        return []
    page,name,ein,evidence = next(iter(pairs.values()))
    return [NormalizedExtractionField(field_name=label,value=value,confidence=.98,page=page,source_text=evidence,
        evidence=[SourceEvidence(provider='Native labelled carrier identity',page=page,source_text=evidence)])
        for label,value in (('1a. Name of Insurance Company',name),('1b. Insurance Carrier EIN',ein))]


def statement_broker_rows(pages):
    """Require a bounded payment section and an exactly reconciled footer.

    A shared bonus can only be assigned when exactly one recipient is printed.
    Unknown sections, missing totals and leftover text cannot authorize deletion.
    """
    from app.services.extractor import _compensation_address, is_probable_person_or_entity_name
    from app.services.schedule_a_broker_roster import _money
    result = []
    for page,text in pages:
        headers = list(_HEADER.finditer(text))
        for index,header in enumerate(headers):
            tail = text[header.end():headers[index+1].start() if index+1<len(headers) else len(text)]
            total = _TOTAL.search(tail)
            if not total:
                return []
            body = tail[:total.start()]
            bonuses = list(_BONUS.finditer(body))
            if len(bonuses)>1:
                return []
            bonus = Decimal(_money(bonuses[0]['amount'])) if bonuses else Decimal(0)
            body = body[:bonuses[0].start()] if bonuses else body
            rows,pending = [],[]
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                amount = re.fullmatch(r'(?P<prefix>.*?)\s*(?P<amount>'+_DOLLARS+r')',line)
                if not amount:
                    pending.append(line)
                    continue
                if amount['prefix'].strip():
                    pending.append(amount['prefix'].strip())
                if not pending or len(pending)>8 or not is_probable_person_or_entity_name(pending[0]):
                    return []
                address = _compensation_address(pending[1:])
                paid = _money(amount['amount'])
                source = header[0]+'\n'+'\n'.join(pending)+'\n'+amount['amount']
                rows.append(ScheduleABrokerRow(name=pending[0],address_line_1=address[0],address_line_2=address[1],
                    city=address[2],state=address[3],zip_code=address[4],organization_code='3',organization_code_defaulted=True,
                    commission_total=paid,fee_total='0',commission_rows=[ScheduleABrokerMoneyRow(amount=paid,purpose='Commissions')],
                    commission_source_text=source,source_page=page,confidence=.98,
                    evidence=[SourceEvidence(provider='Native reconciled compensation section',page=page,source_text=source)]))
                pending = []
            if pending or not rows or (bonus and len(rows)!=1):
                return []
            if bonus:
                row = rows[0]
                row.commission_total = format(Decimal(row.commission_total)+bonus,'f')
                row.commission_rows.append(ScheduleABrokerMoneyRow(amount=format(bonus,'f'),purpose='Bonus'))
                row.evidence.append(SourceEvidence(provider='Native compensation bonus',page=page,source_text=bonuses[0][0]))
            if sum((Decimal(row.commission_total) for row in rows),Decimal(0))!=Decimal(_money(total['amount'])):
                return []
            for row in rows:
                row.evidence.append(SourceEvidence(provider='Native compensation section total',page=page,source_text=total[0]))
            result.extend(rows)
    return result
