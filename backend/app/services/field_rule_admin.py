from datetime import datetime
from hashlib import sha256
from dataclasses import dataclass
import re

from app.models import FieldRule, FieldRuleApplicability, FieldRuleMappingMode, FieldRuleStatus
from app.repositories import Repository
from app.services.field_rules import DEFAULT_FIELD_RULES, normalize_name
from app.services.ftw_field_catalog import RETIRED_FIELD_RULE_KEYS, field_catalog_entry


class FieldRuleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PublishedRuleSnapshot:
    version: str
    rules: list[FieldRule]


class FieldRuleService:
    """Versioned administration boundary for the canonical mapping rules."""

    def __init__(self, repository: Repository):
        self.repository = repository

    @staticmethod
    def approved_update_tag(rule_key: str) -> str | None:
        entry = field_catalog_entry(rule_key)
        return entry.update_tag if entry else None

    @classmethod
    def apply_catalog_capability(cls, rule: FieldRule) -> FieldRule:
        """Make the catalog, rather than stale saved behavior, authoritative."""
        if rule.mapping_mode == FieldRuleMappingMode.EXTRACTION_ONLY:
            return rule
        if cls.approved_update_tag(rule.key):
            return rule
        return rule.model_copy(
            deep=True,
            update={"existing_behavior": "Review Only", "new_behavior": "Keep FTW"},
        )

    async def ensure_seeded(self) -> None:
        existing = await self.repository.list_field_rule_versions()
        existing_keys = {rule.key for rule in existing}
        now = datetime.utcnow()
        for rule in DEFAULT_FIELD_RULES:
            if rule.key in existing_keys:
                continue
            applicability = infer_applicability(rule)
            seeded = rule.model_copy(
                deep=True,
                update={
                    "status": FieldRuleStatus.PUBLISHED,
                    "applicability": applicability,
                    "version": 1,
                    "updated_by": "system:migration",
                    "change_reason": "Imported from the canonical field-rule inventory.",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await self.repository.save_field_rule_version(seeded)

        # Retire obsolete discovered rules with a new disabled version. This is
        # idempotent and intentionally retains the complete version history.
        versions = await self.repository.list_field_rule_versions()
        for key in RETIRED_FIELD_RULE_KEYS:
            history = [rule for rule in versions if rule.key == key]
            if not history:
                continue
            latest = max(history, key=lambda item: item.version)
            if latest.status == FieldRuleStatus.DISABLED:
                continue
            retired = latest.model_copy(
                deep=True,
                update={
                    "id": None,
                    "status": FieldRuleStatus.DISABLED,
                    "version": latest.version + 1,
                    "updated_by": "system:migration",
                    "change_reason": "Retired from the active Field Rules inventory.",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            await self.repository.save_field_rule_version(retired)

    async def list_rules(self) -> list[FieldRule]:
        await self.ensure_seeded()
        versions = await self.repository.list_field_rule_versions()
        current: list[FieldRule] = []
        for key in sorted({rule.key for rule in versions} - RETIRED_FIELD_RULE_KEYS):
            key_versions = [rule for rule in versions if rule.key == key]
            published = next((rule for rule in key_versions if rule.status == FieldRuleStatus.PUBLISHED), None)
            draft = next((rule for rule in key_versions if rule.status == FieldRuleStatus.DRAFT), None)
            disabled = next((rule for rule in key_versions if rule.status == FieldRuleStatus.DISABLED), None)
            if disabled and (not published or disabled.version >= published.version):
                current.append(self.apply_catalog_capability(disabled))
                continue
            if published:
                current.append(self.apply_catalog_capability(published))
            if draft and (not published or draft.version > published.version):
                current.append(self.apply_catalog_capability(draft))
        return sorted(current, key=lambda item: (item.order, item.form_section or item.source, item.label))

    async def published_rules(self) -> list[FieldRule]:
        await self.ensure_seeded()
        versions = await self.repository.list_field_rule_versions()
        published: list[FieldRule] = []
        for key in sorted({rule.key for rule in versions} - RETIRED_FIELD_RULE_KEYS):
            candidates = [rule for rule in versions if rule.key == key and rule.status == FieldRuleStatus.PUBLISHED]
            disabled = [rule for rule in versions if rule.key == key and rule.status == FieldRuleStatus.DISABLED]
            latest = max(candidates, key=lambda item: item.version, default=None)
            latest_disabled = max(disabled, key=lambda item: item.version, default=None)
            if latest and (not latest_disabled or latest.version > latest_disabled.version):
                published.append(self.apply_catalog_capability(latest))
        return sorted(published, key=lambda item: (item.order, item.form_section or item.source, item.label))

    async def published_snapshot(self) -> PublishedRuleSnapshot:
        rules = await self.published_rules()
        signature = "|".join(f"{rule.key}:{rule.version}" for rule in sorted(rules, key=lambda item: item.key))
        return PublishedRuleSnapshot(version=sha256(signature.encode("utf-8")).hexdigest()[:12], rules=rules)

    async def create_draft(self, rule: FieldRule, *, actor: str, reason: str) -> FieldRule:
        require_change_reason(reason)
        await self.ensure_seeded()
        await self.validate(rule)
        history = await self.repository.list_field_rule_versions(rule.key)
        next_version = max((item.version for item in history), default=0) + 1
        now = datetime.utcnow()
        draft = rule.model_copy(
            deep=True,
            update={
                "id": None,
                "status": FieldRuleStatus.DRAFT,
                "version": next_version,
                "updated_by": actor,
                "change_reason": reason,
                "created_at": now,
                "updated_at": now,
            },
        )
        return await self.repository.save_field_rule_version(draft)

    async def publish(self, key: str, *, actor: str, reason: str) -> FieldRule:
        require_change_reason(reason)
        history = await self.repository.list_field_rule_versions(key)
        draft = max(
            (item for item in history if item.status == FieldRuleStatus.DRAFT),
            key=lambda item: item.version,
            default=None,
        )
        if not draft:
            raise FieldRuleValidationError("No draft is available to publish.")
        await self.validate(draft, ignore_record_id=draft.id)
        now = datetime.utcnow()
        published = draft.model_copy(
            deep=True,
            update={
                "id": None,
                "status": FieldRuleStatus.PUBLISHED,
                "updated_by": actor,
                "change_reason": reason,
                "created_at": now,
                "updated_at": now,
            },
        )
        return await self.repository.save_field_rule_version(published)

    async def disable(self, key: str, *, actor: str, reason: str) -> FieldRule:
        require_change_reason(reason)
        published = next((rule for rule in await self.published_rules() if rule.key == key), None)
        if not published:
            raise FieldRuleValidationError("Published field rule not found.")
        now = datetime.utcnow()
        disabled = published.model_copy(
            deep=True,
            update={
                "id": None,
                "status": FieldRuleStatus.DISABLED,
                "version": published.version + 1,
                "updated_by": actor,
                "change_reason": reason,
                "created_at": now,
                "updated_at": now,
            },
        )
        return await self.repository.save_field_rule_version(disabled)

    async def rollback(self, key: str, version: int, *, actor: str, reason: str) -> FieldRule:
        require_change_reason(reason)
        history = await self.repository.list_field_rule_versions(key)
        source = next(
            (item for item in history if item.version == version and item.status == FieldRuleStatus.PUBLISHED),
            None,
        )
        if not source:
            raise FieldRuleValidationError("The selected published version was not found.")
        next_version = max((item.version for item in history), default=0) + 1
        now = datetime.utcnow()
        restored = source.model_copy(
            deep=True,
            update={
                "id": None,
                "status": FieldRuleStatus.PUBLISHED,
                "version": next_version,
                "updated_by": actor,
                "change_reason": reason,
                "created_at": now,
                "updated_at": now,
            },
        )
        return await self.repository.save_field_rule_version(restored)

    async def history(self, key: str) -> list[FieldRule]:
        await self.ensure_seeded()
        records = await self.repository.list_field_rule_versions(key)
        return sorted(records, key=lambda item: (item.version, item.created_at), reverse=True)

    async def validate(self, rule: FieldRule, *, ignore_record_id: str | None = None) -> list[str]:
        errors: list[str] = []
        if rule.key in RETIRED_FIELD_RULE_KEYS:
            errors.append("This field has been retired from the active Field Rules inventory.")
        if not re.fullmatch(r"[a-z0-9_]+", rule.key.strip()):
            errors.append("Stable rule key must use lowercase letters, numbers, and underscores only.")
        if not rule.label.strip():
            errors.append("Official field label is required.")
        extraction_only = rule.mapping_mode == FieldRuleMappingMode.EXTRACTION_ONLY
        if not extraction_only and not rule.ftw_field.strip():
            errors.append("FT Williams field is required.")
        approved_rule = next((item for item in DEFAULT_FIELD_RULES if item.key == rule.key), None)
        catalog_entry = field_catalog_entry(rule.key)
        if extraction_only:
            if rule.source == "Form 5500" or str(rule.form_section or "").startswith("Form 5500"):
                errors.append(
                    "Plan Worksheet uses the protected field catalog; custom extraction-only fields "
                    "are supported for Schedule A documents only."
                )
            if rule.xml_tag:
                errors.append("Extraction-only fields cannot have an FT Williams XML tag.")
            requests_update = str(rule.existing_behavior or "").strip().lower() in {"update", "add"}
            requests_add = str(rule.new_behavior or "").strip().lower() in {"add", "update"}
            if requests_update or requests_add:
                errors.append("Extraction-only fields must remain review-only and cannot update FT Williams.")
        elif not catalog_entry:
            errors.append(
                "Select an approved FT Williams field or discovered comparison field before saving this rule."
            )
        elif catalog_entry.catalog_tier == "DISCOVERED":
            protected_values = {
                "FT Williams field": (rule.ftw_field, catalog_entry.label),
                "current XML mapping": (rule.xml_tag or "", catalog_entry.current_tag or ""),
                "source": (rule.source, "Schedule A" if catalog_entry.form_type.value == "SCHEDULE_A" else "Form 5500"),
                "form section": (rule.form_section or "", catalog_entry.form_section or ""),
            }
            changed_protected = [
                name
                for name, (actual, expected) in protected_values.items()
                if str(actual or "").strip() != str(expected or "").strip()
            ]
            if changed_protected:
                errors.append(
                    "Discovered FT Williams technical mappings cannot be changed manually "
                    f"({', '.join(changed_protected)})."
                )
            if catalog_entry.form_type.value == "FORM_5500":
                expected_aliases = {normalize_name(catalog_entry.label)}
                actual_aliases = {normalize_name(alias) for alias in rule.aliases if alias.strip()}
                if rule.label.strip() != catalog_entry.label or actual_aliases != expected_aliases:
                    errors.append(
                        "Plan Worksheet labels are fixed by the FT Williams catalog; "
                        "custom names and aliases are supported for Schedule A fields only."
                    )
            requests_update = str(rule.existing_behavior or "").strip().lower() in {"update", "add"}
            requests_add = str(rule.new_behavior or "").strip().lower() in {"add", "update"}
            if requests_update or requests_add:
                errors.append(
                    "This discovered field is available for FT Williams comparison only until its update contract is verified."
                )
        else:
            protected_values = {
                "official label": (rule.label, approved_rule.label),
                "FT Williams field": (rule.ftw_field, approved_rule.ftw_field),
                "XML mapping": (rule.xml_tag or "", approved_rule.xml_tag or ""),
                "source": (rule.source, approved_rule.source),
                "form section": (rule.form_section or "", approved_rule.form_section or ""),
            }
            changed_protected = [
                name
                for name, (actual, expected) in protected_values.items()
                if str(actual or "").strip() != str(expected or "").strip()
            ]
            if changed_protected:
                errors.append(
                    "Approved FT Williams technical mappings cannot be changed manually "
                    f"({', '.join(changed_protected)})."
                )
            if rule.key.startswith("form_5500_"):
                expected_aliases = {
                    normalize_name(alias) for alias in approved_rule.aliases if alias.strip()
                }
                actual_aliases = {normalize_name(alias) for alias in rule.aliases if alias.strip()}
                if actual_aliases != expected_aliases:
                    errors.append(
                        "Plan Worksheet labels are fixed by the protected field catalog; "
                        "add aliases only to Schedule A fields."
                    )
            update_supported = bool(catalog_entry and catalog_entry.update_supported)
            requests_update = str(rule.existing_behavior or "").strip().lower() == "update"
            requests_add = str(rule.new_behavior or "").strip().lower() in {"add", "update"}
            if (requests_update or requests_add) and not update_supported:
                errors.append(
                    "This approved field is read-only in FT Williams and cannot be configured for updates."
                )
        normalized_aliases = [normalize_name(alias) for alias in rule.aliases if alias.strip()]
        if len(normalized_aliases) != len(set(normalized_aliases)):
            errors.append("Aliases must be unique within the rule.")
        versions = await self.repository.list_field_rule_versions()
        same_key_published = max(
            (item for item in versions if item.key == rule.key and item.status == FieldRuleStatus.PUBLISHED),
            key=lambda item: item.version,
            default=None,
        )
        existing_same_key_aliases = {
            normalize_name(alias) for alias in (same_key_published.aliases if same_key_published else [])
        }
        aliases_to_check = set(normalized_aliases) - existing_same_key_aliases
        for existing in versions:
            if existing.id == ignore_record_id or existing.key == rule.key:
                continue
            if existing.status not in {FieldRuleStatus.PUBLISHED, FieldRuleStatus.DRAFT}:
                continue
            existing_names = {normalize_name(existing.label), *(normalize_name(alias) for alias in existing.aliases)}
            conflicts = existing_names.intersection(aliases_to_check)
            if conflicts:
                errors.append(f"Alias conflicts with {existing.label}: {sorted(conflicts)[0]}.")
                break
        if errors:
            raise FieldRuleValidationError(" ".join(errors))
        return errors


def infer_applicability(rule: FieldRule) -> FieldRuleApplicability:
    if rule.key.startswith("form_5500_"):
        return FieldRuleApplicability.FORM_5500
    if rule.key.startswith("schedule_a_part_iii_9"):
        return FieldRuleApplicability.EXPERIENCE
    if rule.key.startswith("schedule_a_part_iii_10"):
        return FieldRuleApplicability.NONEXPERIENCE
    return rule.applicability


def require_change_reason(reason: str) -> None:
    if not str(reason or "").strip():
        raise FieldRuleValidationError("A change reason is required for this field-rule action.")
