"""Narrow V1 interpretation confirmed in client feedback item 15."""
import re


def approved_vsp_commission_label(text: str) -> bool:
    # Native PDF runs can join adjacent cells ("FeesPaid", "PolicyAgent",
    # "YearExample"). Restore their case boundaries before applying the same
    # exact approved wording, carrier restriction and single-label check.
    upper = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', str(text or "")).upper()
    if not ("SCHEDULE A" in upper and ("VISION SERVICE PLAN" in upper or "VSP VISION CARE" in upper)):
        return False
    combined = list(re.finditer(r"COMMISSIONS?\s*/\s*FEES?", upper))
    approved = list(re.finditer(
        r"COMMISSIONS?\s*/\s*FEES?\s+PAID\s+FOR\s+POLICY\s+"
        r"(?:AGENT\s+OR\s+BROKER\s+)?OR\s+CONTRACT\s+YEAR\b", upper))
    # Another combined category on the same page must retain its own review.
    return len(combined) == len(approved) == 1
