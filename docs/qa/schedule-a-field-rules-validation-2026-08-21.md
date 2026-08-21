# Schedule A Field Rules Validation — 21 August 2026

## Result

The two new Schedule A comparison fields work through the Field Rules, EyeLevel/GroundX extraction, and FT Williams current-data flow. Four representative Schedule A PDFs were processed with the configured GroundX provider. Three had an accessible matching FT Williams Schedule A record; the fourth was a synthetic sample with no FT Williams Schedule A for the tested years.

No FT Williams update was submitted. The code fixes in this report are local and have not been deployed.

## Published field rules

| Field | FTW current tag | Published aliases | Behavior |
| --- | --- | --- | --- |
| Health Indicator | `HealthInd` | Health; Medical; Medical coverage; Health benefit | Extract and compare; never send |
| Vision Indicator | `VisionInd` | Vision; Vision coverage; Eye care; Optical benefit | Extract and compare; never send |

Live Field Rules evidence:

- Published rule set: `cc3ab9a041aa`
- FTW catalog: `2026-08-638eed800538`
- Catalog contained 357 FTW fields.
- Both new rules appeared as `Published v1` on 21 August 2026.

## Real extraction evidence

| Document | SHA-256 | Provider | Key extracted results |
| --- | --- | --- | --- |
| `Completed_ScheduleA_Sample.pdf` | `6B6843E5021578678FCEDC2A4D961D1B477A0388AD0B066D3F8B2B99464AF4E1` | GroundX X-Ray | UnitedHealthcare; Health Yes; Vision Yes; EIN, NAIC, contract, and premium extracted |
| `1. Kaiser 127759 Schedule A.pdf` | `43FE833D310B03972469B69DDD4743261E6FEE4AF132511C58F02E8D4B759506` | GroundX X-Ray | Kaiser Foundation; Health Yes; EIN, NAIC, contract, and premium extracted |
| `5955240_Schedule A_01012025_12312025 (3).pdf` | `BCAFDE1DA6CB6C70ACB4CEFACA6BD82EA204084F7F2A75472E5895A4911506E8` | GroundX X-Ray | MetLife; EIN `13-5581829`; NAIC `65978`; contract `5955240`; premium `15,870` |
| Oxford/EyeMed Schedule A | `012B654650EC43914FB0AE691C81E1F6F3CC004F48B0B08F8C04C7FE1E34545C` | GroundX X-Ray | Fidelity Security Life; Vision Yes; EIN `43-0949844`; NAIC `71870`; validated combined contract `1042075/6-1001/1002`; premium `11,965` |

Blank, template, and duplicate outputs were removed. Health/Vision were only populated when the document supplied evidence; they were not invented when absent.

## Read-only FT Williams evidence

FT Williams was queried without performing any update. Matching used customer, plan, year, and the exact Schedule A sequence rather than accepting the first record.

| Filing | Exact FTW record | Verification |
| --- | --- | --- |
| Oxford/EyeMed | Sequence 3; EyeMed Vision Care; EIN `43-0949844`; NAIC `71870`; Vision `1` | Correct sequence selected; extracted Vision Yes equals FTW `1` |
| Special Service/Kaiser | Sequence 1; Kaiser Foundation; EIN `94-1340523`; NAIC `00000`; Health `1` | Correct sequence selected; extracted Health Yes equals FTW `1`. Document contract `127759` differs from FTW current `608066`, so review is correctly required |
| Advertising Council/MetLife | Sequence 2; MetLife; EIN `13-5581829`; NAIC `65978`; contract `5955240`; Health `0`; Vision `1` | Identity fields matched. The source PDF did not explicitly label Health/Vision, so extraction correctly left them absent while FTW current values remained visible |
| Bluestone synthetic sample | Plan was found, but FTW returned no Schedule A for 2024 or 2025 | External FTW data absence; extraction passed but no current FTW record exists for comparison |

## Issues found and fixed

1. Safe Extraction QA could wait longer than CloudFront's 60-second origin timeout. It now has a 45-second application timeout and a deterministic Schedule A parser fallback.
2. GroundX could return template filler, duplicates, blank values, or invalid contract fragments. Extraction now filters these and keeps one validated best value per field.
3. AI output could override a more reliable Schedule A table value. Stable local-parser fields now win when the AI result conflicts or fails validation.
4. Published/discovered FTW aliases were not consistently used by deterministic extraction. They now participate in the extraction match flow.
5. FTW represents boolean indicators as `1/0`, while EyeLevel returns `Yes/No`. Comparison now treats these representations as equivalent and still flags genuine differences.

## Automated regression proof

- Focused extraction, QA-timeout, and FTW comparison tests: **77 passed**.
- Complete backend suite: **302 passed, 2 skipped**.
- Frontend build, type-check, smoke, performance, review UI, dashboard UI, and Field Rules UI checks: passed.
- Browser console errors during the tested Field Rules workflow: **0**.

## Constraint

An actual ShareFile upload could not be performed because the available ShareFile browser session had expired and required sign-in. The same four source documents were therefore run directly through the application's configured GroundX extraction service. After ShareFile sign-in, one final intake smoke test should confirm the external ShareFile transport; the extraction, Field Rules, and FT Williams read paths have already been verified.
