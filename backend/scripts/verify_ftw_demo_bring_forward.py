"""Run one guarded Bring Forward canary against the HighlandTech demo account.

The script refuses to click unless the exact account, plan, EIN, plan number,
and year match and the target has no current-year Schedule A records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.models import FTWilliamsQueryRequest
from app.services.ftwilliams import FTWilliamsService
from app.services.ftwilliams_local_agent_runtime import PersistentFTWBrowser
from scripts.verify_ftw_demo_update_restore import _query_all_schedule_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-demo-account", required=True)
    parser.add_argument("--ftw-customer-id", required=True)
    parser.add_argument("--ftw-plan-id", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--expected-plan-name", required=True)
    parser.add_argument("--expected-ein", required=True)
    parser.add_argument("--expected-plan-number", required=True)
    parser.add_argument("--profile-dir", required=True)
    return parser.parse_args()


def identity_key(value: object) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


async def main() -> int:
    args = parse_args()
    if args.confirm_demo_account.strip().casefold() != "highlandtech":
        raise SystemExit("Refusing to run without --confirm-demo-account HighlandTech")
    if not (args.year.isdigit() and len(args.year) == 4):
        raise SystemExit("A four-digit demo plan year is required.")

    service = FTWilliamsService()

    async def query(operation: str):
        return await service.run_query(FTWilliamsQueryRequest(
            operation=operation,
            ftw_customer_id=args.ftw_customer_id,
            ftw_plan_id=args.ftw_plan_id,
            year=args.year,
            send=True,
        ))

    report: dict = {
        "test": "highland_demo_bring_forward",
        "year": args.year,
        "mutation_attempted": False,
    }
    plan_response, company_response = await asyncio.gather(query("query_plan"), query("query_company"))
    plan_status = next((item for item in plan_response.statuses if str(item.error_code or "") == "0"), None)
    company_status = next((item for item in company_response.statuses if str(item.error_code or "") == "0"), None)
    plan_values = plan_status.query_results if plan_status else {}
    company_values = company_status.query_results if company_status else {}
    plan_name = str((plan_status.plan_name if plan_status else None) or plan_values.get("PlanLine1") or "").strip()
    ein = str(company_values.get("CompanyEmployerID") or "").strip()
    plan_number = str(plan_values.get("PlanNumber") or "").strip()
    identity_matches = bool(
        plan_response.success
        and company_response.success
        and plan_status
        and company_status
        and identity_key(plan_name) == identity_key(args.expected_plan_name)
        and identity_key(ein) == identity_key(args.expected_ein)
        and identity_key(plan_number) == identity_key(args.expected_plan_number)
    )
    report["identity"] = {
        "matches": identity_matches,
        "plan_name_matches": identity_key(plan_name) == identity_key(args.expected_plan_name),
        "ein_matches": identity_key(ein) == identity_key(args.expected_ein),
        "plan_number_matches": identity_key(plan_number) == identity_key(args.expected_plan_number),
    }
    if not identity_matches:
        report["stopped_safely"] = "Exact demo plan identity did not match."
        print(json.dumps(report, sort_keys=True))
        return 2

    before_records, before_summary = await _query_all_schedule_records(
        service,
        ftw_customer_id=args.ftw_customer_id,
        ftw_plan_id=args.ftw_plan_id,
        year=args.year,
    )
    before_sequences = sorted(str(record.get("ftw_seq_no") or "") for record in before_records)
    report["before"] = {**before_summary, "sequence_ids": before_sequences}
    if before_sequences:
        report["stopped_safely"] = "Current-year Schedule A already exists; no click was attempted."
        print(json.dumps(report, sort_keys=True))
        return 2

    target_url = (
        "https://www.ftwilliam.com/cgi-bin/index.cgi?"
        "#go=iframe&page=/cgi-bin/PlanDoc2.cgi&PerformDoc5500=1&"
        f"plan={args.ftw_customer_id},{args.ftw_plan_id}&Year={args.year}"
    )
    browser = PersistentFTWBrowser(
        args.profile_dir,
        expected_account="HighlandTech",
        timeout_ms=45_000,
    )
    try:
        action = await browser.execute({
            "id": "demo-bring-forward-canary",
            "filing_id": "demo-bring-forward-canary",
            "expected_account": "HighlandTech",
            "target_url": target_url,
            "expected_plan_name": args.expected_plan_name,
            "expected_ein": args.expected_ein,
            "expected_plan_number": args.expected_plan_number,
            "expected_year": args.year,
        })
    finally:
        await browser.close()
    report["mutation_attempted"] = action.state == "SUBMITTED"
    report["action"] = {"state": action.state, "message": action.message}
    if action.state != "SUBMITTED":
        print(json.dumps(report, sort_keys=True))
        return 1

    after_sequences: list[str] = []
    after_success = False
    for _ in range(12):
        await asyncio.sleep(5)
        after_records, after_summary = await _query_all_schedule_records(
            service,
            ftw_customer_id=args.ftw_customer_id,
            ftw_plan_id=args.ftw_plan_id,
            year=args.year,
        )
        after_success = after_summary["success"]
        after_sequences = sorted(str(record.get("ftw_seq_no") or "") for record in after_records)
        if after_sequences:
            break
    new_sequences = sorted(set(after_sequences) - set(before_sequences))
    report["after"] = {
        "success": after_success,
        "sequence_ids": after_sequences,
        "new_sequence_ids": new_sequences,
    }
    report["verified"] = bool(new_sequences)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
