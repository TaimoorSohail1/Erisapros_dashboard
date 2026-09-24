# Live automatic Schedule A test — 16 September 2026

## Result

**Partial pass: automatic Bring Forward verified for 3 of 4 clients; the complete straight-through update flow is not yet proven.**

The three processed filings correctly paused for review rather than sending uncertain data. These are acceptable safety outcomes, but two contain genuine extraction mistakes, not merely missing decisions. Advertising Council did not reach extraction during the observation window.

This was a production test using the existing **Test** ShareFile folders and HighlandTech FTW plans, targeting filing year **2025** (the test's current filing year, not calendar year 2026). Each target had prior-year 2024 Schedule A records and no 2025 Schedule A at baseline.

## Method and boundaries

1. Downloaded one existing Schedule A PDF per client and checked its source pages independently.
2. Recorded unique FTW plan identities and read-only 2024/2025 baselines.
3. Uploaded byte-identical, uniquely named `AUTO-QA-20260916-*` copies to the corresponding Test folders.
4. Observed automatic ShareFile discovery, package/worksheet pairing, EyeLevel/GroundX extraction, plan matching and local-agent jobs.
5. Read back FTW records to verify actual 2025 creation and preservation of 2024 data.

**No manual dashboard Query, Bring Forward, Retry, Send, approval or decision overrides were used.** FTW queries used for baseline and verification were read-only diagnostics; they did not create forms or advance the dashboard workflow. No product code or production configuration was changed for this test.

This is a four-client sample, one uploaded PDF per client. It does **not** certify ten-client load, all contracts in each plan, or complete field accuracy across every extracted field.

## Timing and automatic-agent outcome

Times below are measured elapsed durations, not estimates. Intake means upload completion to filing creation. Extraction is the package extraction job, including the automatically paired worksheet. Agent time includes queue wait, execution, completion and server verification.

| Client | Intake | Extraction | Agent queue → verified | Upload → verified | Actual FTW result |
|---|---:|---:|---:|---:|---|
| Titmouse | 5m 56s | 2m 43s | 47s | 9m 40s | 2025 records created; job VERIFIED; review pause |
| Boston Home | 1m 57s | 2m 38s | 3m 24s | 8m 25s | 2025 records created; job VERIFIED after automatic reclaim; review pause |
| Advertising Council | Not discovered after over 11 minutes | Not reached | Not reached | Not completed | 2025 Schedule A still absent |
| Rollease | 1m 47s | 2m 43s | 1m 18s | 6m 17s | 2025 records created; job VERIFIED; review pause |

The three extraction jobs ran concurrently. The local agent handled Bring Forward jobs sequentially. Its last claimed execution-to-completion times were approximately **6–7 seconds**; the larger measured delays were intake, extraction, queueing and verification/recovery, not just browser clicks.

FTW read-back confirmed **3 Titmouse, 6 Boston Home and 7 Rollease current-year records**. Bring Forward copies the plan's prior-year set, so these counts need not equal the number of PDFs uploaded. All four clients' **2024 Schedule A records remained unchanged** against the baseline comparison.

## Source accuracy checks

| Client | Independently checked source versus output | Finding |
|---|---|---|
| Titmouse | Carrier EIN, NAIC, policy, covered count, premium, commission, fees and real broker | Checked values matched. Empty “None” broker slots were ignored correctly. **Policy dates were wrong:** both became 03/12/2026, the cover/signature date; the PDF states policy period 01/2025–12/2025. Both dates were low-confidence and required review. |
| Boston Home | Carrier identifiers, contract, covered count, dates, scalar premium/commission/fees and broker table | Checked scalar values matched. **Broker table extraction confused premiums with commissions and commission amounts with fees.** NFP should have commission 5,759.98 and fees 0; normalized output had commission 43,831.51 and fees 5,759.98. Professional Pensions should have commission 0 and fees 2,191.50; output had commission 43,831.51. Organization reference numbers were also mistaken for organization codes in the structured provider output. Broker evidence/reconciliation checks blocked unsafe sending. |
| Advertising Council | Source PDF prepared and inspected | **No extraction output available.** Accuracy and provider duration cannot be reported for this client. |
| Rollease | Scanned PDF: carrier identifiers, policy, dates, premium, broker/address, commission and fees | Checked values matched, including premium 12,013.49 and broker commission 1,201.34. Covered-person count is not supplied by the source, so leaving it missing is correct. Some values still require confidence/review decisions; this is not an unconditional automatic-send pass. |

No overall “accuracy percentage” is claimed: not every field was independently scored, and the above errors prevent declaring extraction fully correct.

## Confirmed issues and limits

### 1. Intake relies on recovery polling in these observed cases

All three discovered QA items were indexed as `SHAREFILE_QUICK_POLL`, not webhook intake. Production recovery polling runs every five minutes, with idle scan duration around 43 seconds. The poll dispatch also waits for inline package extraction, which can occupy the worker's main message-consumption loop and increase intake latency.

Advertising Council's file exists in `Schedule A's/Cigna/Medical`, with a production upload subscription whose callback and token match the configured values. **Missing subscription is not the explanation.** The confirmed quick-scan predicate accepts the Schedule A folder but rejects the deeper `Cigna` and `Medical` folders. This leaves a recovery coverage gap when upload-event intake does not process an item. The precise reason that this upload's webhook did not produce intake is **not established** by the available logs. The broad deep scan is configured at a 12-hour interval, so it is not timely recovery for this scenario.

### 2. Boston Home needed automatic job recovery

Its first claim did not reach a completed/verified job state before the three-minute claim lease expired. The agent automatically reclaimed it at 08:02:02 UTC (attempt 2), completed at 08:02:08 and was verified at 08:02:25. This demonstrates recovery, but adds roughly three minutes. Available evidence does not prove whether the original delay was completion delivery, a transient error or another handshake issue.

### 3. Extraction needs stronger source-layout safeguards

Boston Home needs broker table-column/reference-code validation and Titmouse needs policy-period versus cover-date disambiguation. Existing safety checks prevented these questionable values from being automatically sent. They do not make the extracted values correct.

### 4. Automatic sending is disabled

The production worker has automatic Bring Forward enabled but **`FTW_AUTOMATION_AUTO_SEND_ENABLED=false`**. Consequently, successful review and Bring Forward do not prove automatic data updates. This setting was not changed. None of these test uploads produced a verified send of their extracted data.

## Recommended next work

1. Trace upload-event receipt through queue and filing creation, and extend quick recovery coverage to supported nested folders beneath Schedule A without scanning unrelated client trees.
2. Add extraction regression fixtures for these PDFs: correct broker column mapping, explicit organization-code evidence, policy-period evidence and scanned-source checks.
3. Instrument agent claim/execution/completion/verification failures so a stalled job gives a clear client-facing stage and recoverable error instead of unexplained waiting.
4. After the fixes, repeat the four-client automatic-only test; then run a ten-client batch, including multiple contracts for the same client, and verify isolation and duplicate prevention.
5. With explicit approval for the production sending policy, test an eligible clean filing through automatic Send and FTW read-back before declaring end-to-end straight-through processing ready.

## Test artifacts left in place

The four QA upload copies remain in the Test ShareFile folders. The automatic agent created 2025 Schedule A records for Titmouse, Boston Home and Rollease. Original source files and prior-year records were not deleted. No cleanup or rollback was performed.

Live test filings:

- [Titmouse](https://d3axcdlq9aydpw.cloudfront.net/filings/6aaa4b95035e7531a1ddca70)
- [Boston Home](https://d3axcdlq9aydpw.cloudfront.net/filings/6aaa4b8d035e7531a1ddca6d)
- [Rollease](https://d3axcdlq9aydpw.cloudfront.net/filings/6aaa4b86035e7531a1ddca6a)

Supporting sanitized snapshots, timings, source expectations and read-back results are stored locally in `tmp/automation_batch_20260916/`. Timing/status observations used here were taken through approximately 08:05 UTC (13:05 Asia/Karachi), with final read-back immediately afterward.
