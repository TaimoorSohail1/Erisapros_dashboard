from app.models import FormType
from app.services.field_rules import DEFAULT_FIELD_RULES
from app.services.ftw_field_catalog import (
    SUPPORTED_FTW_YEARS,
    field_catalog,
    field_catalog_entry,
    field_catalog_version,
)


def test_catalog_covers_every_supported_field_rule_once() -> None:
    catalog = field_catalog()

    verified = [entry for entry in catalog if entry.catalog_tier == "VERIFIED"]

    assert len(verified) == len(DEFAULT_FIELD_RULES) == 62
    assert {entry.key for entry in verified} == {rule.key for rule in DEFAULT_FIELD_RULES}
    assert len({entry.key for entry in catalog}) == len(catalog)
    assert all(tuple(entry.supported_years) == SUPPORTED_FTW_YEARS for entry in verified)
    assert all(entry.form_type in {FormType.SCHEDULE_A, FormType.FORM_5500} for entry in catalog)
    assert all(entry.value_type and entry.format_hint for entry in catalog)


def test_catalog_records_update_support_and_read_only_reason() -> None:
    catalog = field_catalog()

    assert sum(entry.update_supported for entry in catalog if entry.catalog_tier == "VERIFIED") == 61
    assert sum(not entry.update_supported for entry in catalog) > 5
    assert all(entry.update_tag for entry in catalog if entry.update_supported)
    assert all(entry.read_only_reason for entry in catalog if not entry.update_supported)


def test_catalog_excludes_retired_discovered_schedule_a_fields() -> None:
    retired_keys = {
        "ftw_discovered_schedule_a_health_ind",
        "ftw_discovered_schedule_a_ins_fail_provide_info_text",
        "ftw_discovered_schedule_a_vision_ind",
    }

    assert all(field_catalog_entry(key) is None for key in retired_keys)
    assert retired_keys.isdisjoint({entry.key for entry in field_catalog()})


def test_catalog_preserves_every_observed_current_tag_without_promoting_write_access() -> None:
    catalog = field_catalog()
    discovered = [entry for entry in catalog if entry.catalog_tier == "DISCOVERED"]

    assert len(catalog) == 353
    assert len(discovered) == 291
    assert len({(entry.form_type, entry.current_tag) for entry in catalog if entry.current_tag}) == 351
    assert all(not entry.update_supported and entry.update_tag is None for entry in discovered)


def test_catalog_exposes_ftw_format_for_naic_code() -> None:
    entry = field_catalog_entry("schedule_a_part_i_1c_naic_code")

    assert entry is not None
    assert entry.form_type == FormType.SCHEDULE_A
    assert entry.current_tag == "InsCarrierNAICCode"
    assert entry.update_tag == "InsCarrierNAICCode"
    assert entry.value_type == "NAIC_CODE"
    assert entry.format_hint == "Exactly 5 digits"
    assert entry.update_supported is True


def test_catalog_version_is_stable_and_contract_scoped() -> None:
    first = field_catalog_version()
    second = field_catalog_version()

    assert first == second
    assert first.startswith("2026-08-")
    assert len(first) > len("2026-08-")
