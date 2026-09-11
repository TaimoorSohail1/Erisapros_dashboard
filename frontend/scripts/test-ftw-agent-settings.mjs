import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const page = await readFile(new URL("../src/pages/FTWilliamsAgentSettingsPage.tsx", import.meta.url), "utf8");
const shell = await readFile(new URL("../src/ui/AppShell.tsx", import.meta.url), "utf8");

assert.match(shell, /to="\/settings\/ftw-agent"/, "The FT Williams Agent setup page must be reachable from the main navigation.");
assert.match(page, /createFTWLocalAgentPairingCode/, "Administrators must be able to generate one-time pairing codes from the dashboard.");
assert.match(page, /revokeFTWLocalAgentDevice/, "Administrators must be able to revoke a lost or replaced computer.");
assert.match(page, /Do not send the code by email or chat/, "The setup screen must warn users not to share the one-time pairing code.");
assert.match(page, /Connecting a computer does not enable automatic updates or sending/, "Pairing a computer must not imply unsafe automatic sending.");

console.log("FT Williams Agent settings UI passed.");
