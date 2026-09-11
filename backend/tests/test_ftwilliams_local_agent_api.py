import asyncio

from fastapi.testclient import TestClient

import app.repositories as repositories
from app.main import app
from app.config import get_settings
from app.models import FTWClientWorkspace, FTWLocalAgentDevice


def test_local_agent_pairing_heartbeat_and_revocation_do_not_expose_device_tokens():
    repo = repositories.MemoryRepository()
    repositories._repository = repo
    client = TestClient(app)
    try:
        pairing_response = client.post("/api/ftwilliams/local-agent/pairing-codes")
        assert pairing_response.status_code == 200
        pairing_code = pairing_response.json()["pairing_code"]

        pair_response = client.post(
            "/api/ftwilliams/local-agent/pair",
            json={
                "pairing_code": pairing_code,
                "device_name": "Benefits workstation",
                "agent_version": "0.1.0",
            },
        )
        assert pair_response.status_code == 200
        device_id = pair_response.json()["device_id"]
        token = pair_response.json()["device_token"]
        assert pair_response.json()["expected_account"] == "HighlandTech"

        assert client.post("/api/ftwilliams/local-agent/agent/heartbeat", json={}).status_code == 401
        heartbeat = client.post(
            "/api/ftwilliams/local-agent/agent/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_version": "0.1.0", "browser_ready": True},
        )
        assert heartbeat.status_code == 200

        devices = client.get("/api/ftwilliams/local-agent/devices")
        assert devices.status_code == 200
        assert devices.json()["devices"][0]["id"] == device_id
        assert "token_hash" not in devices.json()["devices"][0]

        revoked = client.post(f"/api/ftwilliams/local-agent/devices/{device_id}/revoke")
        assert revoked.status_code == 200
        rejected = client.post(
            "/api/ftwilliams/local-agent/agent/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
            json={"agent_version": "0.1.0", "browser_ready": True},
        )
        assert rejected.status_code == 401
    finally:
        repositories._repository = None


def test_pairing_code_cannot_be_reused_through_the_api():
    repositories._repository = repositories.MemoryRepository()
    client = TestClient(app)
    try:
        code = client.post("/api/ftwilliams/local-agent/pairing-codes").json()["pairing_code"]
        payload = {
            "pairing_code": code,
            "device_name": "Benefits workstation",
            "agent_version": "0.1.0",
        }
        assert client.post("/api/ftwilliams/local-agent/pair", json=payload).status_code == 200
        second = client.post("/api/ftwilliams/local-agent/pair", json=payload)
        assert second.status_code == 400
        assert "already used" in second.json()["detail"]
    finally:
        repositories._repository = None


def test_local_agent_status_and_device_list_are_scoped_to_pairing_owner():
    repo = repositories.MemoryRepository()
    repositories._repository = repo
    client = TestClient(app)
    try:
        first_code = client.post("/api/ftwilliams/local-agent/pairing-codes").json()["pairing_code"]
        first = client.post(
            "/api/ftwilliams/local-agent/pair",
            json={"pairing_code": first_code, "device_name": "Owned computer", "agent_version": "0.1.0"},
        )
        assert first.status_code == 200

        # An administrator cannot manage a device that was paired by a different
        # account. This protects future client self-service screens from showing
        # or revoking another client's local computer.
        other_device = asyncio.run(repo.create_ftw_local_agent_device(FTWLocalAgentDevice(
            name="Other client computer",
            token_hash="other-token-hash",
            token_prefix="other-",
            expected_account="AnotherAccount",
            paired_by="another-client@example.com",
        )))

        devices = client.get("/api/ftwilliams/local-agent/devices")
        assert devices.status_code == 200
        assert [item["name"] for item in devices.json()["devices"]] == ["Owned computer"]
        assert client.post(f"/api/ftwilliams/local-agent/devices/{other_device.id}/revoke").status_code == 404
    finally:
        repositories._repository = None


def test_client_workspaces_have_unique_slugs_for_future_agent_routing():
    repositories._repository = repositories.MemoryRepository()
    client = TestClient(app)
    try:
        response = client.post(
            "/api/ftwilliams/local-agent/workspaces",
            json={
                "name": "HighlandTech Demo",
                "slug": "highlandtech-demo",
                "expected_account": "HighlandTech",
                "admin_subjects": ["demo-admin@example.com"],
            },
        )
        assert response.status_code == 200
        assert response.json()["slug"] == "highlandtech-demo"
        assert client.get("/api/ftwilliams/local-agent/workspaces").json()["workspaces"][0]["name"] == "HighlandTech Demo"
        duplicate = client.post(
            "/api/ftwilliams/local-agent/workspaces",
            json={"name": "Duplicate", "slug": "highlandtech-demo", "expected_account": "HighlandTech"},
        )
        assert duplicate.status_code == 409
    finally:
        repositories._repository = None


def test_workspace_pairing_code_binds_the_device_to_only_that_client_workspace():
    repo = repositories.MemoryRepository()
    repositories._repository = repo
    client = TestClient(app)
    try:
        first_workspace = client.post(
            "/api/ftwilliams/local-agent/workspaces",
            json={"name": "Client A", "slug": "client-a", "expected_account": "ClientA"},
        )
        assert first_workspace.status_code == 200
        workspace_id = first_workspace.json()["id"]

        code_response = client.post(
            "/api/ftwilliams/local-agent/pairing-codes",
            json={"workspace_id": workspace_id},
        )
        assert code_response.status_code == 200
        assert code_response.json()["workspace_id"] == workspace_id

        paired = client.post(
            "/api/ftwilliams/local-agent/pair",
            json={
                "pairing_code": code_response.json()["pairing_code"],
                "device_name": "Client A computer",
                "agent_version": "0.1.0",
            },
        )
        assert paired.status_code == 200
        assert paired.json()["expected_account"] == "ClientA"
        assert paired.json()["workspace_id"] == workspace_id

        devices = client.get("/api/ftwilliams/local-agent/devices").json()["devices"]
        assert devices[0]["workspace_id"] == workspace_id

        other_workspace = asyncio.run(repo.create_ftw_client_workspace(FTWClientWorkspace(
            name="Client B",
            slug="client-b",
            expected_account="ClientB",
            admin_subjects=["client-b-admin@example.com"],
        )))
        # Client A's dashboard does not discover Client B and cannot mint a code
        # that would attach a computer to Client B.
        visible_workspaces = client.get("/api/ftwilliams/local-agent/workspaces").json()["workspaces"]
        assert [item["id"] for item in visible_workspaces] == [workspace_id]
        assert client.post(
            "/api/ftwilliams/local-agent/pairing-codes",
            json={"workspace_id": other_workspace.id},
        ).status_code == 404
    finally:
        repositories._repository = None


def test_workspace_routing_rejects_unscoped_pairing_codes_when_enabled(monkeypatch):
    repositories._repository = repositories.MemoryRepository()
    monkeypatch.setenv("FTW_LOCAL_AGENT_WORKSPACE_ROUTING_ENABLED", "true")
    get_settings.cache_clear()
    client = TestClient(app)
    try:
        response = client.post("/api/ftwilliams/local-agent/pairing-codes")
        assert response.status_code == 400
        assert "workspace" in response.json()["detail"].lower()
    finally:
        repositories._repository = None
        get_settings.cache_clear()
