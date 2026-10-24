"""Source-only review data; never manufactures destination rules or FTW tags."""
import re
from decimal import Decimal
from app.models import ExtractionValidationResult, NormalizedExtractionField, ScheduleABrokerMoneyRow, SourceEvidence
from app.services.schedule_a_hartford_columns import extract_hartford_compensation_components

_CURRENCY = r"(?:-\s*)?\$\s*-?\s*(?:\d[\d,]*(?:\.\d{1,2})?|\.\d{1,2})"
_PLAIN_AMOUNT = r"-?\d[\d,]*(?:\.\d{1,2})?"
_REPORTED_MONEY = (
    ("paid_premium", r"TOTAL\s+PREMIUMS?(?:\s+OR\s+SUBSCRIPTIONS?\s+CHARGES)?\s+PAID\s+TO\s+CARRIER[\s.:]*"),
    ("unpaid_premium", r"PREMIUMS\s+DUE\s+AND\s+UNPAID[^$]{0,160}"),
    ("contingent_compensation", r"TOTAL\s+CONTINGENT\s+COMP(?:ENSATION)?\s+PAID[\s.:]*"),
    ("ga_override", r"TOTAL\s+GA\s+OVERRIDES?\s+PAID[\s.:]*"),
    ("general_agent_fees", r"GENERAL\s+AGENT\s+FEES[\s.:]*"),
    ("carrier_fees", r"TOTAL\s+FEES\s+PAID\s+TO\s+CARRIER[\s.:]*"),
    ("incentive_compensation",r"Incentive\s+compensation\s+payments[^$]{0,200}?lump\s+sum\s+amount[\s:]*"),
    ("premium_due",r"Premium\s+Due\s+as\s+of\s+\d{1,2}/\d{1,2}/\d{4}[\s:]*"),
)


def _decimal(literal):
    return Decimal(re.sub(r"[$,\s]", "", literal))

_BENEFITS = (
    ("long_term_disability", r"LTD(?=\b|-)"),
    ("long_term_disability", r"Long\s*-?\s*term(?:\s+disability|[^\n]{0,30}?\bdisability)"),
    ("temporary_disability", r"Temporary\s+disability"),
    # Approved Schedule A benefit alias. Only an entire declared code is an
    # interpretation; a contract identifier or incidental DBL mention is not.
    ("temporary_disability", r"DBL(?=\s*$)"),
    ("supplemental_unemployment", r"Supplemental\s+unemployment"),
    ("prescription_drug", r"Prescription\s+drug"),
    ("stop_loss", r"Stop\s*[- ]?\s*Loss"),
    ("health", r"Health(?:\s+Insurance)?"),
    ("dental", r"Dental"), ("vision", r"Vision(?:\s+Care)?"),
    ("life", r"Life(?:\s+Insurance)?"), ("hmo", r"HMO(?:\s+contract)?"),
    ("ppo", r"PPO(?:\s+contract)?"), ("indemnity", r"Indemnity(?:\s+contract)?"),
    ("other", r"(?:Other(?:\s*\(\s*specify\s*\))?|AD\s*&\s*D|ADD|Accidental\s+Death[^\n]{0,35})"),
)


def _benefit(value):
    for kind, pattern in _BENEFITS:
        match = re.match(pattern + r"\b", value.strip(), re.I)
        if match:
            return kind, match.group(0)
    return None, value.strip()


def collect_schedule_a_source_observations(page_texts):
    result = {"benefit_types": [], "benefit_observations": [], "reported_values": [],
              "premium_components": [], "broker_components": [], "coverage_counts": [], "reconciliations": [],
              "source_instructions": []}
    seen = set()
    premium_blocks = set()
    def add_value(key, page, literal, source, *, unreadable=False):
        value = format(_decimal(literal), "f") if literal else ""
        kind = ("UNREADABLE" if unreadable else "PRINTED_BLANK") if not literal else "PRINTED_ZERO" if _decimal(literal) == 0 else "PRINTED_VALUE"
        identity = (key, page, value, kind)
        if identity in seen:
            return
        seen.add(identity)
        result["reported_values"].append({"key": key, "literal": literal, "value": value,
            "value_kind": kind, "page": page, "source_text": source,
            "decision": "REVIEW_REQUIRED" if unreadable else "SOURCE_ONLY"})
    def add_benefit(page, literal, source):
        kind, selected = _benefit(literal)
        identity = (page, kind, selected, source)
        if identity in seen:
            return
        seen.add(identity)
        result["benefit_observations"].append({
            "benefit_type": kind, "literal": selected, "page": page, "source_text": source,
            "decision": "SOURCE_ONLY" if kind else "PENDING_INTERPRETATION",
        })
        if kind and kind not in result["benefit_types"]:
            result["benefit_types"].append(kind)
    for page, text in page_texts:
        for payment in re.finditer(r'Commissions\s*/\s*Fees(?P<context>[^$]{0,1000})(?P<amount>'+_CURRENCY+r')',text,re.I):
            context=payment.group('context')
            if not (re.search(r'Paid\s+for\s+Policy',context,re.I) and re.search(r'Agent\s+or\s+Broker',context,re.I)):
                continue
            if re.search(r'certif(?:ies|ication)|hereby',context,re.I):
                continue
            add_value('combined_broker_payment',page,payment.group('amount'),payment.group(0))
            for value in result['reported_values']:
                if value['key']=='combined_broker_payment' and value['page']==page and value['source_text']==payment.group(0):
                    value['value_kind']='PRINTED_COMBINED_PAYMENT'
                    value['decision']='PENDING_CLASSIFICATION'
        for instruction in re.finditer(
            r'\b12[.)]?\s+If\s+the\s+answer\s+to\s+line\s+11\b.{0,500}?\bAnswer\s*["“]?\s*(Not\s+Applicable)\s*["”]?',
            text, re.I | re.S,
        ):
            result['source_instructions'].append({
                'field_reference':'12', 'instruction_value':'Not Applicable',
                'literal':instruction.group(0), 'source_text':instruction.group(0), 'page':page,
                'applicability':'Only if line 11 is Yes; this source instruction is not an independently populated answer.',
                'decision':'SOURCE_ONLY_NOT_AN_OUTBOUND_ANSWER',
            })
        if re.search(r"SALES[\s\S]{0,140}COMMISSION[\s\S]{0,100}FEES[\s\S]{0,100}COMPENSATION", text, re.I):
            amount = r"[-+]?\$?(?:\d[\d,]*)?\.\d{2}"
            for match in re.finditer(r"(?m)^[ \t]*(?P<name>[A-Za-z][^\n$]+?)[ \t]+"
                                     + r"[ \t]+".join(rf"(?P<{key}>{amount})" for key in ("commission", "fee", "additional"))
                                     + r"[ \t]*$", text):
                for column, category, purpose in (("commission", "commission", "COMMISSIONS"),
                        ("fee", "fee", "FEES"), ("additional", "additional_compensation", "ADDITIONAL COMPENSATION")):
                    result["broker_components"].append({"broker_name": match.group("name").strip(),
                        "coverage": None, "literal": match.group(column), "amount": format(_decimal(match.group(column)), ".2f"),
                        "category": category, "purpose": purpose, "page": page, "source_text": match.group(0).strip()})
        if re.search(r"Commissions\s+Paid", text, re.I) and re.search(r"Coverage[ \t]+Amount[ \t|_]+Purpose", text, re.I):
            names = re.findall(r"\bName\s*:\s*([^\n]+)", text, re.I)
            broker_name = re.split(r"\bField\s+\d+[a-z]\b", names[0], flags=re.I)[0].strip() if len(names) == 1 else None
            components = []
            for match in re.finditer(r"(?m)^\s*(?P<coverage>[A-Za-z][A-Za-z &/\-]{1,35}?)\s+"
                                     r"(?P<amount>" + _PLAIN_AMOUNT + r")[ \t]*(?:[|}][ \t]*)?Base[ \t]+Commissions(?:[ \t]+\d{1,2})?"
                                     r"(?:[ \t]+Fields?\s+\d+[a-z][^\n]*)?[ \t]*$", text, re.I):
                coverage = match.group("coverage").strip()
                source = match.group(0).strip()
                # A wrapped qualifier may be on the immediately preceding
                # physical line. Never borrow it across blank lines, headers,
                # recipient boundaries, or pages, or infer it for bare Disability.
                if re.fullmatch(r"Disability", coverage, re.I):
                    previous = re.search(
                        r"(?:^|\n)[ \t]*(?P<qualifier>(?:Long|Short)[ \t]+Term)[ \t]*\r?\n[ \t]*\Z",
                        text[:match.start("coverage")], re.I,
                    )
                    if previous:
                        qualifier = previous.group("qualifier")
                        coverage = qualifier + " " + coverage
                        source = qualifier + "\n" + source
                if re.fullmatch(r"Long\s+Term", coverage, re.I) and re.match(r"\s*Disability\b", text[match.end():], re.I):
                    coverage += " Disability"
                    source += "\nDisability"
                components.append({"broker_name": broker_name, "coverage": coverage,
                    "amount": format(_decimal(match.group("amount")), "f"), "literal": match.group("amount"),
                    "category": "commission", "purpose": "Base Commissions", "page": page, "source_text": source})
            result["broker_components"].extend(components)
            total = re.search(r"(" + _PLAIN_AMOUNT + r")[ \t]+(?:[|}][ \t]*)?Sub\s*-?\s*total", text, re.I)
            if components and total:
                calculated = sum((Decimal(c["amount"]) for c in components), Decimal(0))
                printed = _decimal(total.group(1))
                result["reconciliations"].append({"kind": "broker_commission_components", "page": page,
                    "component_sum": format(calculated, "f"), "printed_total": format(printed, "f"),
                    "result": "MATCH" if calculated == printed else "MISMATCH", "source_text": total.group(0)})
        benefit_table = re.search(r"Benefits?\s*(?:&|and)[\s\S]{0,180}?\bContract\s+Type\b", text, re.I)
        if benefit_table:
            date = r"\d{1,2}/\d{1,2}/\d{4}"
            body = re.split(r"(?im)^[ \t]*(?:[23][.)]\s+Insurance\b|Part\s+[IV]+\b)",
                            text[benefit_table.end():], maxsplit=1)[0]
            for match in re.finditer(r"(?m)^[ \t]*(?P<coverage>[A-Za-z][A-Za-z &/\-]{1,40}?)[ \t]+"
                                     r"(?P<count>[\d,]+)(?![\d,.])(?:[ \t]+(?P<period>[^\n]*))?[ \t]*$", body):
                kind,literal = _benefit(match.group('coverage'))
                if not kind or literal.casefold() != match.group('coverage').strip().casefold():
                    continue
                period = re.fullmatch(r"(" + date + r")\s+(" + date + r")", (match.group('period') or '').strip())
                result["coverage_counts"].append({"coverage": match.group("coverage"),
                    "persons_covered": match.group("count").replace(",", ""), "from": period.group(1) if period else None,
                    "to": period.group(2) if period else None, "page": page, "source_text": match.group(0).strip()})
                add_benefit(page, match.group("coverage"), match.group(0).strip())
        for block in re.finditer(
            r"\b(?:9|10)[.)]?\s+Non\s*[- ]?\s*experience\s*[- ]?\s*rated\s+contracts\s+Coverage\s+Amount\s*"
            # OCR can retain the printed subsection underline as "a_". Keep
            # the full paid-premium label mandatory rather than borrowing an
            # amount from another subsection or discarding the component table.
            r"(?P<body>.*?)\ba[._]?\s+Total\s+premiums\s+or\s+subscription\s+charges\s+paid\s+to\s+carrier[\s.:]*\$?\s*"
            r"(?P<total>" + _PLAIN_AMOUNT + ")", text, re.I | re.S,
        ):
            signature = (page, re.sub(r"\s+", " ", block.group(0)).casefold())
            if signature in premium_blocks:
                continue
            premium_blocks.add(signature)
            components = []
            for match in re.finditer(r"(?m)^\s*(?P<coverage>[A-Za-z][A-Za-z &/\-]{1,45}?)\s+"
                                     r"(?P<amount>" + _PLAIN_AMOUNT + r")[ \t]*$", block.group("body")):
                components.append({"coverage": match.group("coverage"),
                    "amount": format(_decimal(match.group("amount")), "f"), "literal": match.group("amount"),
                    "page": page, "source_text": match.group(0).strip(), "persons_covered": None})
                add_benefit(page, match.group("coverage"), match.group(0).strip())
            result["premium_components"].extend(components)
            if components:
                total = _decimal(block.group("total"))
                calculated = sum((Decimal(r["amount"]) for r in components), Decimal(0))
                result["reconciliations"].append({"kind": "premium_components", "page": page,
                    "component_sum": format(calculated, "f"), "printed_total": format(total, "f"),
                    "result": "MATCH" if calculated == total else "MISMATCH", "source_text": block.group(0)})
        if re.search(r"Premium\s+was\s+applied\s+as\s+follows", text, re.I):
            components = []
            for match in re.finditer(
                r"(?m)^\s*(?P<contract>[A-Z0-9-]{4,})\s+(?P<coverage>[A-Z][A-Z0-9]*(?:[ \t]*-[ \t]*[A-Z0-9]+)*)\s+"
                r"(?P<amount>" + _CURRENCY + r")[ \t]*\)?[ \t]+(?P<count>[\d,]+)\s*$", text, re.I,
            ):
                component = {"coverage": match.group("coverage"), "contract": match.group("contract"),
                    "amount": format(_decimal(match.group("amount")), "f"), "literal": match.group("amount"),
                    "persons_covered": match.group("count").replace(",", ""), "page": page,
                    "source_text": match.group(0).strip()}
                components.append(component)
                add_benefit(page, match.group("coverage"), match.group(0).strip())
            result["premium_components"].extend(components)
            total = re.search(r"\bTotal\s+(" + _CURRENCY + ")", text, re.I)
            if components and total:
                calculated = sum((Decimal(r["amount"]) for r in components), Decimal(0))
                result["reconciliations"].append({"kind": "premium_components", "page": page,
                    "component_sum": format(calculated, "f"), "printed_total": format(_decimal(total.group(1)), "f"),
                    "result": "MATCH" if calculated == _decimal(total.group(1)) else "MISMATCH",
                    "source_text": total.group(0)})
        for key, pattern in _REPORTED_MONEY:
            for match in re.finditer(pattern + r"(?P<amount>" + _CURRENCY + ")", text, re.I):
                add_value(key, page, match.group("amount"), match.group(0))
                if key=='premium_due':
                    as_of=re.search(r'\d{1,2}/\d{1,2}/\d{4}',match.group(0)).group(0)
                    for value in result['reported_values']:
                        if value['key']==key and value['page']==page and value['source_text']==match.group(0):
                            value['as_of']=as_of
        for heading in re.finditer(r"Additional\s+costs\s+incurred|incurred\s+(?:any\s+)?specific\s+costs", text, re.I):
            tail = text[heading.end():heading.end()+600]
            tail = re.split(r"Specify\s+(?:nature|Nature)|Footnotes?\b|(?m:^\s*PART\s+[IV]+\b)", tail, maxsplit=1, flags=re.I)[0]
            report_line = re.search(r"REPORT\s+AMOUNT(?P<cell>[^\n]*)", tail, re.I)
            direct_label = heading.group(0).lower().startswith('additional')
            cell = report_line.group('cell') if report_line else tail.split('\n', 1)[0] if direct_label else ''
            amounts = list(re.finditer(_CURRENCY, cell))
            # Some statements omit the currency sign. A plain decimal must
            # terminate the labelled amount cell; section digits and dotted
            # leaders are not money. Multiple different candidates fail closed.
            terminal = re.search(r"(?<![\d.,])(-?(?:\d[\d,]*\.\d{2}|\.\d{2}))[ \t]*$", cell)
            if terminal:
                amounts.extend(re.finditer(r"(?<![\d.,])-?(?:\d[\d,]*\.\d{2}|\.\d{2})(?![\d.,])", cell))
            amount = amounts[0] if amounts and len({_decimal(item.group(0)) for item in amounts}) == 1 else None
            complete_label = bool(report_line) or direct_label
            unreadable = not amount and not (complete_label and re.fullmatch(r"[\s.$_:,-]*", cell))
            add_value("additional_costs", page, amount.group(0).strip() if amount else "",
                      text[heading.start():heading.end()] + (tail[:report_line.end()] if report_line else cell),
                      unreadable=unreadable)
        for match in re.finditer(r"(?:\[\s*[Xx✗✘✓]\s*\]|\(\s*[Xx✗✘✓]\s*\)|☑|☒|✓|✗|✘|(?<!\w)X(?=\s))\s*(?=([^\n]{1,100}))", text):
            kind, literal = _benefit(match.group(1))
            if kind:
                add_benefit(page, literal, text[match.start():match.end()+len(literal)])
        for match in re.finditer(
            r"(?<![A-Za-z])(?:COVERAGE(?:\s*/\s*BENEFITS)?(?:\s+PROVIDED)?|BENEFIT\s+TYPE)\s*[:\-]\s*([^\n]+)", text, re.I,
        ):
            literal = re.split(r"Policy\s+or\s+Contract|Insurance\s+Carrier|Group\s+(?:ID|Number|Legal)", match.group(1), maxsplit=1, flags=re.I)[0].strip()
            if not re.search(r"\bshould\b|\bwould\b|\bfield\s+\d", literal, re.I):
                add_benefit(page, literal, match.group(0))
        for match in re.finditer(r"Benefit\s+and\s+contract\s+type([^\n]*)\n\s*\([a-m]\)\s*([^\n]+)", text, re.I):
            if not re.search(r"check|box", match.group(1), re.I):
                kind, literal = _benefit(match.group(2))
                if kind and re.sub(r"\s+", " ", match.group(2)).strip().casefold() == literal.casefold():
                    add_benefit(page, literal, match.group(0))
    result["benefit_types"].sort()
    result["broker_components"].extend(extract_hartford_compensation_components(page_texts))
    return result


def _reconcile_cost_source_reads(observations, raw, page_texts, source_sha256):
    """Recover only source-only cost evidence from an already computed read.

    The primary parser route is unchanged. Different amounts or a proven blank
    cannot be overwritten by an alternative OCR segmentation. Neither mode is
    an independent provider or a business approval.
    """
    if not source_sha256 or not isinstance(raw, dict):
        return
    reads = raw.get('local_ocr_source_reads')
    if not isinstance(reads, list):
        return
    pages = {page for page, _ in page_texts}
    alternatives = {}
    for read in reads:
        if (not isinstance(read, dict) or read.get('source_sha256') != source_sha256
                or read.get('psm') != 6 or type(read.get('page')) is not int
                or read['page'] not in pages or not isinstance(read.get('text'), str)):
            continue
        values = collect_schedule_a_source_observations([(read['page'], read['text'])])['reported_values']
        for value in values:
            if value['key'] == 'additional_costs' and value['value_kind'] in {'PRINTED_VALUE', 'PRINTED_ZERO'}:
                alternatives.setdefault(read['page'], []).append({**value, 'ocr_psm': 6,
                    'source_sha256': source_sha256})
    for page, candidates in alternatives.items():
        primary = [value for value in observations['reported_values']
                   if value['key'] == 'additional_costs' and value['page'] == page]
        distinct = {Decimal(value['value']) for value in candidates}
        known = [value for value in primary if value['value_kind'] != 'UNREADABLE']
        conflict = len(distinct) != 1 or any(
            value['value_kind'] == 'PRINTED_BLANK' or Decimal(value['value']) not in distinct for value in known)
        action = 'CONFLICT_REQUIRES_REVIEW' if conflict else 'RETAIN_PRIMARY' if known else 'RECOVER_SOURCE_ONLY'
        observations.setdefault('cost_source_reconciliations', []).append({
            'page': page, 'primary': primary[0].copy() if len(primary) == 1 else [value.copy() for value in primary],
            'alternates': candidates, 'action': action,
        })
        if conflict:
            for value in primary:
                value['decision'] = 'REVIEW_REQUIRED'
            continue
        if not known:
            observations['reported_values'] = [value for value in observations['reported_values']
                if not (value['key'] == 'additional_costs' and value['page'] == page)]
            observations['reported_values'].append(candidates[0])


def attach_schedule_a_source_observations(result, page_texts, *, source_sha256=None):
    observations = collect_schedule_a_source_observations(page_texts)
    _reconcile_cost_source_reads(observations, result.raw, page_texts, source_sha256)
    components=observations['premium_components']
    if len(components)==1 and components[0].get('persons_covered') is not None and not any(
        field.field_name.startswith('1e.') for field in result.fields
    ):
        component=components[0]
        page_text=next((text for page,text in page_texts if page==component['page']),'')
        header=next((line for line in page_text.splitlines() if re.search(r'(?:Lives|Persons)\s+Covered',line,re.I)),None)
        if header:
            source=header+'\n'+component['source_text']
            result.fields.append(NormalizedExtractionField(field_name='1e. Persons Covered (End of Policy Year)',
                value=component['persons_covered'],confidence=.94,page=component['page'],source_text=source,
                evidence=[SourceEvidence(provider='Single labelled coverage row',page=component['page'],source_text=source)]))
            observations['covered_count_derivation']='One printed coverage row with an explicit lives-covered header; no sum, maximum, or population substitution.'
    observations["mapping_mode"] = "EXTRACTION_ONLY_REVIEW"
    raw = dict(result.raw) if isinstance(result.raw, dict) else {"provider_raw": result.raw}
    raw["source_review"] = observations
    raw["benefit_types"] = observations["benefit_types"]
    result.raw = raw
    key = lambda v: re.sub(r"[^a-z0-9]", "", str(v or "").casefold())
    for row in result.schedule_a_broker_rows:
        matching_recipients = [candidate for candidate in result.schedule_a_broker_rows
                               if key(candidate.name) == key(row.name)]
        ambiguous_name_only = any(
            c["category"] == "commission" and c["broker_name"]
            and key(c["broker_name"]) == key(row.name) and not c.get("address_line_1")
            for c in observations["broker_components"]
        ) and len(matching_recipients) > 1
        if ambiguous_name_only:
            if not any(v.validator == "recipient_identity_ambiguity" for v in row.validation_results):
                row.validation_results.append(ExtractionValidationResult(
                    validator="recipient_identity_ambiguity", status="ERROR",
                    reason="Name-only source components match multiple recipient rows; retain components separately until their recipient is confirmed.",
                ))
            row.decision = "REVIEW_REQUIRED"
            continue
        components = [c for c in observations["broker_components"]
                      if c["category"] == "commission" and c["broker_name"] and key(c["broker_name"]) == key(row.name)
                      and (not c.get("address_line_1") or key(c["address_line_1"]) == key(row.address_line_1))]
        if not components or row.commission_total in {None, ""}:
            continue
        total = sum((Decimal(c["amount"]) for c in components), Decimal(0))
        if total != _decimal(row.commission_total):
            row.validation_results.append(ExtractionValidationResult(validator="source_component_total", status="ERROR",
                reason="Source broker component sum differs from the extracted total; retain both for review."))
            row.decision = "REVIEW_REQUIRED"
            continue
        row.commission_rows = [ScheduleABrokerMoneyRow(
            coverage=c["coverage"], amount=c["amount"], purpose=c["purpose"],
        ) for c in components]
        for component in components:
            row.evidence.append(SourceEvidence(provider="Source-only broker component reconciliation",
                page=component["page"], source_text="Name: " + component["broker_name"] + "\n" + component["source_text"]))
    for check in observations["reconciliations"]:
        if check["kind"] != "premium_components" or check["result"] != "MISMATCH":
            continue
        for field in result.fields:
            if field.field_name.startswith("10a."):
                field.validation_results.append(ExtractionValidationResult(validator="source_component_total", status="ERROR",
                    reason="Printed premium component sum does not reconcile with its printed total; review the source."))
                field.decision = "REVIEW_REQUIRED"
