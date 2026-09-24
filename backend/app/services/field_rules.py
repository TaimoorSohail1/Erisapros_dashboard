import json
from functools import lru_cache
from pathlib import Path

from app.models import FieldRule, FormType


RETIRED_FIELD_RULE_KEYS = frozenset(
    {
        "form_5500_part_i_2a_plan_administrator_name",
        "ftw_discovered_schedule_a_health_ind",
        "ftw_discovered_schedule_a_ins_fail_provide_info_text",
        "ftw_discovered_schedule_a_vision_ind",
    }
)
RETIRED_FIELD_NAMES = frozenset({"2a. Plan Administrator Name", "Plan Administrator Name"})


@lru_cache
def load_field_rules() -> list[FieldRule]:
    data_path = Path(__file__).resolve().parents[1] / "data" / "field_rules.json"
    raw_rules = json.loads(data_path.read_text(encoding="utf-8"))
    return [FieldRule(**rule) for rule in raw_rules]


DEFAULT_FIELD_RULES: list[FieldRule] = load_field_rules()


def normalize_name(value: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())


def is_retired_field(value: object) -> bool:
    rule_key = str(
        getattr(value, "mapped_rule_key", None)
        or getattr(value, "rule_key", None)
        or getattr(value, "key", None)
        or ""
    ).strip()
    if rule_key in RETIRED_FIELD_RULE_KEYS:
        return True
    names = (
        getattr(value, "field_name", None),
        getattr(value, "source_field_name", None),
        getattr(value, "mapped_label", None),
        getattr(value, "label", None),
    )
    retired_names = {normalize_name(name) for name in RETIRED_FIELD_NAMES}
    return any(normalize_name(str(name)) in retired_names for name in names if name)


def find_rule_for_field(field_name: str, rules: list[FieldRule] | None = None) -> FieldRule | None:
    rules = rules or DEFAULT_FIELD_RULES
    normalized = normalize_name(field_name)
    normalized_by_rule = []
    for item in rules:
        names = [item.label, item.key, item.ftw_field, item.xml_tag or "", *item.aliases]
        normalized_names = [normalize_name(name) for name in names]
        normalized_by_rule.append((item, normalized_names))
        if normalized in normalized_names:
            return item
    for item, normalized_names in normalized_by_rule:
        if any(normalized in name or name in normalized for name in normalized_names if name):
            return item
    return None


def form_type_for_rule(rule: FieldRule) -> FormType:
    text = f"{rule.source} {rule.form_section or ''}".lower()
    if "form 5500" in text:
        return FormType.FORM_5500
    return FormType.SCHEDULE_A


def rules_for_form_type(form_type: FormType | None, rules: list[FieldRule] | None = None) -> list[FieldRule]:
    available_rules = rules if rules is not None else DEFAULT_FIELD_RULES
    if not form_type:
        return available_rules
    return [rule for rule in available_rules if form_type_for_rule(rule) == form_type]
