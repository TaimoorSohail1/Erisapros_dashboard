from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_production_build_requires_and_verifies_a_code_signature():
    script = (ROOT / "scripts" / "build_ftw_local_agent.ps1").read_text(encoding="utf-8")

    assert "$RequireSignature" in script
    assert "signtool.exe" in script
    assert "verify /pa" in script
    assert "release-manifest.json" in script
    assert "Get-FileHash" in script
    assert "--name ERISAProsFTWAgentSetup" in script


def test_installer_checks_release_integrity_and_uses_per_user_storage():
    script = (ROOT / "scripts" / "install_ftw_local_agent.ps1").read_text(encoding="utf-8")

    assert "Get-AuthenticodeSignature" in script
    assert "$ExpectedSha256" in script
    assert "$env:LOCALAPPDATA" in script
    assert "RunLevel Limited" in script
    assert "device.credential" in script


def test_client_installer_needs_only_the_one_time_pairing_code():
    script = (ROOT / "scripts" / "install_ftw_local_agent.ps1").read_text(encoding="utf-8")

    assert '[string]$AgentExecutable = ""' in script
    assert '[string]$ServerUrl = "https://d3axcdlq9aydpw.cloudfront.net"' in script
    assert '[string]$PairingCode = ""' in script
    assert 'Join-Path $PSScriptRoot "ERISAProsFTWAgentSetup.exe"' in script
    assert 'Read-Host "Enter the one-time connection code from ERISAPros"' in script
    assert "Open ERISAPros and click Test connection" in script


def test_uninstaller_revokes_the_device_before_removing_local_state():
    script = (ROOT / "scripts" / "uninstall_ftw_local_agent.ps1").read_text(encoding="utf-8")

    assert "unpair --credential-file" in script
    assert "Unregister-ScheduledTask" in script
    assert script.index("unpair --credential-file") < script.index("Remove-Item")
