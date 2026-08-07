from datetime import datetime
from hashlib import sha256
from dataclasses import dataclass
import re

from app.models import FieldRule, FieldRuleApplicability, FieldRuleStatus
from app.repositories import Repository
from app.services.field_rules import DEFAULT_FIELD_RULES, normalize_name


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

    async def list_rules(self) -> list[FieldRule]:
        await self.ensure_seeded()
        versions = await self.repository.list_field_rule_versions()
        current: list[FieldRule] = []
        for key in sorted({rule.key for rule in versions}):
            key_versions = [rule for rule in versions if rule.key == key]
            published = next((rule for rule in key_versions if rule.status == FieldRuleStatus.PUBLISHED), None)
            draft = next((rule for rule in key_versions if rule.status == FieldRuleStatus.DRAFT), None)
            disabled = next((rule for rule in key_versions if rule.status == FieldRuleStatus.DISABLED), None)
            if disabled and (not published or disabled.version >= published.version):
                current.append(disabled)
                continue
            if published:
                current.append(published)
            if draft and (not published or draft.version > published.version):
                current.append(draft)
        return sorted(current, key=lambda item: (item.order, item.form_section or item.source, item.label))

    async def published_rules(self) -> list[FieldRule]:
        await self.ensure_seeded()
        versions = await self.repository.list_field_rule_versions()
        published: list[FieldRule] = []
        for key in sorted({rule.key for rule in versions}):
            candidates = [rule for rule in versions if rule.key == key and rule.status == FieldRuleStatus.PUBLISHED]
            disabled = [rule for rule in versions if rule.key == key and rule.status == FieldRuleStatus.DISABLED]
            latest = max(candidates, key=lambda item: item.version, default=None)
            latest_disabled = max(disabled, key=lambda item: item.version, default=None)
            if latest and (not latest_disabled or latest.version > latest_disabled.version):
                published.append(latest)
        return sorted(published, key=lambda item: (item.order, item.form_section or item.source, item.label))

    async def published_snapshot(self) -> PublishedRuleSnapshot:
        rules = await self.published_rules()
        signature = "|".join(f"{rule.key}:{rule.version}" for rule in sorted(rules, key=lambda item: item.key))
        return PublishedRuleSnapshot(version=sha256(signature.encode("utf-8")).hexdigest()[:12], rules=rules)

    async def create_draft(self, rule: FieldRule, *, actor: str, reason: str) -> FieldRule:
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
        if not re.fullmatch(r"[a-z0-9_]+", rule.key.strip()):
            errors.append("Stable rule key must use lowercase letters, numbers, and underscores only.")
        if not rule.label.strip():
            errors.append("Official field label is required.")
        if not rule.ftw_field.strip():
            errors.append("FT Williams field is required.")
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
