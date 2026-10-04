# Apollo Global Management — Schedule A Production Validation

**Validation date:** October 4, 2026  
**Client:** Apollo Global Management TEST  
**Plan:** APOLLO GLOBAL MANAGEMENT GROUP INSURANCE PLAN  
**Plan sponsor EIN / plan number:** 20-8351069 / 501  
**Plan year:** January 1, 2025–December 31, 2025

## Executive result

The newest Plan Worksheet and all ten Schedule A source documents were inspected, extracted, routed through the production workflow, compared with the corresponding FT Williams 2025 filing, and re-run after the fixes described below.

- **9 of 10 Schedule As complete automatically.**
- **1 of 10 requires a human decision:** ACE policy N18154614 has genuine worksheet-versus-source date conflicts and no safely matching FT Williams Schedule A.
- **4 automatic cases have successful FT Williams update receipts and field-level read-back:** Sun Life, First Unum 910370, First Unum 612696, and VSP.
- **5 automatic cases finish as reconciled no-ops on the clean final run:** Delaware American, Cigna 04094A, Delta Dental, Cigna 04094B, and Mount Sinai. Their applicable FT Williams values already match, and the no-commission/no-fee cases correctly recognize FT Williams' `OverrideCommissionsAndFees` checkbox.
- Broker handling completed automatically for the unambiguous NFP and Alterity matches. AON was identified for ACE, but nothing was sent because the Schedule A itself remains blocked by real conflicts.
- The production API is healthy, and both production ECS services are stable on the same deployed image.

## Scope and acceptance checks

The validation covered:

1. Layout identification and complete extraction for DOCX and each carrier-specific PDF layout.
2. Plan Worksheet and Schedule A field normalization, including uppercase text.
3. FT Williams current-value retrieval and exact record matching.
4. Routing rules: one-sided values update automatically; both blank skip; equal values no-op; materially different values alone require a decision.
5. Broker matching, addition/update routing, and ambiguity handling.
6. No-commission/no-fee checkbox behavior.
7. Policy dates limited to a maximum inclusive 12-month period.
8. New-record behavior when EIN and contract number cannot match an existing FT Williams Schedule A.
9. FT Williams send, read-back, and completion state.

## Plan Worksheet validation

Source: `5500 Plan Worksheet - Apollo Global Management 5500 - PY25.docx`

| Field | Extracted / validated value | FT Williams result |
|---|---|---|
| Sponsor | APOLLO GLOBAL MANAGEMENT | Matched |
| Sponsor EIN | 20-8351069 | Matched |
| Plan number | 501 | Matched |
| Plan name | APOLLO GLOBAL MANAGEMENT GROUP INSURANCE PLAN | Matched |
| Address | 100 WEST PUTNAM AVE, 3RD FLOOR, GREENWICH, CT 06830 | Matched/normalized uppercase |
| Business code | 523900 | Matched |
| Plan year | 01/01/2025–12/31/2025 | Matched |
| Effective date | 06/01/1980 | Matched |
| Total participants, beginning | 1,937 | Extracted correctly |
| Active participants, beginning | 1,839 | Extracted correctly |
| Active participants, end | 2,083 | FT Williams read-back confirmed 2,083 |
| Retired/COBRA receiving benefits | 52 | FT Williams read-back confirmed 52 |
| Other participants entitled to future benefits | 32 | FT Williams read-back confirmed 32 |
| Total participants, end | 2,167 (derived) | FT Williams read-back confirmed 2,167 |

Worksheet rows containing `N/A` dates are now safely ignored rather than aborting the client package.

## Schedule A results

### 1. VSP — `2. Apollo 2025 5500 (1).pdf`

- **Layout/extraction:** VSP experience-rated report correctly identified. Carrier EIN 22-2777159; NAIC 47029; contract 30105423; 3,055 persons; policy 01/01/2025–12/31/2025.
- **Financial mapping:** 9a amount received = 503,524.06; 9b(1) claims paid = 424,707.02; 9c(1)(B) administrative fees = 67,975.56. These values are not treated as broker compensation or line 10a premium.
- **FT Williams:** Matched sequence 2. Five fields sent and five confirmed by read-back, including organizational code 3.
- **Result:** **COMPLETED automatically.** Receipt `281e78f5c26f4ebcb2dfb77063c4b05d`, action `SCHEDULE_A_UPDATED`.

### 2. Cigna — `3.CHLIC_04094A_Apollo Management_ L_P_.PDF`

- **Layout/extraction:** Cigna G2050A layout correctly identified. Carrier EIN 59-1031071; NAIC 67369; contract 04094A; 56 persons; policy 01/01/2025–12/31/2025; premium 888,075; no commission/fee.
- **FT Williams:** Matched sequence 3. Persons and premium read back exactly. Final clean run recognizes the no-commission/no-fee override checkbox and does not create anonymous zero-value broker rows.
- **Result:** **COMPLETED automatically** as an already-reconciled no-op after verified earlier field writes. No decision remains.

### 3. First Unum — `5. 910370 010125-26 b.pdf`

- **Layout/extraction:** First Unum layout correctly identified. Carrier EIN 13-1898173; NAIC 64297; contract 910370; 2,083 persons; source anniversary period normalized to 01/01/2025–12/31/2025; premium 2,050,681.96; NFP commission 102,534.28.
- **Broker:** NFP automatically matched; no human decision.
- **FT Williams:** Matched sequence 5. Two required changes sent and both confirmed by read-back: persons 2,083 and premium 2,050,681.96.
- **Result:** **COMPLETED automatically.** Receipt `efc57b92cef64730ab05de2bfa350bc1`, action `SCHEDULE_A_UPDATED`.

### 4. First Unum — `6. 612696 010125-26 b.pdf`

- **Layout/extraction:** First Unum layout correctly identified. Carrier EIN 13-1898173; NAIC 64297; contract 612696; 1,505 persons; source anniversary period normalized to 01/01/2025–12/31/2025; premium 637,857.78; NFP commission 31,892.89.
- **Broker:** NFP automatically matched; no human decision.
- **FT Williams:** Matched sequence 6. Two required changes sent and both confirmed by read-back: persons 1,505 and premium 637,857.78.
- **Result:** **COMPLETED automatically.** Receipt `0c165f61941449f695b38d67aa27577d`, action `SCHEDULE_A_UPDATED`.

### 5. Mount Sinai Solutions — `7. 2025 Apollo Management Holdings, L.P. Schedule A - Form 5500.pdf`

- **Layout/extraction:** Mount Sinai layout correctly identified. Contract APOLLO; 0 persons at year end; total charges/fees 230,926; no commission/fee. Carrier EIN and NAIC are absent from the source and therefore correctly left blank rather than invented.
- **FT Williams:** Matched sequence 8. Existing FT Williams EIN 82-4856792 and NAIC 56190 are preserved through `KEEP_CURRENT`; persons 0 and total charges 230,926 read back exactly. Final run correctly recognizes the no-commission/no-fee checkbox.
- **Result:** **COMPLETED automatically** as a reconciled no-op. No decision remains.

### 6. Sun Life — `7. 931755- Schedule A - 01012025 - 12312025..pdf`

- **Layout/extraction:** Sun Life layout correctly identified. Carrier EIN 06-0893662; NAIC 80926; contract 931755; 1,929 persons; policy 01/01/2025–12/31/2025; premium 2,026,913.43; Alterity commission 91,286.13. The separate 33,750 bonus is not misclassified as a fee.
- **Broker:** Alterity automatically matched; no human decision.
- **FT Williams:** Matched sequence 7. Five fields sent and five confirmed by read-back, including persons 1,929 and premium 2,026,913.43.
- **Result:** **COMPLETED automatically.** Receipt `c2cac12eb4134ae387ce2bd4d2cfe17c`, action `SCHEDULE_A_UPDATED`.

### 7. ACE — `8. N18154614 SCHEDULE A 25-26.pdf`

- **Layout/extraction:** ACE layout correctly identified. Carrier EIN 95-2371728; NAIC 22667; contract N18154614; source persons value explicitly says `To be Provided` and therefore remains blank; policy 06/01/2025–05/31/2026; premium 184,676; AON commission 36,935.20.
- **Broker:** AON is automatically identified, but no broker or Schedule A write is sent while the record is blocked.
- **FT Williams:** No existing Schedule A can be matched safely by EIN and contract. Creation is correctly proposed rather than attaching the data to an unrelated record.
- **Real conflicts requiring a decision:**
  1. Worksheet contract contains `ADD N18154614`, while the source contract is `N18154614`.
  2. Worksheet beginning date is 06/01/2024, while the source is 06/01/2025.
  3. Worksheet ending date is 05/31/2025, while the source is 05/31/2026.
- **Result:** **ACTION_NEEDED.** This is the only filing not automatically completed, and it is correctly blocked rather than written to FT Williams.

### 8. Cigna — `9. CHLIC_04094B_Apollo Management_ L_P_.PDF`

- **Layout/extraction:** Cigna G2050A layout correctly identified. Contract 04094B; 1 person; policy 01/01/2025–12/31/2025; premium 49,700; no commission/fee.
- **FT Williams:** Matched sequence 4. Persons 1 and premium 49,700 read back exactly. Final run recognizes the no-commission/no-fee override checkbox.
- **Result:** **COMPLETED automatically** as an already-reconciled no-op. No decision remains.

### 9. Delaware American Life — `11. MLIFE_04094A_Apollo Management_ L_P_.PDF`

- **Layout/extraction:** Carrier-specific layout correctly identified. Carrier EIN 51-0104167; NAIC 62634; contract 04094A; 56 persons; policy 01/01/2025–12/31/2025; premium 2,478; no commission/fee.
- **FT Williams:** Matched sequence 11. Persons 56 and premium 2,478 read back exactly. Final run recognizes the no-commission/no-fee override checkbox.
- **Result:** **COMPLETED automatically** as an already-reconciled no-op. No decision remains.

### 10. Delta Dental — `12. Apollo Management Holdings, L.P Schedule A 2025.pdf`

- **Layout/extraction:** Delta Dental layout correctly identified. Carrier EIN 42-0959302; NAIC 55786; contract 43173; 1,980 persons; policy 01/01/2025–12/31/2025; premium 2,113,696.44; no commission/fee.
- **FT Williams:** Matched sequence 12. Fourteen substantive fields were confirmed during the earlier write/read-back, including carrier identity, contract, dates, persons, premium, plan identity, and sponsor EIN. Final clean run recognizes the no-commission/no-fee override checkbox.
- **Result:** **COMPLETED automatically** as an already-reconciled no-op. No decision remains.

## Defects found and fixed

1. **Plan Worksheet `N/A` date crash:** non-date worksheet values could terminate processing for the whole client package. Fixed by safely skipping `N/A` date cells.
2. **Carrier layout extraction gaps:** added or corrected deterministic extraction for Cigna G2050A, Delta Dental, First Unum, ACE, Mount Sinai, Sun Life, and VSP; generic extraction contamination is removed when an authoritative carrier parser succeeds.
3. **VSP experience-rated misclassification:** payments, claims, and carrier administrative fees could be routed to the wrong Schedule A lines. Fixed to map to 9a, 9b(1), and 9c(1)(B), respectively.
4. **Date span normalization:** exclusive anniversary dates are normalized to an inclusive period of no more than 12 months.
5. **No-fee false failures:** zero commission/fee fields were incorrectly verified as missing zero-value broker rows. Fixed to use the FT Williams `OverrideCommissionsAndFees=1` checkbox.
6. **No-op completion:** filings whose FT Williams values already match could end in failure instead of completion. Fixed so fully reconciled no-op workflows complete automatically.
7. **Cross-client recovery scope:** targeted rescans could select same-named packages from another client. Fixed by resolving and checking the full ShareFile folder ancestry before processing.

The seven previously contaminated Bank of Bartlett recovery records are now `SUPERSEDED` and invisible; later records carry the correct Bank client identity. No destructive deletion was performed.

## Regression and production evidence

- Full automated suite: **1,010 passed, 2 skipped, 1 warning, 68 subtests passed**.
- Focused extractor/workflow suite: **376 passed, 4 subtests passed**.
- VSP exact-source regression confirms only the correct 9a/9b/9c experience-rated values are emitted; no erroneous 3c broker fee or 10a premium is produced.
- Production API task definition: `erisapros-production-api:100`.
- Production worker task definition: `erisapros-production-sharefile-worker:90`.
- Both services use image digest `sha256:94e49abcd5c5f68f2da7160ae66f7d3f20311dc4d31ef2caffc08ea7ea2c0059`.
- Production health response: `{"status":"ok","stack":"react-python-mongodb"}`.
- Final production filing IDs:
  - Delaware American: `6ac1acfc09091d76805042d0`
  - Sun Life: `6ac1ad0809091d76805042d5`
  - Cigna 04094A: `6ac1ad1409091d76805042da`
  - Delta Dental: `6ac1ad2009091d76805042de`
  - ACE: `6ac1ad2c09091d76805042e2`
  - First Unum 910370: `6ac1ad3709091d76805042e6`
  - Cigna 04094B: `6ac1ad4409091d76805042ea`
  - VSP: `6ac1ad5009091d76805042ee`
  - First Unum 612696: `6ac1ad5b09091d76805042f2`
  - Mount Sinai: `6ac1ad6709091d76805042f6`

## Final disposition

Apollo Global Management is validated end to end in production. All safely automatable records are complete and FT Williams values are either receipt/read-back confirmed or reconciled as already correct. The ACE source remains intentionally unsent until the three real worksheet/source conflicts are resolved.
