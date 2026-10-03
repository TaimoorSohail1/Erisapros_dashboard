# Pomerene Hospital Schedule A production validation — 2026-10-03

## Outcome

The Pomerene Hospital TEST package was tested through ShareFile intake, deterministic extraction, Plan Worksheet harmonization, FT Williams current-value retrieval, routing, Schedule A matching/creation, broker handling, update submission, and FT Williams read-back.

Six Schedule A operations completed with verified FT Williams read-back. Two new Schedule As were created and the existing set was preserved. A total of 43 selected field writes and 17 prepared broker rows were exercised across the successful operations. The package is **partially validated, not fully green**, because the image-only Reliance document could not complete automated OCR and the current one-filing/one-primary-policy workflow does not independently send secondary policies contained inside the AultCare and EyeMed multi-policy documents.

## Release identity and production checks

- Validation branch: `codex/pomerene-schedule-a-validation`
- Production release branch: `codex/automated-ftw-workflow`
- Final application commit: `361586a` (`fix(extraction): isolate Sun Life covered lives evidence`)
- Final CodeBuild: `erisapros-production-backend:9b6ce12d-5d26-4a38-91d5-e9727dca2029` — `SUCCEEDED`
- Immutable image: `427925098650.dkr.ecr.eu-north-1.amazonaws.com/erisapros-production-containerrepository-pex9hu6lblao@sha256:28cd8b80d10951f22f5b8fad2a72ee1ce204624f047dc6d071f496b98b2ccbef`
- API task definition: `erisapros-production-api:80`
- Worker task definition: `erisapros-production-sharefile-worker:70`
- ECS stability: both services desired 1, running 1, pending 0, one active deployment
- Production health: `{"status":"ok","stack":"react-python-mongodb"}`
- Backend regression suite: `959 passed, 2 skipped, 1 warning`

## Plan Worksheet result

The newest Plan Worksheet was identified as a native DOCX worksheet and extracted as follows:

- Sponsor: `POMERENE HOSPITAL`; EIN `31-1518658`; plan number `501`
- Plan: `POMERENE HOSPITAL WELFARE BENEFIT PLAN`
- Plan year: `01/01/2025` through `12/31/2025`; original effective date `01/01/2016`
- Sponsor address: `981 WOOSTER ROAD MILLERSBURG OH 44654`; business code `622000`
- Participants: beginning `358`; active beginning `346`; active end `351`; receiving benefits `2`; other entitled `18`; participant end `371`
- Fully insured references: Guardian `00579270`, AultCare `950052`, EyeMed `10049061001`, Reliance `GL 160111`, Guardian `00579175`, and American Heritage `MH301`

The worksheet reference values were harmonized into Schedule A Part IV. The live Guardian `00579270` operation verified the four changed participant values (`351`, `18`, `371`, and `2`) in FT Williams.

## Per-document results

| Source | Layout and extracted result | FT Williams result | Automatic updates and broker result | Final status |
|---|---|---|---|---|
| AultCare workbook — policies `950052` and `25365` | Native XLSX multi-policy layout identified. `950052`: AultCare Insurance Company, EIN `34-1624818`, NAIC `77216`, 243 covered, premium `4,059,527`, Hummel commission `29,855`. `25365`: same carrier identity, 245 covered, premium `9,020.25`, commission/fees `0`. | Existing Schedule A #1 matched for `950052`. Two fields were written and read back at 10:31:47. | Premium and covered lives updated; one Hummel broker row selected. Existing Schedule As preserved. | **Primary policy verified.** Secondary `25365` extracted but not materialized as a separate FTW operation. |
| EyeMed both-policies PDF — `10049071001` and `10049061001` | Multi-policy EyeMed layout identified. Carrier Fidelity Security Life Insurance Company, EIN `43-0949844`, NAIC `71870`. `10049071001`: 172 covered, premium `2,451.00`, Hummel commission `245.62`. `10049061001`: 432 covered, premium `33,717.70`, Hummel commission `3,406.53`. | No safe match existed for `10049071001`; Schedule A #7 was created. Fourteen fields were saved and read back at 11:27:44. Existing Schedule A #2 (`10049061001`) and the other five records were preserved. | Broker filtering selected only the active contract. Hummel address read as `461 Wadsworth Road`, `PO Box 3`, `Orrville`, OH `44667`; one broker row was included. | **Primary policy verified with zero conflicts.** Secondary `10049061001` was preserved but not independently refreshed from this document. |
| Reliance Standard PDF — `GL160111` | Image-only layout. Manual evidence confirms Reliance Standard Life Insurance Company, EIN `36-0883760`, NAIC `68381`, 319 covered, premium `10,878.32`, Gallagher commission `1,522.94`, other fee `384.40`. | No safe automated FT Williams update was sent. GroundX repeatedly remained pending/timed out, and the production image has no independent image OCR fallback for this layout. | None. Existing FT Williams Schedule A #3 remained unchanged. | **Blocked — automated extraction/read-back not verified.** |
| Guardian STD PDF — `00579175` | Guardian layout identified. Guardian Life, EIN `13-5123390`, NAIC `64246`, 418 covered, premium `98,942.65`, commissions `12,862.54`, fees `0`; Redtail and Hummel each `6,431.27`. | Existing Schedule A #4 matched. Two fields were saved and read back at 10:29:18. | Premium and covered lives updated; two broker rows selected. | **Verified; zero true conflicts.** |
| American Heritage/Allstate PDF — `MH301` | American Heritage layout identified. EIN `59-0781901`, NAIC `60534`, 83 covered, premium `65,705.44`, commission `12,894.62`, fees `0`; ten broker allocations extracted. | Existing Schedule A #5 matched. Two fields were saved and read back at 10:34:02. | Premium and covered lives updated; ten broker rows selected. | **Verified; zero true conflicts.** |
| Guardian STD PDF — `00579270` | Guardian layout identified. Guardian Life, EIN `13-5123390`, NAIC `64246`, 63 covered, premium `29,598.29`, commission `4,774.21`, fees `0`; Lifetime `38.48` and Hummel `4,735.73`. | Existing Schedule A #6 matched. Six fields were saved and read back at 10:24:25. | Premium, covered lives, and four participant values updated; two broker rows selected. | **Verified; zero true conflicts.** |
| Sun Life PDF — `924948` | Sun Life layout identified. Sun Life Assurance Company of Canada, EIN `38-1082080`, NAIC `80802`, 62 covered, premium `32,478.18`, Gallagher commission `6,330.49`, fees `0`. | No safe existing match; Schedule A #8 was created. Seventeen fields were saved and read back at 12:21:09. All seven prior records were preserved. | One Gallagher broker row at `2850 Golf Rd`, `5th Fl`, Rolling Meadows, IL `60008` was included. | **Verified; zero conflicts.** One unsupported worksheet note remains audit-only and requires no action. |

## Routing, formatting, broker, and safety checks

- **One value available:** verified. New EyeMed and Sun Life records routed blank FTW values to `Will Update` (14 and 17 selected fields). Existing Guardian/AultCare/American Heritage records automatically selected only the changed fields.
- **Both blank:** verified as skip/preserve behavior. EyeMed reported three skipped fields; blank extractions did not erase current FT Williams data.
- **Both present and equal:** verified as no change/keep current. Preserved records were included in the complete replace-set payload and verified after each write.
- **Different values:** successful operations ended with zero unresolved true conflicts. Unsupported worksheet-only fields were retained as audit notes and did not block Schedule A writes.
- **Schedule A replace-set safety:** verified. The first rejected EyeMed attempt automatically restored and read back the original six records. The corrected retry created #7 while preserving six; Sun Life created #8 while preserving seven.
- **Broker matching:** 17 broker rows were exercised across the successful operations (1 AultCare, 2 + 2 Guardian, 10 American Heritage, 1 EyeMed, 1 Sun Life). No broker ambiguity required a value decision. The final send safety checkbox was explicitly included for broker writes.
- **No-fee handling:** zero fee values and empty fee rows were preserved for Guardian, American Heritage, EyeMed, and Sun Life. Sun Life's broker row and zero-fee result were accepted and read back. The true no-commission/no-fee checkbox case (`AultCare 25365`) is not fully verified because that secondary policy was not independently sent.
- **Uppercase text:** FT Williams read-back displayed normalized uppercase carrier identities for created records (`FIDELITY SECURITY LIFE INSURANCE COMPANY` and `SUN LIFE ASSURANCE COMPANY OF CANADA`) and the uppercase plan name.
- **Dates:** every sent policy and plan-year pair was `01/01/2025`–`12/31/2025`, exactly 12 months. Date normalization and the 12-month guard passed.
- **New record rule:** no-match contract identities created Schedule A #7 (`10049071001`) and #8 (`924948`) without overwriting the existing set.

## Issues found and fixes deployed

1. Added deterministic Pomerene extraction for AultCare, EyeMed, Guardian, American Heritage, Sun Life, Reliance OCR text, and the Plan Worksheet (`1284fe7`).
2. Allowed targeted client packages to be reprocessed and fixed a scan accumulator bug that previously retained only the last changed package (`2d697e5`, `507b7d9`).
3. Scoped broker rows to the selected contract in multi-policy documents (`b5a660c`). This prevented the second EyeMed policy's broker amount from entering the first policy's payload.
4. Split the EyeMed PO box from the city (`05534f2`). The first FT Williams rejection was safely rolled back; the corrected live retry passed.
5. Attached real Sun Life page evidence, recognized `Total Premium received` as explicit nonexperience-rated evidence, and isolated the covered-lives label from unrelated cover-letter wording (`cd04ebf`, `361586a`). The live retry reduced false decisions from 12 to zero Schedule A field decisions and the exact case then passed FT Williams read-back.

## Remaining blockers and recommended follow-up

1. **Reliance image OCR:** add a bounded second OCR provider/local image OCR path and re-run `GL160111` before filing.
2. **Multi-policy fan-out:** create one filing/FTW operation per worksheet summary so AultCare `25365` and EyeMed `10049061001` are independently compared and sent. Until this exists, a multi-policy source can be fully extracted but only its primary selected policy is automated end to end.
3. **MongoDB network stability:** several controlled retries saved extracted values but ended with a MongoDB Atlas read timeout (`timeoutMS 18000`). The successful FT Williams operations and read-backs prove the saved data paths worked, but the infrastructure timeout can incorrectly leave the filing status as `FAILED`; connection routing/retry behavior should be hardened.

Because of these three items, the client package should not be described as completely production-ready yet, despite six successful verified FT Williams operations.
