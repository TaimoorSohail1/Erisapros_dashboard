import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class LocalFTWTarget:
    label: str
    url: str
    plan_name: str
    ein: str
    plan_number: str
    year: str

    @classmethod
    def from_dict(cls, value: dict) -> "LocalFTWTarget":
        target = cls(
            label=str(value.get("label") or "").strip(),
            url=str(value.get("url") or "").strip(),
            plan_name=str(value.get("plan_name") or "").strip(),
            ein=str(value.get("ein") or "").strip(),
            plan_number=str(value.get("plan_number") or "").strip(),
            year=str(value.get("year") or "").strip(),
        )
        target.validate()
        return target

    def validate(self) -> None:
        if not all([self.label, self.url, self.plan_name, self.ein, self.plan_number, self.year]):
            raise ValueError("Each local FT Williams target requires a label, URL, plan name, EIN, plan number, and year.")
        try:
            parsed = urlsplit(self.url)
        except ValueError as exc:
            raise ValueError(f"{self.label}: invalid FT Williams URL.") from exc
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or not (host == "ftwilliam.com" or host.endswith(".ftwilliam.com"))
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise ValueError(f"{self.label}: target must be a safe ftwilliam.com HTTPS URL.")
        decoded = unquote(self.url).casefold()
        if self.year.casefold() not in decoded:
            raise ValueError(f"{self.label}: target URL does not contain the expected year.")


@dataclass(frozen=True)
class LocalFTWVerification:
    label: str
    success: bool
    state: str
    message: str


def load_local_ftw_targets(path: str | Path) -> list[LocalFTWTarget]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("The local FT Williams target file is missing or invalid.") from exc
    values = payload.get("targets") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or not values:
        raise ValueError("The local FT Williams target file must contain a non-empty targets list.")
    targets = [LocalFTWTarget.from_dict(value) for value in values if isinstance(value, dict)]
    if len(targets) != len(values):
        raise ValueError("Every local FT Williams target must be a JSON object.")
    labels = [target.label.casefold() for target in targets]
    if len(labels) != len(set(labels)):
        raise ValueError("Local FT Williams target labels must be unique.")
    return targets


def verify_local_ftw_identity(
    target: LocalFTWTarget,
    page_text: str,
    *,
    expected_account: str,
    password_visible: bool,
) -> LocalFTWVerification:
    if password_visible:
        return LocalFTWVerification(target.label, False, "LOGIN_REQUIRED", "FT Williams requires login or MFA.")

    normalized_page = _normalize(page_text)
    if _normalize(expected_account) not in normalized_page:
        return LocalFTWVerification(target.label, False, "WRONG_ACCOUNT", "The expected FT Williams account is not visible.")
    if _normalize(target.plan_name) not in normalized_page:
        return LocalFTWVerification(target.label, False, "INVALID_TARGET", "The FT Williams page does not match the expected plan name.")

    expected_ein = re.sub(r"\D", "", target.ein)
    ein_match = re.search(r"\bEIN\s*:\s*([0-9-]+)", page_text or "", re.IGNORECASE)
    if not ein_match or re.sub(r"\D", "", ein_match.group(1)) != expected_ein:
        return LocalFTWVerification(target.label, False, "INVALID_TARGET", "The FT Williams page does not match the expected EIN.")

    expected_plan_number = target.plan_number.zfill(3)
    plan_number_match = re.search(r"\bPN\s*:\s*(\d{1,3})", page_text or "", re.IGNORECASE)
    if not plan_number_match or plan_number_match.group(1).zfill(3) != expected_plan_number:
        return LocalFTWVerification(target.label, False, "INVALID_TARGET", "The FT Williams page does not match the expected plan number.")

    if not re.search(rf"\b5500\s*-\s*{re.escape(target.year)}\b", page_text or "", re.IGNORECASE):
        return LocalFTWVerification(target.label, False, "INVALID_TARGET", "The FT Williams page does not match the expected year.")

    return LocalFTWVerification(target.label, True, "VERIFIED", "Account and plan identity verified without changing FT Williams data.")


def _normalize(value: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        re.sub(r"[,.;:]", " ", str(value or "").replace("&", " and ").casefold()),
    ).strip()
