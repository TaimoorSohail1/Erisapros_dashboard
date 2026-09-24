"""Run a reversible ftwLink update canary against an explicitly named demo plan.

This script never enables application update flags. It reads a baseline, changes
one numeric field, verifies the read-back, and restores the baseline in finally.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.models import ExtractedField, FieldPriority, FormType, FTWilliamsQueryRequest
from app.services.ftwilliams import FTWilliamsService
from app.services.xml_builder import (
    build_schedule_a_records_update_xml,
    build_single_document_update_xml,
    current_values_for_schedule_a_update,
    schedule_a_broker_multipart_rows,
    schedule_a_replacement_data_gaps,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-demo-account", required=True)
    parser.add_argument("--ftw-customer-id", required=True)
    parser.add_argument("--ftw-plan-id", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--expected-plan-name", required=True)
    parser.add_argument("--expected-ein", required=True)
    parser.add_argument("--expected-plan-number", required=True)
    parser.add_argument("--baseline-output", required=True)
    return parser.parse_args()


def result(response) -> dict:
    return {
        "http_status": response.http_status,
        "success": response.success,
        "codes": [item.error_code for item in response.statuses],
        "descriptions": [item.error_desc for item in response.statuses if item.error_desc],
    }


def _record(status) -> dict:
    return {
        "ftw_seq_no": status.ftw_seq_no,
        "query_results": status.query_results,
        "query_subparts": status.query_subparts,
    }


def _canonical_schedule_record(
    record: dict,
    *,
    ignored_tags: set[str] | None = None,
    ignored_broker_tags: set[str] | None = None,
) -> tuple:
    ignored_tags = ignored_tags or set()
    ignored_broker_tags = ignored_broker_tags or set()
    values = current_values_for_schedule_a_update(record.get("query_results") or {})
    for rows in (record.get("query_subparts") or {}).values():
        for row in rows:
            for tag in row:
                values.pop(str(tag), None)
    fields = tuple(sorted(
        (str(tag), str(value or "").strip())
        for tag, value in values.items()
        if tag not in ignored_tags and str(value or "").strip()
    ))
    brokers = schedule_a_broker_multipart_rows(
        record.get("query_results") or {},
        query_subparts=record.get("query_subparts") or {},
    )
    canonical_brokers = tuple(sorted(
        tuple(sorted(
            (str(tag), str(value or "").strip())
            for tag, value in row.items()
            if tag not in ignored_broker_tags and str(value or "").strip()
        ))
        for row in brokers
    ))
    return fields, canonical_brokers


def _canonical_schedule_set(records: list[dict]) -> tuple:
    return tuple(sorted(_canonical_schedule_record(record) for record in records))


def _single_record_block_reason(records: list[dict]) -> str | None:
    if len(records) == 1:
        return None
    return (
        "Single-Schedule-A canary requires exactly 1 current record; "
        f"found {len(records)}. No update was sent."
    )


def _matching_schedule_record(baseline: dict, candidates: list[dict]) -> dict | None:
    baseline_sequence = str(baseline.get("ftw_seq_no") or "").strip()
    by_sequence = [
        candidate
        for candidate in candidates
        if baseline_sequence and str(candidate.get("ftw_seq_no") or "").strip() == baseline_sequence
    ]
    if len(by_sequence) == 1:
        return by_sequence[0]

    identity_tags = ("InsContractNum", "InsCarrierEIN", "InsCarrierNAICCode", "InsCarrierName", "ScheduleDesc")
    baseline_values = baseline.get("query_results") or {}
    identity = tuple(
        (tag, str(baseline_values.get(tag) or "").strip().casefold())
        for tag in identity_tags
        if str(baseline_values.get(tag) or "").strip()
    )
    if not identity:
        return candidates[0] if len(candidates) == 1 else None
    matches = [
        candidate
        for candidate in candidates
        if all(
            str((candidate.get("query_results") or {}).get(tag) or "").strip().casefold() == value
            for tag, value in identity
        )
    ]
    return matches[0] if len(matches) == 1 else None


async def _query_all_schedule_records(
    service: FTWilliamsService,
    *,
    ftw_customer_id: str,
    ftw_plan_id: str,
    year: str,
    slot_count: int = 20,
) -> tuple[list[dict], dict]:
    """Read every Schedule A slot before any replace-style update."""

    responses = []

    async def query_slot(sequence: int):
        response = await service.run_query(FTWilliamsQueryRequest(
            operation="query_schedule_a",
            ftw_customer_id=ftw_customer_id,
            ftw_plan_id=ftw_plan_id,
            year=year,
            ftw_seq_no=str(sequence),
            send=True,
        ))
        return sequence, response

    for batch_start in range(1, slot_count + 1, 5):
        batch = range(batch_start, min(slot_count + 1, batch_start + 5))
        responses.extend(await asyncio.gather(*(query_slot(sequence) for sequence in batch)))

    records: list[dict] = []
    for sequence, response in responses:
        for status in response.statuses:
            if str(status.error_code or "") != "0":
                continue
            record = _record(status)
            if not str(record.get("ftw_seq_no") or "").strip():
                record["ftw_seq_no"] = str(sequence)
            records.append(record)
    return records, {
        "success": bool(records),
        "queried_slots": slot_count,
        "record_count": len(records),
        "http_statuses": sorted({response.http_status for _, response in responses if response.http_status}),
    }


async def main() -> int:
    args = parse_args()
    if args.confirm_demo_account.strip().casefold() != "highlandtech":
        raise SystemExit("Refusing to run without --confirm-demo-account HighlandTech")
    if not (args.year.isdigit() and len(args.year) == 4):
        raise SystemExit("A four-digit demo plan year is required.")
    baseline_path = Path(args.baseline_output).expanduser().resolve()
    if baseline_path.exists():
        raise SystemExit(f"Refusing to overwrite the existing baseline: {baseline_path}")
    service = FTWilliamsService()

    async def query(operation: str):
        return await service.run_query(FTWilliamsQueryRequest(
            operation=operation,
            ftw_customer_id=args.ftw_customer_id,
            ftw_plan_id=args.ftw_plan_id,
            year=args.year,
            send=True,
        ))

    report = {
        "test": "highland_demo_update_readback_restore",
        "year": args.year,
        "form_5500": {},
        "schedule_a": {},
    }

    identity_response = await query("query_plan")
    company_response = await query("query_company")
    identity_status = next(
        (item for item in identity_response.statuses if str(item.error_code or "") == "0"),
        None,
    )
    company_status = next(
        (item for item in company_response.statuses if str(item.error_code or "") == "0"),
        None,
    )
    identity_values = identity_status.query_results if identity_status else {}
    company_values = company_status.query_results if company_status else {}

    def first_value(*keys: str) -> str:
        for key in keys:
            value = str(identity_values.get(key) or "").strip()
            if value:
                return value
        return ""

    actual_plan_name = str((identity_status.plan_name if identity_status else None) or "").strip() or first_value(
        "PlanName", "PlanLine1"
    )
    actual_ein = ""
    for key in ("CompanyEmployerID", "CompanyEIN", "EmployerEIN", "EIN"):
        value = str(company_values.get(key) or "").strip()
        if value:
            actual_ein = value
            break
    actual_plan_number = first_value("PlanNumber", "PlanNum")

    def identity_key(value: object) -> str:
        return "".join(character for character in str(value or "").casefold() if character.isalnum())

    identity_matches = bool(
        identity_response.success
        and identity_status
        and company_response.success
        and company_status
        and identity_key(actual_plan_name) == identity_key(args.expected_plan_name)
        and identity_key(actual_ein) == identity_key(args.expected_ein)
        and identity_key(actual_plan_number) == identity_key(args.expected_plan_number)
    )
    report["identity"] = {
        **result(identity_response),
        "account_confirmed": True,
        "actual_plan_name": actual_plan_name,
        "returned_field_names": sorted(str(key) for key in identity_values),
        "company_query_success": company_response.success,
        "company_returned_field_names": sorted(str(key) for key in company_values),
        "plan_name_matches": identity_key(actual_plan_name) == identity_key(args.expected_plan_name),
        "ein_matches": identity_key(actual_ein) == identity_key(args.expected_ein),
        "plan_number_matches": identity_key(actual_plan_number) == identity_key(args.expected_plan_number),
        "year": args.year,
    }
    if not identity_matches:
        report["stopped_safely"] = "The queried FT Williams plan did not match the confirmed demo identity."
        print(json.dumps(report, sort_keys=True))
        return 2

    form_before_response = await query("query_5500")
    report["form_5500"]["baseline_query"] = result(form_before_response)
    form_before = form_before_response.statuses[0].query_results if form_before_response.success else {}
    records, schedule_baseline_summary = await _query_all_schedule_records(
        service,
        ftw_customer_id=args.ftw_customer_id,
        ftw_plan_id=args.ftw_plan_id,
        year=args.year,
    )
    report["schedule_a"]["baseline_query"] = schedule_baseline_summary
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(
            {
                "account": args.confirm_demo_account,
                "ftw_customer_id": args.ftw_customer_id,
                "ftw_plan_id": args.ftw_plan_id,
                "year": args.year,
                "form_5500": form_before,
                "schedule_a": records,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    report["baseline_saved"] = str(baseline_path)

    single_record_block = _single_record_block_reason(records)
    if single_record_block:
        report["schedule_a"]["blocked_before_send"] = single_record_block
        report["stopped_safely"] = single_record_block
        print(json.dumps(report, sort_keys=True))
        return 3

    form_original = str(form_before.get("TotPartcpBoyCnt") or "").strip()
    if form_original.isdigit():
        canary = str(int(form_original) + 1)
        field = ExtractedField(
            filing_id="demo-canary",
            source_field_name="participant canary",
            normalized_field_name="participant_canary",
            mapped_rule_key="form_5500_part_ii_11_total_participants_at_beginning_of_year",
            mapped_label="Participants",
            form_type=FormType.FORM_5500,
            priority=FieldPriority.HIGH,
            value=canary,
            proposed_value=canary,
            confidence=1,
        )
        sent = False
        try:
            update = await service.send_xml("update_5500_canary", build_single_document_update_xml(
                "DOL5500Data", [field], FormType.FORM_5500,
                transaction_type="1", ftw_customer_id=args.ftw_customer_id,
                ftw_plan_id=args.ftw_plan_id, year=args.year,
            ))
            sent = True
            report["form_5500"]["update"] = result(update)
            after = await query("query_5500")
            after_values = after.statuses[0].query_results if after.success else {}
            report["form_5500"]["readback"] = {
                **result(after),
                "canary_confirmed": after_values.get("TotPartcpBoyCnt") == canary,
                "other_fields_unchanged": (
                    {key: value for key, value in after_values.items() if key != "TotPartcpBoyCnt"}
                    == {key: value for key, value in form_before.items() if key != "TotPartcpBoyCnt"}
                ),
            }
        finally:
            if sent:
                restore_field = field.model_copy(update={"value": form_original, "proposed_value": form_original})
                restore = await service.send_xml("restore_5500_canary", build_single_document_update_xml(
                    "DOL5500Data", [restore_field], FormType.FORM_5500,
                    transaction_type="1", ftw_customer_id=args.ftw_customer_id,
                    ftw_plan_id=args.ftw_plan_id, year=args.year,
                ))
                restored = await query("query_5500")
                restored_values = restored.statuses[0].query_results if restored.success else {}
                report["form_5500"]["restore"] = {
                    **result(restore),
                    "query_success": restored.success,
                    "exact_baseline_restored": restored_values == form_before,
                }
    else:
        report["form_5500"]["skipped"] = "No numeric participant baseline was available."

    candidates = [
        ("WlfrTotChargesPaidAmt", "schedule_a_part_iii_10a_total_premiums_or_subscription_charges_paid_to_carrier"),
        ("WlfrTotEarnedPremAmt", "schedule_a_part_iii_9a_4_earned_1_2_3"),
        ("InsPrsnCoveredEoyCnt", "schedule_a_part_i_1e_persons_covered_end_of_policy_year"),
    ]
    selected = None
    for record in records:
        for tag, rule in candidates:
            raw = str(record["query_results"].get(tag) or "").replace(",", "").strip()
            try:
                selected = (record, tag, rule, raw, Decimal(raw), None)
                break
            except InvalidOperation:
                continue
        if selected:
            break
    if not selected:
        broker_candidates = [
            ("CommPdAmtXX", "CommPdAmt01", "schedule_a_part_i_3b_amount_of_commissions"),
            ("FeesPdAmtXX", "FeesPdAmt01", "schedule_a_part_i_3c_amount_of_fees"),
        ]
        for record in records:
            broker_rows = schedule_a_broker_multipart_rows(
                record.get("query_results") or {},
                query_subparts=record.get("query_subparts") or {},
            )
            if not broker_rows:
                continue
            for broker_tag, current_tag, rule in broker_candidates:
                raw = str(broker_rows[0].get(broker_tag) or "").replace(",", "").strip()
                try:
                    selected = (record, current_tag, rule, raw, Decimal(raw), broker_tag)
                    break
                except InvalidOperation:
                    continue
            if selected:
                break
    if selected:
        record, tag, rule, original, number, broker_tag = selected
        canary = format(number + Decimal("1"), "f")
        field = ExtractedField(
            filing_id="demo-canary",
            source_field_name="schedule canary",
            normalized_field_name="schedule_canary",
            mapped_rule_key=rule,
            mapped_label="Schedule A canary",
            form_type=FormType.SCHEDULE_A,
            priority=FieldPriority.HIGH,
            value=canary,
            proposed_value=canary,
            confidence=1,
        )
        sent = False
        try:
            update_xml = build_schedule_a_records_update_xml(
                records, record["ftw_seq_no"], [field],
                ftw_customer_id=args.ftw_customer_id, ftw_plan_id=args.ftw_plan_id, year=args.year,
            )
            preflight_gaps = schedule_a_replacement_data_gaps(
                records,
                update_xml,
                matched_ftw_seq_no=record["ftw_seq_no"],
            )
            update_root = ET.fromstring(update_xml)
            report["schedule_a"]["preflight"] = {
                "baseline_record_count": len(records),
                "replacement_record_count": len(update_root.findall(".//DOLScheduleAData")),
                "baseline_broker_row_count": sum(
                    len(schedule_a_broker_multipart_rows(
                        item.get("query_results") or {},
                        query_subparts=item.get("query_subparts") or {},
                    ))
                    for item in records
                ),
                "replacement_broker_row_count": len(update_root.findall(".//DOLSubPartData/Broker")),
                "preservation_gaps": preflight_gaps,
            }
            if preflight_gaps:
                raise ValueError("Schedule A replacement preflight failed: " + "; ".join(preflight_gaps))
            update = await service.send_xml("update_schedule_a_canary", update_xml)
            sent = update.sent
            report["schedule_a"]["update"] = result(update)
            after_records, after_summary = await _query_all_schedule_records(
                service,
                ftw_customer_id=args.ftw_customer_id,
                ftw_plan_id=args.ftw_plan_id,
                year=args.year,
            )
            selected_after = _matching_schedule_record(record, after_records)
            siblings_before = [item for item in records if item is not record]
            siblings_after = [item for item in after_records if item is not selected_after]
            report["schedule_a"]["readback"] = {
                **after_summary,
                "sequence": record["ftw_seq_no"],
                "canary_confirmed": bool(
                    selected_after
                    and (
                        str(schedule_a_broker_multipart_rows(
                            selected_after.get("query_results") or {},
                            query_subparts=selected_after.get("query_subparts") or {},
                        )[0].get(broker_tag) or "").replace(",", "").strip()
                        if broker_tag
                        else str((selected_after.get("query_results") or {}).get(tag) or "").replace(",", "").strip()
                    ) == canary
                ),
                "selected_other_fields_unchanged": bool(
                    selected_after
                    and _canonical_schedule_record(
                        selected_after,
                        ignored_tags={tag},
                        ignored_broker_tags={broker_tag} if broker_tag else set(),
                    )
                    == _canonical_schedule_record(
                        record,
                        ignored_tags={tag},
                        ignored_broker_tags={broker_tag} if broker_tag else set(),
                    )
                ),
                "selected_other_broker_values_unchanged": bool(
                    selected_after
                    and _canonical_schedule_record(
                        selected_after,
                        ignored_broker_tags={broker_tag} if broker_tag else set(),
                    )[1]
                    == _canonical_schedule_record(
                        record,
                        ignored_broker_tags={broker_tag} if broker_tag else set(),
                    )[1]
                ),
                "sibling_records_unchanged": _canonical_schedule_set(siblings_before) == _canonical_schedule_set(siblings_after),
            }
        except ValueError as exc:
            report["schedule_a"]["blocked_before_send"] = str(exc)
        finally:
            if sent:
                restore = await service.send_xml("restore_schedule_a_canary", build_schedule_a_records_update_xml(
                    records, record["ftw_seq_no"], [],
                    ftw_customer_id=args.ftw_customer_id, ftw_plan_id=args.ftw_plan_id, year=args.year,
                ))
                restored_records, restored_summary = await _query_all_schedule_records(
                    service,
                    ftw_customer_id=args.ftw_customer_id,
                    ftw_plan_id=args.ftw_plan_id,
                    year=args.year,
                )
                report["schedule_a"]["restore"] = {
                    **result(restore),
                    "query_success": restored_summary["success"],
                    "exact_baseline_restored": _canonical_schedule_set(restored_records) == _canonical_schedule_set(records),
                }
    else:
        report["schedule_a"]["skipped"] = "No numeric safe canary field was available."

    print(json.dumps(report, sort_keys=True))
    checks = []
    for form in ("form_5500", "schedule_a"):
        checks.extend([
            "skipped" not in report[form],
            bool(report[form].get("update", {}).get("success")),
            bool(report[form].get("readback", {}).get("canary_confirmed")),
            bool(report[form].get("restore", {}).get("exact_baseline_restored")),
        ])
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
