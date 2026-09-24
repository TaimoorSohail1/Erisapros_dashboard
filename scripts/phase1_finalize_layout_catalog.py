import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image


def canonical_client(record: dict) -> str:
    parts = [str(value) for value in (record.get("path_parts") or [])]
    if len(parts) >= 3 and parts[0].lower() == "folders" and parts[1].lower() == "erisa pros":
        return parts[2]
    if parts:
        return parts[0]
    return str(record.get("folder_path") or "Unknown").split(" > ", 1)[0]


def hamming(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def dhash(image_path: Path, size: int = 16) -> str:
    with Image.open(image_path) as image:
        gray = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
        values = list(gray.getdata())
    bits = []
    for y in range(size):
        row = values[y * (size + 1):(y + 1) * (size + 1)]
        bits.extend(row[x] > row[x + 1] for x in range(size))
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{size * size // 4}x}"


def render_first_page(pdf_path: Path, output_path: Path, pdftoppm: Path) -> bool:
    if output_path.exists() and output_path.stat().st_size > 0:
        return True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = output_path.with_suffix("")
    result = subprocess.run(
        [str(pdftoppm), "-f", "1", "-l", "1", "-scale-to", "1200", "-png", "-singlefile", str(pdf_path), str(prefix)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=90,
        check=False,
    )
    return result.returncode == 0 and output_path.exists()


def gold_score(member: dict) -> float:
    return (
        float(member.get("ocr_quality_score") or 0) * 50
        + min(int(member.get("section_coverage") or 0), 2) * 8
        + min(int(member.get("section_commissions_fees") or 0), 3) * 7
        + min(int(member.get("table_pages") or 0), 3) * 3
        - (10 if member.get("source_role") == "QA copy" else 0)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preview-dir", required=True)
    parser.add_argument("--pdftoppm", required=True)
    parser.add_argument("--visual-overrides")
    args = parser.parse_args()
    source = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    pages = json.loads(Path(args.pages).read_text(encoding="utf-8"))
    page_map = defaultdict(list)
    for page in pages:
        page_map[str(page.get("item_id"))].append(page)
    preview_dir = Path(args.preview_dir)
    pdftoppm = Path(args.pdftoppm)
    visual_overrides = json.loads(Path(args.visual_overrides).read_text(encoding="utf-8")) if args.visual_overrides else {}
    records = source["records"]
    visual_reviewed = 0
    for record in records:
        record["client"] = canonical_client(record)
        name = str(record.get("file_name") or "")
        record["source_role"] = "QA copy" if re.match(r"(?i)^(qa-|erisapros[-_ ]qa|erisapros-q)", name) else "Source document"
        record["is_system_test_folder"] = record["client"].lower() in {"highlandtech ai test folder", "erisapros auto scan qa 2026-08-21 2705"}
        local_value = str(record.get("local_path") or "")
        if not local_value:
            record.update({"inspection_status": "Unavailable", "include_status": "Excluded", "exclusion_reason": record.get("result") or "download unavailable"})
            continue
        if record["is_system_test_folder"]:
            record.update({"include_status": "Excluded", "exclusion_reason": "System QA folder, not a client folder"})
            continue
        if record.get("include_status") == "Needs visual/OCR review":
            pdf_path = Path(local_value)
            preview = preview_dir / f"{record.get('item_id')}__page1.png"
            if render_first_page(pdf_path, preview, pdftoppm):
                record["visual_hash"] = dhash(preview)
                record["preview_path"] = str(preview)
                record["visual_review_status"] = "Rendered for visual QA"
                record["include_status"] = "Included"
                record["classification_confidence"] = "Medium"
                record["ocr_risk"] = "High"
                record["ocr_quality"] = "Image-only; visual inspection required"
                visual_reviewed += 1
            else:
                record.update({"include_status": "Excluded", "exclusion_reason": "Image-only PDF could not be rendered", "visual_review_status": "Render failed"})

        if str(record.get("item_id")) in visual_overrides:
            record.update({
                "include_status": "Excluded",
                "exclusion_reason": visual_overrides[str(record.get("item_id"))],
                "visual_review_status": "Manually classified from rendered page",
            })

    known_visual = [record for record in records if record.get("visual_hash") and record.get("carrier") != "Unknown carrier"]
    for record in records:
        if not record.get("visual_hash") or record.get("carrier") != "Unknown carrier":
            continue
        candidates = [
            other for other in known_visual
            if other.get("page_count") == record.get("page_count")
            and other.get("page_size_signature") == record.get("page_size_signature")
        ]
        if not candidates:
            continue
        nearest = min(candidates, key=lambda other: hamming(record["visual_hash"], other["visual_hash"]))
        distance = hamming(record["visual_hash"], nearest["visual_hash"])
        if distance <= 18:
            record["carrier"] = nearest["carrier"]
            record["carrier_source"] = f"visual template match (distance {distance})"

    duplicate_groups = defaultdict(list)
    for record in records:
        duplicate_groups[str(record.get("hash") or record.get("item_id"))].append(record)
    duplicate_index = 0
    for members in duplicate_groups.values():
        if len(members) < 2:
            for member in members:
                member["duplicate_group"] = ""
                member["exact_duplicate_count"] = 1
            continue
        duplicate_index += 1
        duplicate_id = f"DUP-{duplicate_index:03d}"
        for member in members:
            member["duplicate_group"] = duplicate_id
            member["exact_duplicate_count"] = len(members)

    template_keys = sorted({(record.get("carrier"), record.get("template_family")) for record in records if record.get("include_status") == "Included"}, key=lambda item: tuple(str(value) for value in item))
    template_ids = {key: f"TPL-{index:03d}" for index, key in enumerate(template_keys, start=1)}
    structural_keys = sorted({
        (record.get("carrier"), record.get("template_family"), record.get("page_count"), record.get("page_size_signature"))
        for record in records if record.get("include_status") == "Included"
    }, key=lambda item: tuple(str(value) for value in item))
    structural_ids = {key: f"STR-{index:03d}" for index, key in enumerate(structural_keys, start=1)}
    for record in records:
        if record.get("include_status") != "Included":
            continue
        template_key = (record.get("carrier"), record.get("template_family"))
        structural_key = (record.get("carrier"), record.get("template_family"), record.get("page_count"), record.get("page_size_signature"))
        record["template_family_id"] = template_ids[template_key]
        record["structural_family_id"] = structural_ids[structural_key]

    digital_groups = defaultdict(list)
    scanned_candidates = defaultdict(list)
    for record in records:
        if record.get("include_status") != "Included":
            continue
        record_pages = sorted(page_map.get(str(record.get("item_id")), []), key=lambda item: int(item.get("page") or 0))
        anchor_profile = ";".join(
            f"{page.get('anchors','')}|T{page.get('table_markers',0)}|B{page.get('broker_row_markers',0)}"
            for page in record_pages
        )
        record["page_anchor_profile"] = anchor_profile
        base = (record.get("carrier"), record.get("template_family"), record.get("version_year"), record.get("page_count"), record.get("page_size_signature"))
        if record.get("visual_hash"):
            scanned_candidates[base].append(record)
        else:
            profile_hash = hashlib.sha1(anchor_profile.encode()).hexdigest()[:10]
            record["catalog_fingerprint"] = hashlib.sha1(f"{base}|{profile_hash}".encode()).hexdigest()[:14]
            digital_groups[(*base, profile_hash)].append(record)

    all_groups = list(digital_groups.items())
    for base, candidates in scanned_candidates.items():
        clusters = []
        for record in candidates:
            match = next((cluster for cluster in clusters if hamming(record["visual_hash"], cluster[0]["visual_hash"]) <= 24), None)
            if match is None:
                clusters.append([record])
            else:
                match.append(record)
        for cluster in clusters:
            representative_hash = cluster[0]["visual_hash"]
            for record in cluster:
                record["catalog_fingerprint"] = hashlib.sha1(f"{base}|{representative_hash}".encode()).hexdigest()[:14]
            all_groups.append(((*base, representative_hash[:12]), cluster))

    all_groups.sort(key=lambda item: (-len(item[1]), tuple(str(value) for value in item[0])))
    variant_counts = Counter()
    layouts = []
    for index, (key, members) in enumerate(all_groups, start=1):
        carrier, family, version_year, _page_count, _page_size, _profile = key
        variant_key = (carrier, family, version_year)
        variant_counts[variant_key] += 1
        variant = f"V{variant_counts[variant_key]:02d}"
        layout_id = f"LAY-{index:03d}"
        layout_name = f"{carrier} – {family} – {version_year} – {variant}"
        gold = max(members, key=lambda member: (gold_score(member), -len(str(member.get("file_name") or ""))))
        for member in members:
            member["layout_id"] = layout_id
            member["layout_name"] = layout_name
            member["layout_variant"] = variant
            member["is_gold_standard"] = member is gold
        clients = sorted({str(member.get("client") or "") for member in members})
        years = sorted({str(member.get("filing_year") or member.get("version_year") or "") for member in members})
        layouts.append({
            "layout_id": layout_id,
            "layout_name": layout_name,
            "carrier": carrier,
            "template_family": family,
            "version_year": version_year,
            "variant": variant,
            "catalog_fingerprint": members[0].get("catalog_fingerprint"),
            "template_family_id": members[0].get("template_family_id"),
            "structural_family_id": members[0].get("structural_family_id"),
            "pdf_count": len(members),
            "client_count": len(clients),
            "clients": "; ".join(clients),
            "filing_years": ", ".join(years),
            "page_count_mode": Counter(int(member.get("page_count") or 0) for member in members).most_common(1)[0][0],
            "ocr_high_risk_count": sum(1 for member in members if member.get("ocr_risk") == "High"),
            "broker_rows_present": any(int(member.get("broker_row_markers") or 0) > 0 for member in members),
            "totals_present": any(bool(member.get("has_total_label")) for member in members),
            "gold_standard_item_id": gold.get("item_id"),
            "gold_standard_file": gold.get("file_name"),
            "gold_standard_client": gold.get("client"),
            "classification_confidence": Counter(str(member.get("classification_confidence")) for member in members).most_common(1)[0][0],
        })

    included = [record for record in records if record.get("include_status") == "Included"]
    excluded = [record for record in records if record.get("include_status") == "Excluded"]
    summary = {
        "source_candidate_count": len(records),
        "inspected_pdf_count": sum(1 for record in records if record.get("inspection_status") in {"Inspected", "Corrupt/unreadable"}),
        "included_schedule_a_count": len(included),
        "excluded_count": len(excluded),
        "unavailable_count": sum(1 for record in records if record.get("inspection_status") == "Unavailable"),
        "client_count": len({record.get("client") for record in included}),
        "layout_count": len(layouts),
        "template_family_count": len(template_ids),
        "structural_family_count": len(structural_ids),
        "page_count": sum(int(record.get("page_count") or 0) for record in records if record.get("inspection_status") == "Inspected"),
        "high_ocr_risk_count": sum(1 for record in included if record.get("ocr_risk") == "High"),
        "visual_review_rendered_count": visual_reviewed,
        "system_test_folder_excluded_count": sum(1 for record in records if record.get("is_system_test_folder")),
        "qa_copy_count": sum(1 for record in included if record.get("source_role") == "QA copy"),
        "exact_duplicate_pdf_count": sum(1 for record in included if int(record.get("exact_duplicate_count") or 1) > 1),
        "unique_included_content_count": len({str(record.get("hash") or record.get("item_id")) for record in included}),
        "manual_visual_exclusion_count": len(visual_overrides),
    }
    Path(args.output).write_text(json.dumps({"summary": summary, "layouts": layouts, "records": records, "pages": pages}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
