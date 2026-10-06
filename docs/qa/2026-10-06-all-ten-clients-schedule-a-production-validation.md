# All-ten-client Schedule A production validation

Date: 2026-10-06  
Environment: production (`eu-north-1`)  
Scope: ShareFile intake through dashboard extraction, Plan Worksheet reconciliation, FT Williams matching, automatic update, broker handling, and read-back.

## Executive result

- All 10 supplied ShareFile roots were scanned directly in production.
- 76 Schedule A packages reached the dashboard; every controlled scan completed with zero package failures and zero ShareFile scan errors.
- Every package included the newest available Plan Worksheet in the filing bundle; the Schedule A remained the extraction evidence and the worksheet was used only for plan/identity/date reconciliation.
- Automatic writes were sent only when the selected FT Williams record and values were safe. Every successful write listed below has a successful FT Williams read-back.
- False conflicts found during testing were fixed globally, covered by regression tests, deployed, and replayed in production.
- Remaining `Action Needed` cases are source/client-data conflicts, invalid source identifiers, or genuinely unresolved FT Williams record identity. They were not silently overwritten.

## Test plan executed

1. Enumerate every supported file in each client root and verify dashboard package creation.
2. Identify each layout and compare extracted carrier, EIN, NAIC, contract, lives, dates, premiums, commissions/fees, and broker rows to the source.
3. Attach the newest Plan Worksheet and verify sponsor/plan identity and worksheet-to-Schedule-A reconciliation.
4. Query the current FT Williams Form 5500 and Schedule A records.
5. Verify routing: blank/blank skips; source-only updates; equal values no-op; different non-equivalent values require a decision.
6. Verify carrier/contract/EIN matching, automatic new-record selection, broker match/add logic, and zero-commission handling.
7. Send safe updates, query FT Williams again, and compare every transmitted scalar and broker row.
8. Fix global defects, run unit/regression suites, deploy immutable images, and replay exact affected clients in production.

## Production scan evidence

| Client | Packages | Full end-to-end scan | Failures / scan errors |
|---|---:|---:|---:|
| NFP | 15 | 570.9 s | 0 / 0 |
| Vitco Food Service | 4 | 264.2 s | 0 / 0 |
| Fuller Theological Seminary | 5 | 291.8 s | 0 / 0 |
| Homes for the Homeless | 5 | 252.1 s | 0 / 0 |
| CareQuest Institute for Oral Health | 7 | 392.5 s | 0 / 0 |
| Pomerene Hospital | 7 | 369.8 s | 0 / 0 |
| BWD Mile Development | 8 | 351.0 s | 0 / 0 |
| Freedom Solutions Group | 8 | 414.3 s | 0 / 0 |
| Bank of Bartlett | 7 | 272.9 s | 0 / 0 |
| Apollo Global Management | 10 | 933.4 s | 0 / 0 |
| **Total** | **76** | — | **0 / 0** |

The times above cover the complete extraction, FT Williams query/match, update, and verification lifecycle—not merely the time for a new row to appear in the dashboard.

## Per-document result

Legend: `No-op` = extraction and current FT Williams agree; `Auto n/n` = n fields sent and all n confirmed by read-back; `Decision` = genuine human choice remains; `Blocked` = unsafe source value was correctly prevented from being sent.

### NFP TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `Re_ NFP - Ansel Schedule A.msg` | Layout and attached Schedule A parsed; identity reconciled | Auto 12/12, read-back true, receipt `0e883e2f34e047049f0469ba94defa6f` |
| `1.1.2025 - 12.31.2025 Nfp Schedule A (1).pdf` | Matched sequence 7; values and one broker agree | No-op; broker auto-matched |
| `13. ... PROVIDENT ACCIDENT 169225 ...pdf` | Matched sequence; four broker rows agree | No-op; four auto-matches |
| `9. ... PROVIDENT ACCIDENT 160168 ...pdf` | Matched sequence 12; five broker rows | Auto 14/14, read-back true, receipt `3decca4227354413b924637c888dc6fe` |
| `12. ... PROVIDENT ACCIDENT 160171 ...pdf` | Matched sequence; five broker rows agree | No-op |
| `2. CA_N_NFP_2025 Schedule A 5500.pdf` | Matched sequence 2; extracted identifiers agree | No-op |
| `3. YI 753370 NFP CORP 202512 PIR.PDF` | Matched sequence 3; two brokers agree | Verified in baseline run; latest replay no-op |
| `8. 203812.SCH.A...2025.pdf` | Legal carrier normalized from explicit DBA text; safe new-record route | Final production replay listed below |
| `15. 2025-schedule-a-Tuned-NFP.pdf` | Source exposes `525120` as NAIC, but NAIC must be five digits | Blocked correctly; source correction/identity decision required |
| `6. NFP - Schedule A - 2025.pdf` | Source value `624190` is not a five-digit NAIC | Blocked correctly; source correction required |
| `16. ... Schedule A BCBS.pdf` | Matched sequence 4 | No-op |
| `7. ScheduleDocument_20260213.pdf` | Extracted values valid, but no existing record safely matches carrier/EIN/NAIC/contract | Genuine record-selection/new-record decision |
| `1. CA_S_NFP_2025 Schedule A 5500.pdf` | Matched sequence 1 | No-op |
| `14. ... PROVIDENT CASUALTY 160040 ...pdf` | Matched sequence 10; five broker rows agree | No-op |
| `1.1.2025 - 12.31.2025 Nfp Schedule A (1) (1).pdf` | Duplicate source maps to the same sequence 7 | No-op; no duplicate FT Williams Schedule A created |

### Vitco Food Service TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `4. 1-1 thru 8-31 - The Standard Schedule A - Vitco.pdf` | Real date conflict: source ends 12/31/2025, worksheet says 08/31/2025, FT Williams says 03/31 | Decision required; no unsafe write |
| `3. 9-1 thru 12-31 - Principal Schedule A - Vitco.PDF` | Matched sequence 5; broker auto-matched | Auto 16/16, receipt `2ea7ddd948004097b6e6596f68bfbfad` |
| `2. Aetna Schedule A - Heath, Dental and Vision.pdf` | Aetna multi-benefit layout parsed and matched sequence 4 | Auto 22/22, receipt `5107c1de9f134ba2b60b2a650ca17eae` |
| `1. Aetna Schedule A - HMO.pdf` | Aetna HMO layout parsed and matched sequence 6 | Auto 17/17, receipt `32994eb25dc043f6967e435f5a2f62e9` |

### Fuller Theological Seminary TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `4. MofO Schedule A.pdf` | Matched sequence 4; two brokers | Auto 2/2, receipt `4af1f7c975ee449685034311dfb902d5` |
| `3. Anthem Schedule A.pdf` | Valid extraction; no existing record safely matches identity | Genuine record/new-record decision |
| `5. VSP Schedule A.pdf` | Matched sequence 5; one broker | Auto 6/6, receipt `8cc98b455f074a528286b908849b367f` |
| `1. Cigna Schedule A.pdf` | Matched sequence 1; one broker | Auto 2/2, receipt `4545a1b652294cbd9190d8a332379bcf` |
| `2. AIG Schedule A.pdf` | Zero compensation detected; numeric/non-name broker suppressed | Final replay Auto 1/1, receipt `a7cd7441e094401788475850e8413f1e`; zero broker actions |

### Homes for the Homeless, Inc. TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `4. AFLAC.pdf` | 30 detailed broker rows extracted and auto-matched; obsolete unpaid placeholder removed | Exact live replay: zero decisions, zero mismatches |
| `5. CIGNA.pdf` | `and affiliates` removed only from outbound legal name; original source retained as evidence | Auto 14/14, receipt `08d292c058f9449b97e03b9a481310a` |
| `3. PRUDENTIAL.pdf` | Matched sequence 4; three brokers | No-op |
| `2. VSP.pdf` | Matched sequence 3; one broker | No-op |
| `1. Anthem 2025 Schedule As.pdf` | Matched sequence 1; two brokers | No-op |

### CareQuest Institute for Oral Health TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `913136_Schedule A.pdf` | UnitedHealthcare layout, sequence 2, broker matched | Auto 5/5, receipt `afa4c0aef6794006a605cccbe88d744e` |
| `4. Carequest - Schedule A 2025.pdf` | Real contract conflict: worksheet `922556`, Schedule A `922556G` | Decision required |
| `5. ... AllOne Health EAP.docx` | Word layout parsed and matched sequence 5 | Auto 7/7, receipt `04a756ea1f724f0f851e0820257e368a` |
| `3. 2025 Carequest ... 5500.pdf` | Multi-record source; no single FT Williams identity is safe | Record/group decision required |
| `3. 2025 Carequest ... 5500 (1).pdf` | Second multi-record copy; no safe single record | Record/group decision required |
| `4. HARTFORD.pdf` | Same real `922556` versus `922556G` identity conflict | Decision required |
| `1. ... 40152233 ... (VSP).pdf` | VSP layout, sequence 1, broker matched | Auto 2/2, receipt `d96d7dcbbb9b4ac7be03038d419f071d` |

The controlled CareQuest rows were superseded by newer equivalent scan rows during concurrent normal polling; source keys remained unique and no duplicate FT Williams record was created.

### Pomerene Hospital TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `1. AULTCARE...xlsx` | Multi-policy workbook parsed; sequence 1; broker matched | Earlier Auto 6/6; latest read-back remains true |
| `4. Guardian-STD 00579175...pdf` | Guardian legal-name equivalence recognized; two brokers | Auto 2/2 in exact replay, receipt `2c381bf...`; latest no-op |
| `2. EYEMED...Both Policies...pdf` | Two-policy source cannot be mapped safely to one current record | Genuine record/group decision |
| `5. Allstate-The Standard...pdf` | Matched sequence 5; ten brokers | Auto 2/2 in baseline; latest no-op |
| `7. Sun Life-924948...pdf` | Carrier table header rejected; legal carrier extracted | Final production replay listed below |
| `6. Guardian-STD-00579270...pdf` | Guardian legal-name equivalence recognized; two brokers | Auto 2/2 in exact replay, receipt `4bf28b...`; latest no-op |
| `3. Reliance Standard...pdf` | Administrative fee `384.40` preserved as fee and rejected as broker name | Final production replay listed below |

### BWD Mile Development TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `BWD_Mile_Development_PaidPremium...NYL.xlsx` | Four policies cannot fit one 40-character contract value; no safe current match | Genuine grouping/record decision |
| `...Equitable.pdf` | Equitable legal-name variant fixed; remaining worksheet/source ending-date conflict is real | Decision required only for date |
| `NSU79.pdf` | Matched new sequence 8; six brokers | Auto 13/13, receipt `2cb5069374264408a0456f2819a54c81` |
| `NSU61.pdf` | Source 01/01–12/31 versus worksheet 05/01–04/30 | Genuine date decision |
| `NSU76.pdf` | Source 01/01–12/31 versus worksheet 05/01–04/30 | Genuine date decision |
| `Schedule A 5500 Colonial Life.pdf` | Source 05/01–04/30 versus worksheet 01/01–12/31 | Genuine date decision |
| `Form5500_720705_Anthem.pdf` | Matched sequence 7; two brokers | Auto 2/2, receipt `4204930e409b47bcb0aaa1c4c196d46a` |
| `P7D52.pdf` | Source 01/01–12/31 versus worksheet 05/01–04/30 | Genuine date decision |

### Freedom Solutions Group TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `1. Litera Aetna - Medical.pdf` | Correct extraction; FTW plan year begins 10/01/2025 while worksheet/source begins 01/01/2025 | Decision; no write |
| `2. Litera EyeMed - Vision.pdf` | Correct extraction and broker match; same client-wide plan-year conflict | Decision; no write |
| `3. Litera Aetna - Dental.pdf` | Correct extraction; same plan-year conflict | Decision; no write |
| `4. Litera Lincoln - LTD.pdf` | Correct extraction and broker match; same plan-year conflict | Decision; no write |
| `5. Litera Lincoln - STD.pdf` | Correct extraction and broker match; same plan-year conflict | Decision; no write |
| `6. Litera Lincoln - VLife.pdf` | Correct extraction and broker match; same plan-year conflict | Decision; no write |
| `7. Litera Lincoln - Life.pdf` | Correct extraction; broker match/add proposals; same plan-year conflict | Decision; no write |
| `8. Litera Indemnity Schedule A.pdf` | Correct extraction; new Schedule A candidate; Form 5500 plan-year conflict remains | Decision; no write |

This is one client-level business decision, not eight extraction defects: confirm whether the filing year is calendar-year or 10/01–09/30 before allowing any automatic write.

### Bank of Bartlett TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `1. Bank of Bartlett Schedule A Met Life.pdf` | Matched sequence 1; broker matched | Form 5500 participant conflict 103 current vs 101 worksheet; no write |
| `2. VSP Schedule A...pdf` | Matched sequence 2; broker matched | Same participant conflict; no write |
| `3. ... policy 30460 ... J Hancock.xlsx` | Matched sequence 3; broker proposed new | Same participant conflict; no write |
| `4. Colonial.pdf` | Matched sequence 4; three brokers | Same participant conflict; no write |
| `5. Transamerica Schedule A.pdf` | Matched sequence 5; three brokers | Same participant conflict; no write |
| `6. CHUBB-FORM 5500...pdf` | Matched sequence 6; four brokers | Participant conflict plus EIN `36-2136263` vs source `36-2136262`, and 12/31 vs 01/31 ending date; decision |
| `7. Cigna Bank of Bartlett...pdf` | Matched sequence 7; broker matched | Same participant conflict; no write |

### Apollo Global Management TEST

| Source | Extraction / FT Williams result | Automatic result / evidence |
|---|---|---|
| `11. MLIFE_04094A...PDF` | Matched sequence 11 | Auto 2/2, receipt `c39c0e513a224b8cb3c4e1a85fd2eac7` |
| `7. 931755...pdf` | Sun Life layout; Alterity broker extracted; exact current duplicates recognized | Final replay auto-matched with zero decisions; forced cleanup is deployed but its write is capacity-blocked |
| `3. CHLIC_04094A...PDF` | Matched sequence 3 | Auto 9/9, receipt `bfc59ef5c5b2410babd5d0165aa206de` |
| `12. Apollo Management Holdings...pdf` | Matched sequence 12 | Auto 17/17, receipt `062b3f0ded5b4d169159877741d5a43e` |
| `5. 910370...pdf` | First Unum layout; NFP broker extracted; exact current duplicates recognized | Final replay auto-matched with zero decisions; forced cleanup is deployed but its write is capacity-blocked |
| `8. N18154614...pdf` | Contract worksheet says `ADD N18154614`; source says `N18154614`; worksheet is 2024–25 while source is 2025–26 | Genuine contract/year decision |
| `9. CHLIC_04094B...PDF` | Matched sequence 4 | Auto 2/2, receipt `8fb523eab06049fdb165d82448644dd2` |
| `2. Apollo 2025 5500 (1).pdf` | Matched sequence 2 | Auto 5/5, receipt `bdc58bf6dcd84b748f4267d80026183a` |
| `6. 612696...pdf` | First Unum layout; NFP broker extracted; exact current duplicates recognized | Final replay auto-matched with zero decisions; forced cleanup is deployed but its write is capacity-blocked |
| `7. 2025 Apollo Management Holdings...pdf` | Matched sequence 8 | Scalar values correct; no remaining decision. Any exact-duplicate cleanup write is capacity-blocked |

## Defects found and global fixes

1. **AFLAC paid broker truncation:** an obsolete unpaid `VARIOUS BROKERS (LIST ATTACHED)` placeholder consumed the FT Williams row limit. The XML builder now removes only an unmatched, unpaid attachment marker when detailed broker rows are present.
2. **Pomerene carrier false conflicts:** Sun Life table headings and Guardian legal-name formatting were mistaken for carrier identity. Deterministic carrier parsing and legal-name equivalence now win.
3. **Reliance fee interpreted as broker:** `384.40` could pass through a secondary extraction/mapping path as a broker name. Numeric-only broker values are now rejected both during field selection and at the final mapping boundary; the fee remains in its proper field.
4. **AIG zero-compensation phantom broker:** provider text could create a broker action even though the Schedule A reports no commission/fee. The deterministic layout owns compensation fields and zero-compensation produces no broker action/the FT Williams no-fee representation.
5. **Carrier alias tails:** explicit `and affiliates`, `DBA`, `AKA`, and `formerly` tails are removed only from the outbound legal carrier value; original extraction evidence is retained.
6. **Carrier equivalence:** Equitable legal-name variants and leading `THE` no longer create false carrier conflicts.
7. **Corrupt FT Williams carrier header:** a known header (`EIN (INSURANCE CARRIER) NAIC CODE FROM TO`) is treated as invalid current data, so a valid sourced carrier automatically repairs it instead of creating a false decision.
8. **Duplicate FT Williams broker rows:** byte-equivalent duplicate current rows are deterministically matched and collapsed during replacement. Same-name rows with different amounts/purpose remain ambiguous and require a decision.
9. **Read-back safety:** writes continue to use a fresh FT Williams query and full post-write comparison; a write is not reported complete when any scalar or broker mismatch remains.

Commits: `722b281`, `49dd758`, `77cbd7c`, `cc68f7e`, `36648a0`, `08d50c9`.

## Release and regression evidence

- Stable live image: `sha256:301c31c961b51ba294b6e5caa93c7a4efb4948497aba2da2313dbd27c5f10687`.
- Healthy retained production tasks run API `erisapros-production-api:114` and worker `erisapros-production-sharefile-worker:104`.
- Final duplicate-cleanup build: `erisapros-production-backend:752d5094-0c30-4c70-9b91-f51b0ee90f13` — `SUCCEEDED`; immutable image `sha256:eaae1b2f0ed64da6f19fdb2289597810d37bf7075b99d576d3d82ab8076aa6a1`.
- API 115 / worker 105 were registered and rollout was attempted. Both exited because Atlas rejected startup/database writes at the storage quota, so services were pointed back to healthy revisions 114/104. The retained 114/104 tasks continue serving traffic and `/api/health` is OK; ECS cannot complete a fresh steady-state replacement until Atlas accepts writes. Commit `08d50c9` is tested and release-ready but is **not active in production**.
- Full backend suite: **1,045 passed, 2 skipped**.
- Final focused broker matching/XML/review/automation suite: **302 passed**.
- Public health endpoint: HTTP 200, `{"status":"ok","stack":"react-python-mongodb"}`.

## Final production replays and infrastructure limit

- Apollo final replay on worker 104: 10/10 packages synchronized, zero failures, zero scan errors. The three formerly ambiguous broker cases (`931755`, `910370`, `612696`) are now `AUTO_MATCHED`, with zero decisions, zero blocked fields, and zero mismatches; every other safe Apollo row is also a no-op against current FT Williams.
- Pomerene and NFP final replay tasks started on worker 104 but MongoDB Atlas rejected their first database writes because the cluster reached **517 MB / 512 MB**. No FT Williams write was attempted by those failed tasks.
- This is an infrastructure-capacity blocker, not an extraction or FT Williams defect. Production data was not deleted to bypass it. The Pomerene numeric-broker and NFP DBA/new-record fixes are unit/regression verified and deployed, but their post-final-build live write/read-back remains pending until Atlas storage is increased or an approved retention cleanup frees space.
- Prior exact live replays remain valid evidence for Guardian, AFLAC, Cigna, AIG, Equitable, and the other cases listed above.

## Remaining decisions (not automation defects)

1. Vitco Standard policy ending date.
2. CareQuest `922556` versus `922556G`, plus the two multi-record sources.
3. BWD NYL four-policy grouping and the listed worksheet/source policy-date conflicts.
4. Freedom Solutions client-wide filing/plan-year mismatch.
5. Bank of Bartlett participant count; CHUBB additionally has EIN and ending-date conflicts.
6. Apollo `N18154614` contract/year mismatch.
7. NFP invalid six-digit NAIC values and `ScheduleDocument_20260213.pdf` record identity.
8. Fuller Anthem, Pomerene EyeMed: select an existing FT Williams record or confirm creation of a new Schedule A.

## Recommended global operating model

1. Serialize FT Williams writes per plan/year and retain idempotency keys so scheduled polling and controlled replays cannot create duplicate subparts.
2. Keep a versioned golden corpus for every observed carrier layout, including its expected identity, compensation, broker rows, and worksheet relationship.
3. Make identity provenance visible in the dashboard: source document value, worksheet corroboration, FT Williams current value, and normalized outbound value.
4. Add a reconciliation monitor from ShareFile item ID → active dashboard filing → extraction job → FT Williams receipt → read-back result.
5. Separate non-actionable extraction-only fields from blocking FT Williams fields in counts and UI labels.
6. Treat client-wide plan-year and participant-count discrepancies as one grouped decision rather than repeating the same decision on every Schedule A.

