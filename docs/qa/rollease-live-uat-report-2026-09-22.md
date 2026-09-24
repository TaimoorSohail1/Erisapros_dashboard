# Rollease Live UAT Report

Date: 2026-09-22
Client scope: Rollease Inc (Test)
Source file: `2. RollEase Acmeda, Inc. - SGM0609370 - GBA APIR - 1.1.25-12.31.25.pdf`

## Executive result

The live Rollease intake path worked end to end through upload, dashboard creation, and extraction. The filing is visible in the review workspace with 38 of 40 fields found and 5 review decisions required. FT Williams matching is not complete because the live target reports that the current-year Schedule A is missing; no FT Williams write was performed.

## Evidence captured

### 1. ShareFile download and upload

- Rollease folder: `Rollease Inc (Test) → 5500 Filing → 2025 5500 Filing → Schedule A's`.
- Existing file opened: `2. RollEase Acmeda, Inc. - SGM0609370 - GBA APIR - 1.1.25-12.31.25.pdf`.
- The file was uploaded back to the same folder as a new version after ShareFile displayed its duplicate-upload confirmation.
- ShareFile showed the new version at 4:24 PM on 22 September 2026 and 100% upload completion.

**Result: Pass.**

### 2. Dashboard intake

After running **Sync ShareFile**, the dashboard changed from 31 to 32 tracked filings and from 9 to 10 companies. A new Rollease group appeared:

- Client: `Rollease Inc (Test)`
- Filing: `RollEase Acmeda, Inc. SGM0609370 GBA APIR 1.1.25 12.31.25.pdf`
- Status during processing: `PROCESSING`
- Dashboard filing ID: `6ab2657af9dfc231af393c20`

**Result: Pass.**

### 3. Extraction and review

The filing completed automated extraction and opened in the review workspace:

- Fields found: `38 / 40`
- Needs review: `5`
- Extracted premium value: `12,013.49`
- Review columns visible: Extracted, Current FTW, Proposed to Send, Status
- Workflow progress: Intake Complete, Extraction Complete, FTW loaded Pending, Review Needs review, FTW update Needs review
- Local FT Williams agent indicator: `Connected`

The five remaining decisions were visibly explained as missing persons covered and low-confidence/derived nonexperience-rated values. No silent failure occurred.

**Result: Pass with review exceptions.**

### 4. FT Williams agent

The live agent page showed:

- Connection status: `Connected and ready`
- Connected computers: `2`
- Compatible device: `DESKTOP-D9JV7IA`, Agent `0.4.1`, Connected
- Older device: `EP-PF43NS4S`, Agent `0.3.2`
- Verified plan mappings: `None`

**Result: Connectivity Pass; routing setup Blocked.**

### 5. FT Williams matching

The Rollease filing showed `FTW MATCH Pending` and the blocking explanation:

> Current-year Schedule A is missing. Selected changes require a valid, editable FT Williams target.

The filing offered a Retry control, but no retry or live FT Williams update was executed because the target plan/mapping was not verified.

**Result: Blocked.**

### 6. Live write/send

The `Send to FT Williams` control was visible, but no send or update was performed. This remains pending approval of the exact target plan, field/value, rollback value, and read-back procedure.

**Result: Pending.**

## Test summary

| Area | Result | Notes |
|---|---|---|
| Download/baseline current Schedule A | Pass | Existing Rollease file identified and opened |
| Upload new Schedule A version | Pass | ShareFile duplicate confirmation handled; upload reached 100% |
| Dashboard intake | Pass | New filing appeared; total increased to 32 |
| Extraction | Pass with review exceptions | 38/40 fields found; 5 decisions required |
| Review evidence | Pass | Field comparison, statuses, workflow progress visible |
| FTW Agent connectivity | Pass | Compatible agent connected and ready |
| FTW plan mapping | Blocked | No verified plan mappings |
| FTW matching | Blocked | Current-year Schedule A missing |
| FTW send/update | Pending | Intentionally not executed |

## Conclusion

The Rollease ShareFile → dashboard → extraction pipeline is working. The live blocker is downstream FT Williams targeting: the compatible agent is connected, but no verified plan mapping exists and the current-year Schedule A is missing from the target. The next safe step is to verify/map the Rollease FT Williams plan and rerun matching. The final send should only be tested after explicit approval of the exact controlled update.
