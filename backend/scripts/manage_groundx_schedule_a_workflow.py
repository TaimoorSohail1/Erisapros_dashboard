"""Generate, validate, and explicitly provision the Schedule A workflow.

The default command is read-only.  Live validation also changes no GroundX
state.  Creating a workflow and attaching it to a bucket require separate,
explicit flags so application startup can never mutate production workflow
assignments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from groundx import GroundX  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import FormType  # noqa: E402
from app.repositories import get_repository  # noqa: E402
from app.services.field_rule_admin import FieldRuleService  # noqa: E402
from app.services.field_rules import DEFAULT_FIELD_RULES, form_type_for_rule  # noqa: E402
from app.services.groundx_schedule_a_workflow import (  # noqa: E402
    schedule_a_schema_version,
    schedule_a_workflow_yaml,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path, help="Write generated authored YAML to this path.")
    parser.add_argument("--validate-live", action="store_true", help="Validate with GroundX without creating anything.")
    parser.add_argument("--create", action="store_true", help="Create an unassigned GroundX workflow.")
    parser.add_argument("--workflow-id", help="Existing workflow ID to attach instead of creating one.")
    parser.add_argument("--attach-bucket", type=int, help="Bucket ID to attach after create or with --workflow-id.")
    parser.add_argument(
        "--confirm-attach",
        action="store_true",
        help="Required with --attach-bucket; confirms the external bucket mutation.",
    )
    parser.add_argument("--name", default="ERISAPros Schedule A structured extraction")
    parser.add_argument(
        "--rule-source",
        choices=("defaults", "published"),
        default="defaults",
        help="Use repository-published rules when a database connection is available.",
    )
    return parser.parse_args()


async def load_published_rules():
    return (await FieldRuleService(get_repository()).published_snapshot()).rules


def load_rules(source: str):
    if source == "published":
        return asyncio.run(load_published_rules())
    return list(DEFAULT_FIELD_RULES)


def main() -> int:
    args = parse_args()
    if args.attach_bucket is not None and not args.confirm_attach:
        raise SystemExit("--attach-bucket requires --confirm-attach")
    if args.attach_bucket is not None and not (args.create or args.workflow_id):
        raise SystemExit("--attach-bucket requires --create or --workflow-id")
    if args.create and args.workflow_id:
        raise SystemExit("Use either --create or --workflow-id, not both")

    rules = load_rules(args.rule_source)
    yaml_text = schedule_a_workflow_yaml(rules)
    summary: dict[str, object] = {
        "schema_version": schedule_a_schema_version(rules),
        "rule_count": sum(form_type_for_rule(rule) == FormType.SCHEDULE_A for rule in rules),
        "rule_source": args.rule_source,
        "validated": False,
        "created": False,
        "attached_bucket": None,
    }
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(yaml_text, encoding="utf-8")
        summary["yaml_path"] = str(args.write.resolve())

    if args.validate_live or args.create or args.workflow_id:
        settings = get_settings()
        if not settings.groundx_api_key:
            raise SystemExit("GROUNDX_API_KEY is required")
        client = GroundX(api_key=settings.groundx_api_key)
        if args.validate_live or args.create:
            client.workflows.validate(name=args.name, yaml=yaml_text)
            summary["validated"] = True

        workflow_id = args.workflow_id
        if args.create:
            response = client.create_extraction_workflow(yaml_text=yaml_text, name=args.name)
            workflow_id = response.workflow.workflow_id
            if not workflow_id:
                raise RuntimeError("GroundX did not return a workflow ID")
            summary["created"] = True
            summary["workflow_id"] = workflow_id

        if args.attach_bucket is not None:
            client.workflows.add_to_id(id=args.attach_bucket, workflow_id=str(workflow_id))
            summary["attached_bucket"] = args.attach_bucket

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
