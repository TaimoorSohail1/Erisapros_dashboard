import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const page = await readFile(new URL("../src/pages/FTWilliamsAgentSettingsPage.tsx", import.meta.url), "utf8");
const shell = await readFile(new URL("../src/ui/AppShell.tsx", import.meta.url), "utf8");
const guide = await readFile(new URL("../public/ftw-agent-setup-guide.html", import.meta.url), "utf8");

assert.match(shell, /to="\/settings\/ftw-agent"/, "The FT Williams Agent setup page must be reachable from the main navigation.");
assert.match(page, /createFTWLocalAgentPairingCode/, "Administrators must be able to generate one-time pairing codes from the dashboard.");
assert.match(page, /Client workspace/, "A paired computer must be assigned to the current client workspace.");
assert.match(page, /listFTWClientWorkspaces/, "The settings page must load only the signed-in client's workspaces.");
assert.match(page, /listFTWWorkspacePlanMappings/, "The settings page must load plan mappings only for the selected workspace.");
assert.match(page, /Only a verified client, plan, year/, "The settings page must explain the verified routing gate.");
assert.match(page, /disableFTWWorkspacePlanMapping/, "Administrators must be able to disable a plan mapping.");
assert.match(page, /revokeFTWLocalAgentDevice/, "Administrators must be able to revoke a lost or replaced computer.");
assert.match(page, /Do not send the code by email or chat/, "The setup screen must warn users not to share the one-time pairing code.");
assert.match(page, /Connecting a computer does not enable automatic updates or sending/, "Pairing a computer must not imply unsafe automatic sending.");
assert.match(page, /parseApiDateTime/, "UTC API timestamps must be normalized before pairing expiry and display checks.");
assert.match(page, /Test connection/, "Clients must have one clear connection test after installation.");
assert.match(page, /testAgentConnection/, "The connection test must refresh the live agent status.");
assert.match(page, /ftw-agent-setup-guide\.html/, "The settings page must link to the one-page client setup guide.");
assert.match(page, /Download FTW Agent/, "Clients must be able to download the one-file setup directly from the dashboard.");
assert.match(page, /releases\/latest\/download\/ERISAProsFTWAgentSetup\.exe/, "The dashboard must use the stable latest-release installer URL.");
assert.match(page, /Windows encrypts it on this computer/, "The setup screen must explain local encrypted automatic login.");
assert.match(guide, /Download and open the FT Williams Agent/, "The client guide must explain installation.");
assert.match(guide, /Enter your one-time connection code/, "The client guide must explain pairing.");
assert.match(guide, /Test connection/, "The client guide must end with a connection test.");
assert.match(guide, /Login required/, "The client guide must explain safe login recovery.");
assert.match(guide, /only on ftwilliam\.com/, "The client guide must explain the automatic-login domain boundary.");

console.log("FT Williams Agent settings UI passed.");
