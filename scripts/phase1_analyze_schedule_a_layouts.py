import argparse
import hashlib
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader


CARRIER_PATTERNS = [
    ("UNUM", r"\bunum\b|unum life insurance"),
    ("Principal", r"\bprincipal life\b|principal financial"),
    ("UnitedHealthcare", r"unitedhealth|united healthcare|uhc"),
    ("New York Life", r"new york life|\bnyl\b"),
    ("MetLife", r"metlife|metropolitan life"),
    ("The Hartford", r"the hartford|hartford life"),
    ("Guardian", r"guardian life|guardian insurance"),
    ("Lincoln Financial", r"lincoln financial|lincoln national life"),
    ("Mutual of Omaha", r"mutual of omaha|united of omaha"),
    ("Aetna", r"\baetna\b"),
    ("Cigna", r"\bcigna\b|connecticut general life"),
    ("Kaiser Permanente", r"kaiser permanente|kaiser foundation"),
    ("Humana", r"\bhumana\b"),
    ("Blue Cross Blue Shield", r"blue cross|blue shield|\bbcbs\b"),
    ("Anthem", r"\banthem\b|elevance health"),
    ("Delta Dental", r"delta dental"),
    ("VSP", r"vision service plan|\bvsp\b"),
    ("EyeMed", r"eyemed"),
    ("Sun Life", r"sun life"),
    ("Prudential", r"prudential"),
    ("Reliance Standard", r"reliance standard"),
    ("Ameritas", r"ameritas"),
    ("Allstate", r"allstate"),
    ("Aflac", r"\baflac\b"),
    ("Chubb", r"\bchubb\b"),
    ("Berkley", r"berkley"),
    ("Symetra", r"symetra"),
    ("Unimerica", r"unimerica"),
    ("Transamerica", r"transamerica"),
    ("Standard Insurance", r"standard insurance company|the standard"),
    ("Life Insurance Company of North America", r"life insurance company of north america|\blina\b"),
]

ANCHORS = [
    "coverage information", "name of insurance carrier", "ein", "naic", "contract or identification number",
    "persons covered", "policy or contract year", "insurance fee and commissions", "commissions paid", "fees paid",
    "persons receiving commissions and fees", "name and address", "amount of commissions", "amount of fees",
    "purpose", "organization code", "broker", "agent", "total", "schedule a", "form 5500", "worksheet",
]


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def canonical_client(record: dict) -> str:
    parts = record.get("path_parts") or []
    if parts:
        return str(parts[0])
    folder = str(record.get("folder_path") or "")
    return folder.split(" > ", 1)[0] or str(record.get("client_name") or "Unknown")


def detect_carrier(text: str, file_name: str) -> tuple[str, str]:
    haystack = f"{file_name}\n{text[:10000]}".lower()
    for carrier, pattern in CARRIER_PATTERNS:
        if re.search(pattern, haystack, re.I):
            source = "filename/text" if re.search(pattern, file_name, re.I) else "document text"
            return carrier, source
    lines = [normalize_space(line) for line in text[:5000].splitlines() if normalize_space(line)]
    insurance_lines = [line for line in lines if re.search(r"insurance (company|carrier)|assurance company|health plan", line, re.I)]
    if insurance_lines:
        candidate = min(insurance_lines, key=len)
        return candidate[:90], "document text (inferred)"
    return "Unknown carrier", "not detected"


def detect_year(text: str, record: dict) -> str:
    patterns = [
        r"schedule\s+a\s*\(form\s+5500\)\s*(20\d{2})",
        r"schedule\s+a[^\n]{0,50}(20\d{2})",
        r"plan\s+year[^\n]{0,30}(20\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    value = record.get("filing_year")
    return str(value) if value else "Unknown"


def classify_family(text: str, file_name: str) -> tuple[str, str, str]:
    lower = text.lower()
    file_lower = file_name.lower()
    is_schedule_c = "schedule c (form 5500)" in lower or re.search(r"\bschedule\s+c\b", file_lower)
    has_schedule_a = "schedule a" in lower or re.search(r"\bschedule\s*a\b", file_lower)
    has_form_5500 = "form 5500" in lower
    has_core_sections = sum(
        marker in lower
        for marker in ("coverage information", "insurance information", "persons receiving commissions", "commissions paid", "fees paid")
    )
    if is_schedule_c and not has_schedule_a:
        return "Excluded – Schedule C", "Excluded", "High"
    if len(normalize_space(text)) < 80:
        return "Scanned / image-only Schedule A", "Needs visual/OCR review", "Low"
    if has_schedule_a and has_form_5500 and has_core_sections >= 2:
        return "Official IRS Schedule A form", "Included", "High"
    if "schedule a (form 5500) worksheet" in lower or ("worksheet" in lower and has_schedule_a):
        return "Carrier Schedule A worksheet", "Included", "High"
    if has_schedule_a and ("commission" in lower or "fee" in lower or "insurance" in lower):
        return "Carrier Schedule A disclosure", "Included", "Medium"
    if has_core_sections >= 2:
        return "Schedule A structured statement", "Included", "Medium"
    if has_schedule_a:
        return "Schedule A – unstructured/other", "Included", "Low"
    return "Excluded – not verified as Schedule A", "Excluded", "Medium"


def text_quality(text: str, page_count: int) -> tuple[str, float, str]:
    compact = normalize_space(text)
    chars_per_page = len(compact) / max(page_count, 1)
    printable = sum(1 for char in compact if char.isprintable()) / max(len(compact), 1)
    odd = sum(1 for char in compact if ord(char) > 126 and not char.isalnum()) / max(len(compact), 1)
    score = max(0.0, min(1.0, (chars_per_page / 1200.0) * printable * (1 - min(odd * 5, 0.7))))
    if chars_per_page >= 500 and score >= 0.45:
        return "Good text layer", score, "Low"
    if chars_per_page >= 120:
        return "Partial / weak text layer", score, "Medium"
    return "Image-only or unusable text layer", score, "High"


def page_features(page, page_number: int) -> tuple[dict, str, list[tuple]]:
    positioned = []
    fragments = []

    def visitor(text, _cm, tm, _font_dict, _font_size):
        fragments.append(text)
        for raw in re.findall(r"[A-Za-z]+", text or ""):
            token = raw.lower()
            if token in {"schedule", "coverage", "insurance", "carrier", "commissions", "commission", "fees", "fee", "persons", "broker", "agent", "purpose", "organization", "total", "policy", "contract", "worksheet"}:
                positioned.append((token, round(float(tm[4] or 0) / 50), round(float(tm[5] or 0) / 50)))

    try:
        text = page.extract_text(visitor_text=visitor) or ""
    except Exception:
        text = page.extract_text() or ""
    words = re.findall(r"\S+", text)
    lower = text.lower()
    found = []
    for anchor in ANCHORS:
        if anchor in lower:
            found.append(anchor)
    table_markers = sum(marker in lower for marker in ("commissions paid", "fees paid", "organization code", "name and address"))
    line_count = 0
    rect_count = 0
    broker_markers = len(re.findall(r"name and address of (?:the )?(?:agent|broker|other person)", lower))
    page_record = {
        "page": page_number,
        "width": round(float(page.mediabox.width), 1),
        "height": round(float(page.mediabox.height), 1),
        "rotation": int(page.get("/Rotate", 0) or 0),
        "text_chars": len(normalize_space(text)),
        "word_count": len(words),
        "vector_lines": line_count,
        "rectangles": rect_count,
        "anchor_count": len(found),
        "anchors": ", ".join(found),
        "table_markers": table_markers,
        "broker_row_markers": broker_markers,
    }
    return page_record, text, positioned


def analyze_pdf(record: dict) -> tuple[dict, list[dict]]:
    result = dict(record)
    result["client"] = canonical_client(record)
    local_path = Path(str(record.get("local_path") or ""))
    if not local_path.exists() or local_path.stat().st_size == 0:
        result.update({"inspection_status": "Unavailable", "include_status": "Excluded", "exclusion_reason": record.get("result") or "missing local file"})
        return result, []
    pages = []
    text_parts = []
    positioned = []
    try:
        pdf = PdfReader(str(local_path), strict=False)
        for page_number, page in enumerate(pdf.pages, start=1):
                page_record, page_text, page_positions = page_features(page, page_number)
                page_record["item_id"] = record.get("item_id")
                page_record["client"] = result["client"]
                page_record["file_name"] = record.get("file_name")
                pages.append(page_record)
                text_parts.append(page_text)
                positioned.extend((page_number, *item) for item in page_positions)
    except Exception as exc:
        result.update({"inspection_status": "Corrupt/unreadable", "include_status": "Excluded", "exclusion_reason": f"{type(exc).__name__}: {exc}"})
        return result, []
    text = "\n".join(text_parts)
    page_count = len(pages)
    carrier, carrier_source = detect_carrier(text, str(record.get("file_name") or ""))
    family, include_status, confidence = classify_family(text, str(record.get("file_name") or ""))
    version_year = detect_year(text, record)
    quality_label, quality_score, ocr_risk = text_quality(text, page_count)
    dimension_signature = ";".join(f"{round(p['width']/10)*10}x{round(p['height']/10)*10}" for p in pages)
    anchor_payload = json.dumps(sorted(set(positioned)), separators=(",", ":"), ensure_ascii=True)
    anchor_hash = hashlib.sha1(f"{dimension_signature}|{anchor_payload}".encode()).hexdigest()[:12]
    line_bucket = ";".join(str(min(99, int((p["vector_lines"] + p["rectangles"]) / 10))) for p in pages)
    structure_fingerprint = hashlib.sha1(f"{family}|{page_count}|{dimension_signature}|{line_bucket}|{anchor_hash}".encode()).hexdigest()[:14]
    text_lower = text.lower()
    commission_values = len(re.findall(r"(?:commission|commissions)[^\n]{0,40}\$?\s*[\d,]+(?:\.\d{2})?", text_lower))
    fee_values = len(re.findall(r"(?:fee|fees)[^\n]{0,40}\$?\s*[\d,]+(?:\.\d{2})?", text_lower))
    result.update({
        "inspection_status": "Inspected",
        "include_status": include_status,
        "exclusion_reason": "" if include_status == "Included" else family,
        "carrier": carrier,
        "carrier_source": carrier_source,
        "template_family": family,
        "version_year": version_year,
        "classification_confidence": confidence,
        "page_count": page_count,
        "page_size_signature": dimension_signature,
        "structure_fingerprint": structure_fingerprint,
        "anchor_fingerprint": anchor_hash,
        "ocr_quality": quality_label,
        "ocr_quality_score": round(quality_score, 3),
        "ocr_risk": ocr_risk,
        "section_coverage": sum(1 for marker in ("coverage information", "insurance information") if marker in text_lower),
        "section_commissions_fees": sum(1 for marker in ("commissions paid", "fees paid", "persons receiving commissions") if marker in text_lower),
        "table_pages": sum(1 for p in pages if p["table_markers"] >= 2 or p["vector_lines"] >= 10),
        "broker_row_markers": sum(p["broker_row_markers"] for p in pages),
        "commission_value_markers": commission_values,
        "fee_value_markers": fee_values,
        "has_total_label": "total" in text_lower,
        "has_ein_label": bool(re.search(r"\bein\b|employer identification", text_lower)),
        "has_naic_label": "naic" in text_lower,
        "has_policy_dates": bool(re.search(r"policy or contract year|\bfrom\b.{0,50}\bto\b", text_lower, re.S)),
        "text_chars_total": sum(p["text_chars"] for p in pages),
    })
    return result, pages


def assign_layouts(records: list[dict]) -> tuple[list[dict], list[dict]]:
    included = [r for r in records if r.get("include_status") == "Included"]
    groups = defaultdict(list)
    for record in included:
        key = (record.get("carrier"), record.get("template_family"), record.get("version_year"), record.get("structure_fingerprint"))
        groups[key].append(record)
    ordered = sorted(groups.items(), key=lambda item: (-len(item[1]), tuple(str(x) for x in item[0])))
    family_variant_counts = Counter()
    layouts = []
    for layout_index, (key, members) in enumerate(ordered, start=1):
        carrier, family, version_year, fingerprint = key
        family_key = (carrier, family, version_year)
        family_variant_counts[family_key] += 1
        variant = f"V{family_variant_counts[family_key]:02d}"
        layout_id = f"LAY-{layout_index:03d}"
        layout_name = f"{carrier} – {family} – {version_year} – {variant}"
        member_scores = []
        for member in members:
            score = (
                float(member.get("ocr_quality_score") or 0) * 50
                + min(int(member.get("section_coverage") or 0), 2) * 8
                + min(int(member.get("section_commissions_fees") or 0), 3) * 7
                + min(int(member.get("table_pages") or 0), 3) * 3
            )
            member_scores.append((score, str(member.get("file_name") or ""), member))
        gold = max(member_scores, key=lambda item: (item[0], -len(item[1])))[2]
        clients = sorted({str(m.get("client") or "") for m in members})
        years = sorted({str(m.get("filing_year") or m.get("version_year") or "") for m in members})
        for member in members:
            member["layout_id"] = layout_id
            member["layout_name"] = layout_name
            member["layout_variant"] = variant
            member["is_gold_standard"] = member is gold
        layouts.append({
            "layout_id": layout_id,
            "layout_name": layout_name,
            "carrier": carrier,
            "template_family": family,
            "version_year": version_year,
            "variant": variant,
            "structure_fingerprint": fingerprint,
            "pdf_count": len(members),
            "client_count": len(clients),
            "clients": "; ".join(clients),
            "filing_years": ", ".join(years),
            "page_count_mode": Counter(int(m.get("page_count") or 0) for m in members).most_common(1)[0][0],
            "ocr_high_risk_count": sum(1 for m in members if m.get("ocr_risk") == "High"),
            "broker_rows_present": any(int(m.get("broker_row_markers") or 0) > 0 for m in members),
            "totals_present": any(bool(m.get("has_total_label")) for m in members),
            "gold_standard_item_id": gold.get("item_id"),
            "gold_standard_file": gold.get("file_name"),
            "gold_standard_client": gold.get("client"),
            "classification_confidence": Counter(str(m.get("classification_confidence")) for m in members).most_common(1)[0][0],
        })
    return records, layouts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--page-output", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    records = []
    pages = []
    for index, record in enumerate(manifest, start=1):
        analyzed, page_records = analyze_pdf(record)
        records.append(analyzed)
        pages.extend(page_records)
        if index % 50 == 0 or index == len(manifest):
            print(f"analyzed {index}/{len(manifest)} pages={len(pages)}", flush=True)
    records, layouts = assign_layouts(records)
    summary = {
        "source_candidate_count": len(manifest),
        "inspected_pdf_count": sum(1 for r in records if r.get("inspection_status") == "Inspected"),
        "included_schedule_a_count": sum(1 for r in records if r.get("include_status") == "Included"),
        "excluded_count": sum(1 for r in records if r.get("include_status") == "Excluded"),
        "unavailable_count": sum(1 for r in records if r.get("inspection_status") == "Unavailable"),
        "client_count": len({r.get("client") for r in records if r.get("include_status") == "Included"}),
        "layout_count": len(layouts),
        "page_count": len(pages),
        "high_ocr_risk_count": sum(1 for r in records if r.get("include_status") == "Included" and r.get("ocr_risk") == "High"),
    }
    Path(args.output).write_text(json.dumps({"summary": summary, "layouts": layouts, "records": records}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    Path(args.page_output).write_text(json.dumps(pages, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
