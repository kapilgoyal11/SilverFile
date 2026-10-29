"""Dashboard (Kushim review console) API tests. Owner: Nishitha.

Needs the dashboard extras (pip install -r dashboard/requirements.txt httpx);
skipped otherwise so the core suite runs unchanged. No API key needed.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SILVERFILE_OFFLINE", "1")
    from dashboard.app import create_app
    return TestClient(create_app(str(tmp_path / "sessions.sqlite")))


def start(client, sid, scenario, **extra):
    body = {"session_id": sid, "scenario": scenario, "ssn": "900-12-3456", **extra}
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_clean_run_lists_as_draft_ready_with_provenance(client):
    d = start(client, "clean", "clean_single.json", documents=["w2_1.png", "int_1.json"])
    assert d["status"] == "draft_ready"
    lines = {l["line"]: l for l in d["lines"]}
    assert lines["1z"]["kind"] == "document" and lines["1z"]["document"] == "w2_1"
    assert lines["9"]["kind"] == "computed" and lines["9"]["formula"]
    assert [s["id"] for s in client.get("/api/sessions").json()] == ["clean"]


def test_hitl_event_is_shown_and_resolved_from_the_dashboard(client):
    d = start(client, "hitl", "hitl_spouse_died.json", spouse_ssn="900-65-4321")
    assert d["status"] == "needs_review"
    assert d["interrupt"]["type"] == "human_review" and d["interrupt"]["escalations"]
    r = client.post("/api/sessions/hitl/resolve",
                    json={"reviewer": "Test Reviewer", "note": "MFJ confirmed", "action": "proceed"})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["status"] == "draft_ready"
    assert any(e.get("resolved") and e.get("resolved_by") == "Test Reviewer" for e in after["escalations"])


def test_resolving_selected_items_only(client):
    d = start(client, "advice", "hitl_advice.json", spouse_ssn="900-65-4321")
    first = d["interrupt"]["escalations"][0]
    r = client.post("/api/sessions/advice/resolve",
                    json={"reviewer": "Test Reviewer", "escalation_ids": [first.get("id") or first["reason"]]})
    assert r.status_code == 200, r.text


def test_stop_ends_the_session(client):
    start(client, "stopme", "hitl_exploitation.json")
    r = client.post("/api/sessions/stopme/resolve", json={"reviewer": "Test Reviewer", "action": "stop", "note": "x"})
    assert r.status_code == 200 and r.json()["status"] == "failed"


def test_only_test_ssns_are_accepted(client):
    r = client.post("/api/sessions", json={"session_id": "real", "scenario": "clean_single.json", "ssn": "123-45-6789"})
    assert r.status_code == 400


def test_cannot_resolve_a_session_that_is_not_waiting(client):
    start(client, "done", "clean_single.json", documents=["w2_1.png"])
    r = client.post("/api/sessions/done/resolve", json={"reviewer": "Test Reviewer"})
    assert r.status_code == 409


def test_console_page_and_fixtures_are_served(client):
    assert client.get("/").status_code == 200
    fx = client.get("/api/fixtures").json()
    assert any(s["file"] == "clean_single.json" for s in fx["scenarios"])
