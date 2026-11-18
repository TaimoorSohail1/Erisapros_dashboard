"""Read American Heritage's positioned multi-benefit payment reports."""
from datetime import datetime
from decimal import Decimal
import re

from app.models import (
    ExtractionValidationResult, NormalizedExtractionField, NormalizedExtractionResult,
    ScheduleABrokerMoneyRow, ScheduleABrokerRow, SourceEvidence,
)


_CARRIER = re.compile(
    r"Name of Insurance Carrier:[ \t]*AMERICAN HERITAGE LIFE"
    r"(?:[ \t]+|[ \t]*\r?\n[^\n]*?[ \t]{2,})INSURANCE COMPANY", re.I,
)
_CONTRACT = re.compile(r"Includes the Following Accounts:[ \t]*([A-Z0-9-]+)[ \t]*$", re.I | re.M)
_PERIOD = re.compile(r"Plan/Contract Year:[ \t]*(\d{1,2}-[A-Z]{3}-\d{4})[ \t]+to[ \t]+(\d{1,2}-[A-Z]{3}-\d{4})", re.I)
_MONEY = re.compile(r"\$(\d[\d,]*\.\d{2})")
_LOCALITY = re.compile(r"(.+?)[, ]+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", re.I)
_HEADER = re.compile(
    r"(?m)^[ \t]*(Part I, Sec 1 \(d\))[ \t]+(Part I, Sec 1 \(e\))[ \t]+"
    r"(Part III, Sec 10a)[ \t]+(Part I, Sec 3 Name and)[ \t]+(Agent Number)[ \t]+"
    r"(Part I, Sec 2 Total)[ \t]+(Part I, Sec 2 \(c\))[ \t]+(Part I, Sec 3 \(d\))",
    re.I,
)
_PROVIDER = "Reconciled American Heritage report"


def _money(text: str) -> Decimal:
    match = _MONEY.fullmatch(text)
    if not match:
        raise ValueError("Invalid payment cell")
    return Decimal(match[1].replace(",", ""))


def _read_page(page: int, text: str):
    carrier = _CARRIER.search(text)
    account = _CONTRACT.search(text)
    period = _PERIOD.search(text)
    header = _HEADER.search(text)
    ein = re.search(r"Part I \(b\) EIN:[ \t]*(\d{2}-\d{7})", text, re.I)
    naic = re.search(r"Part I \(c\) NAIC Code:[ \t]*(\d{5})\b", text, re.I)
    if not all((carrier, account, period, header, ein, naic)):
        raise ValueError("The report header is incomplete")
    coverage = re.search(r"(?m)^[ \t]*(.+?) Account:[ \t]*([A-Z0-9-]+)[ \t]*$",
                         text[header.end():], re.I)
    if not coverage or coverage[2] != account[1]:
        raise ValueError("Coverage account does not match the report account")
    benefit = coverage[1]
    offsets = [header.start(n) - header.start() for n in range(1, 9)]
    lines = text[header.end() + coverage.end():].splitlines()
    table = []
    grand = None
    for line in lines:
        if line.strip().startswith("Grand Total"):
            if grand is not None:
                raise ValueError("More than one grand total")
            amounts = _MONEY.findall(line)
            if len(amounts) != 3:
                raise ValueError("Incomplete grand total")
            grand = (page, line.strip(), tuple(Decimal(value.replace(",", "")) for value in amounts))
            break
        if re.search(r"^\s*(?:Notes|Disclaimers)\s*:", line, re.I):
            break
        table.append((line, [line[start:end].strip()
                            for start, end in zip(offsets, [*offsets[1:], None])]))
    coverage_index = next(index for index, (_, cells) in enumerate(table) if cells[0] and cells[1])
    coverage_line, coverage_cells = table[coverage_index]
    label = [coverage_cells[0]]
    for line, cells in table[coverage_index+1:]:
        if not cells[0] or any(cells[1:]):
            break
        label.append(cells[0])
        coverage_line += "\n" + line
    if " ".join(label) != benefit:
        raise ValueError("Coverage label does not match the printed account")
    count = int(coverage_cells[1].replace(",", ""))
    totals = tuple(_money(coverage_cells[index]) for index in (2, 5, 6))
    payment_indexes = [index for index, (_, cells) in enumerate(table) if cells[4]]
    payments = []
    for index, stop in zip(payment_indexes, [*payment_indexes[1:], len(table)]):
        line, cells = table[index]
        agent = cells[4]
        if not re.fullmatch(r"[A-Z0-9-]+", agent, re.I):
            raise ValueError("Invalid agent number")
        commission, fee = (_money(cells[key]) for key in (5, 6))
        details = [cells[3], *(candidate[3] for _, candidate in table[index+1:stop] if candidate[3])]
        locality = _LOCALITY.fullmatch(details[-1])
        street_index = next(i for i, part in enumerate(details[1:-1], 1)
                            if re.match(r"(?:\d+\s|P\.?\s*O\.?\s+BOX\b)", part, re.I))
        if not locality or (fee and not cells[7]):
            raise ValueError("Recipient address or fee classification is incomplete")
        street = details[street_index:-1]
        suite_index = next((i for i, part in enumerate(street) if re.match(r"(?:SUITE|STE|APT|FLOOR|FL)\b", part, re.I)),
                           len(street))
        identity = (" ".join(details[:street_index]), " ".join(street[:suite_index]),
                    " ".join(street[suite_index:]), *locality.groups())
        evidence = SourceEvidence(
            provider=_PROVIDER, page=page,
            source_text="\n".join(["Agent Number | Commissions Paid | Fees paid", line.strip(), *details[1:]]),
        )
        payments.append((agent, identity, commission, fee, cells[7], evidence))
    if len({item[0] for item in payments}) != len(payments) or tuple(
        sum((item[index] for item in payments), Decimal(0)) for index in (2, 3)
    ) != totals[1:]:
        raise ValueError("Recipient payments do not reconcile with printed benefit totals")
    begin, end = (datetime.strptime(value, "%d-%b-%Y").strftime("%m/%d/%Y") for value in period.groups())
    return {
        "identity": ("AMERICAN HERITAGE LIFE INSURANCE COMPANY", ein[1], naic[1], account[1], begin, end),
        "identity_source": "\n".join(match[0].strip() for match in (carrier, ein, naic, account, period))
                           + f"\nPlan/Contract Year (normalized): {begin} to {end}",
        "benefit": benefit, "count": count, "coverage_source": coverage_line.strip(),
        "totals": totals, "grand": grand, "payments": payments, "page": page,
    }


def reconcile_american_heritage_report(result: NormalizedExtractionResult, pages: list[tuple[int, str]]) -> bool:
    """Replace overlapping extractions only after all printed payments reconcile."""
    if not pages or not any(_HEADER.search(text) and _CARRIER.search(text) for _, text in pages):
        return False
    try:
        reports = [_read_page(page, text) for page, text in pages]
        first = reports[0]
        identity = first["identity"]
        grand, = [report["grand"] for report in reports if report["grand"]]
        if grand[0] != reports[-1]["page"] or any(report["identity"] != identity for report in reports):
            return False
        if len({report["benefit"].casefold() for report in reports}) != len(reports):
            return False
        if tuple(sum(report["totals"][index] for report in reports) for index in range(3)) != grand[2]:
            return False
        grouped = {}
        for report in reports:
            for agent, recipient, commission, fee, purpose, evidence in report["payments"]:
                row = grouped.setdefault(agent, ScheduleABrokerRow(
                    name=recipient[0], address_line_1=recipient[1], address_line_2=recipient[2] or None,
                    city=recipient[3], state=recipient[4], zip_code=recipient[5],
                    source_page=evidence.page, confidence=.99,
                ))
                old = (row.name, row.address_line_1, row.address_line_2 or "", row.city, row.state, row.zip_code)
                if old != recipient:
                    return False
                row.evidence.append(evidence)
                if commission:
                    row.commission_rows.append(ScheduleABrokerMoneyRow(
                        coverage=report["benefit"], amount=f"{commission:.2f}", purpose="COMMISSIONS",
                    ))
                if fee:
                    row.fee_rows.append(ScheduleABrokerMoneyRow(
                        coverage=report["benefit"], amount=f"{fee:.2f}", purpose=purpose,
                    ))
    except (ValueError, StopIteration):
        return False
    for row in grouped.values():
        row.commission_total = f"{sum(Decimal(item.amount) for item in row.commission_rows):.2f}"
        row.fee_total = f"{sum(Decimal(item.amount) for item in row.fee_rows):.2f}"
        row.commission_source_text = row.fee_source_text = "\n".join(item.source_text for item in row.evidence)
        row.purpose = "; ".join(dict.fromkeys(
            item.purpose for item in [*row.commission_rows, *row.fee_rows] if item.purpose
        )) or None
    identity_labels = (
        "1a. Name of Insurance Company", "1b. Insurance Carrier EIN", "1c. NAIC Code", "1d. Contract/Policy Number",
        "1f. Policy Year Beginning Date", "1g. Policy Year Ending Date",
    )
    fields = [NormalizedExtractionField(field_name=label, value=value, confidence=.99, page=first["page"],
        source_text=first["identity_source"]) for label, value in zip(identity_labels, identity)]
    payment_labels = ("10a. Total premiums or subscription charges paid to carrier", "3b. Amount of Commissions", "3c. Amount of Fees")
    fields.extend(NormalizedExtractionField(field_name=label, value=f"{value:,.2f}", confidence=.99,
        page=grand[0], source_text="Part III line 10a premium, Part I commissions and fees. " + grand[1])
        for label, value in zip(payment_labels, grand[2]))
    coverage = NormalizedExtractionField(
        field_name="1e. Persons Covered (End of Policy Year)", page=first["page"],
        source_text="\n".join(report["coverage_source"] for report in reports),
        candidate_values=list(dict.fromkeys(str(report["count"]) for report in reports)),
    )
    if len(reports) > 1:
        coverage.decision = "REVIEW_REQUIRED"
        coverage.validation_results.append(ExtractionValidationResult(
            validator="business_rule_confirmation", status="ERROR",
            reason="The carrier prints covered persons separately by benefit without a combined count.",
        ))
    else:
        coverage.value, coverage.confidence = str(first["count"]), .99
    fields.append(coverage)
    remove = {field.field_name for field in fields} | {
        "3a. Name of Agent/Broker/Person", "3d. Purpose", "3e. Organizational Code",
    }
    result.fields = [field for field in result.fields if field.field_name not in remove]
    result.fields.extend(fields)
    result.schedule_a_broker_rows = list(grouped.values())
    raw = dict(result.raw) if isinstance(result.raw, dict) else {}
    raw["american_heritage_payment_reconciliation"] = {
        "contract": identity[3], "recipient_count": len(grouped),
        "benefits": [{"page": report["page"], "coverage": report["benefit"], "covered": report["count"],
                      "source_text": report["coverage_source"]} for report in reports],
        "grand_totals": [str(value) for value in grand[2]],
    }
    result.raw = raw
    return True
