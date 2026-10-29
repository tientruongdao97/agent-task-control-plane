from fastapi.testclient import TestClient

from agent_control_plane.api import create_app
from agent_control_plane.config import Settings


def test_health_does_not_require_auth(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_admin_dashboard_is_served(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/admin")

    assert response.status_code == 200
    assert "Agent Task Control Plane" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_admin_routes_require_valid_key(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        missing = client.get("/v1/tasks")
        invalid = client.get("/v1/tasks", headers={"X-API-Key": "wrong"})

    assert missing.status_code == 422
    assert invalid.status_code == 401


def test_create_and_read_task(settings: Settings) -> None:
    headers = {"X-API-Key": settings.admin_api_key}
    body = {
        "title": "Add regression test",
        "description": "Reproduce and protect the fixed behavior.",
        "repository": "example/backend",
        "acceptance_criteria": ["Test fails before fix", "Test passes after fix"],
    }
    with TestClient(create_app(settings)) as client:
        created = client.post("/v1/tasks", headers=headers, json=body)
        fetched = client.get(f"/v1/tasks/{created.json()['id']}", headers=headers)

    assert created.status_code == 201
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "open"
    assert fetched.json()["events"][0]["event_type"] == "created"
