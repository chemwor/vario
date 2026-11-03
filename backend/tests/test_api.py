"""HTTP surface: health, setup state, version, and the confidence floor end to end (no classifier is reached)."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)          # no `with`: the background loops (watcher, triage) don't start


def test_health_and_version():
    assert client.get("/api/health").status_code == 200
    v = client.get("/api/version").json()
    assert "version" in v


def test_setup_state_is_first_run_in_an_empty_home():
    s = client.get("/api/setup").json()
    assert s["first_run"] is True
    assert set(s["steps"]) == {"deps", "models", "hooks", "sound", "mic", "prefs"}


def test_low_confidence_utterance_is_rejected_not_dispatched():
    r = client.post("/api/voice/utterance", json={"text": "approve", "source": "mic", "confidence": 0.12}).json()
    assert r["mode"] == "rejected" and r["rejected"]
    row = [c for c in client.get("/api/comms").json()["entries"] if c["id"] == r["comms_id"]][0]
    assert row["reason"] == "rejected-low-conf"
