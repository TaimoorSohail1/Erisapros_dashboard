# FTW Agent 0.4.1 public release and desktop verification

Date: 2026-09-18. User explicitly approved unsigned publication and the desktop update. Automatic sending and other client computers were excluded.

## Published artifact

- Latest release: https://github.com/TaimoorSohail1/Erisapros_dashboard/releases/tag/ftw-agent-v0.4.1
- Existing dashboard download URL now resolves to 0.4.1: https://github.com/TaimoorSohail1/Erisapros_dashboard/releases/latest/download/ERISAProsFTWAgentSetup.exe
- Actual full public download verified: 398,726,637 bytes; SHA256 `3327C51C5DB975C2AF1CA175DCB5BDCBD05428264292545CCBE07295AB4C2944`.
- Published assets include the executable, release manifest and CLIENT-INSTRUCTIONS.md. Signature status is NotSigned; no signing claim or security-policy bypass.
- Release target commit is baseline `dd9f74941bb57f4ad0504d32001f31cdb4279298`. The packaged binary includes working-tree patches; this tag does not claim every bundled patch is committed. The approved checksum identifies the tested artifact. Unrelated user work was not committed or reverted.

## Desktop update

DESKTOP-D9JV7IA had been reconnected by the user on 0.3.2, device ID `6aad38d03b4194c40d42503d`. Primary-consistent checks found no active reservation, associated jobs, eligible pending jobs, expired pending jobs or claimed jobs before the upgrade and each live resume.

An atomic idle-only migration hold blocked new claims. The public downloaded package created a verified backup before the legacy stop. Only verified idle child PID 16732, with exact installed executable path and parent 11536, was stopped. No broad image-name kill was used.

The first replacement encountered WinError 5 during Windows executable teardown. It failed without replacing the old executable; old checksum remained intact, the drain marker was removed by normal error handling, and the server hold remained. After confirming all installed agent processes had exited, the updater was retried successfully. This is a remaining legacy-upgrade operational caveat, not a claim that first-attempt legacy updates always succeed.

Successful backup: `C:/Users/Hp/AppData/Local/ERISAPros/FTWLocalAgentBackups/update-b52707549ef84919b32d86285210c943`.

Installed checksum matches the public artifact. Device credential, protected FTW login credential and startup launcher remain byte-identical to the verified backup; browser profile remains present. The same device ID reported 0.4.1 and acknowledged PAUSED before live controls were exercised. No disconnect, re-pairing or credential disclosure was needed.

## Live controls

- Production UI showed 0.4.1 and an enabled Resume button.
- First live Resume: CONNECTED, browser_ready=true, pause_requested=false at 13:23:44.523 UTC. Agent-owned dedicated-profile browser processes were present.
- Live Pause: PAUSED, browser_ready=false, pause_requested=true at 13:23:58.209 UTC. Dedicated-profile browser process count was zero. All 15 baseline personal Chrome processes remained running.
- Final live Resume: CONNECTED, browser_ready=true, pause_requested=false at 13:24:25.272 UTC. Desktop left resumed and ready, with no active or pending jobs.
- Other device EP-PF43NS4S remained unchanged on 0.3.2, with the same last-seen timestamp. Its disabled controls were not altered or bypassed.
- Automatic sending stayed disabled. No new filing upload, vendor write or automatic sending test was initiated in this release verification.

## Automated verification

- Full backend suite: 795 passed, 2 skipped, 64 subtests passed; one GroundX SDK deprecation warning.
- Focused agent source checks: 72 passed.
- Packaged synthetic smoke: 0.4.1 version, embedded source match, isolated update, DPAPI preservation, profile preservation and packaged rollback passed. No real credential/vendor request in this harness.
- Frontend agent UI and review UI checks passed.

## Client rollout and limits

Existing connected clients must use the connection-preserving update instructions, not Disconnect and normal re-pairing. Legacy 0.3.2 updates need a confirmed idle maintenance hold/controlled stop. The unsigned package may trigger Windows or organization warnings; no protection should be disabled. Pilot one explicitly approved client computer before broader adoption.

This run verifies public delivery, desktop update and idle live controls. It does not certify fresh upload-to-extraction-to-FTW delivery, live mid-click pause, fresh password/MFA login, or personal-browser simultaneous FTW session behavior. Saved session restoration succeeded. The earlier automatic Bring Forward and pending-work reconciliation evidence remains in `ftw-agent-control-transport-release-2026-09-18.md`; it is not new evidence from this run. Real automatic sending remains deliberately deferred.
