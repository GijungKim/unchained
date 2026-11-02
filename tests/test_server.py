"""Server smoke tests: status, replay ingest, window, compare."""

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("UNCHAINED_DATA_DIR", "")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UNCHAINED_DATA_DIR", str(tmp_path / "data"))
    from unchained import server

    return TestClient(server.app)


def test_status_empty(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["records"] == 0
    assert body["whoop"]["running"] is False


def test_replay_then_window_and_compare(client):
    res = client.post("/api/replay")
    assert res.status_code == 200
    session = res.json()["session_id"]
    assert session.startswith("replay-")

    status = client.get("/api/status").json()
    assert status["records"] > 0
    assert "whoop-5" in status["devices"]

    window = client.get("/api/window", params={"minutes": 60 * 24 * 300}).json()
    assert window["summary"]["n"] > 0
    assert window["samples"]

    # replay lands in the live window (time-shifted), and repeats insert again
    live = client.get("/api/window", params={"minutes": 5}).json()
    assert live["summary"]["n"] > 0
    again = client.post("/api/replay").json()
    assert again["records"] > 0

    window = client.get("/api/window", params={"minutes": 60 * 24 * 300}).json()
    cmp = client.get("/api/compare", params={"minutes": 60 * 24 * 300}).json()
    assert cmp["whoop-5"]["n"] == window["summary"]["n"]


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Unchained" in res.text


def test_stream_start_stop(client, monkeypatch):
    import time as time_mod

    calls = []

    def fake_stream(seconds, on_records):
        calls.append(seconds)
        on_records(
            [
                {
                    "device_id": "whoop-5",
                    "metric": "heart_rate",
                    "timestamp": "2026-10-28T00:00:00.000Z",
                    "value": 70.0,
                    "unit": "bpm",
                    "quality": 1.0,
                    "method": "test",
                    "session_id": f"test-{len(calls)}",
                }
            ]
        )
        return f"test-{len(calls)}"

    monkeypatch.setattr("unchained.collect.stream_whoop_live", fake_stream)
    res = client.post("/api/collect/whoop", json={"seconds": 10, "loop": True})
    assert res.status_code == 200
    assert res.json()["status"] == "streaming"
    assert client.post("/api/collect/whoop", json={"seconds": 10}).status_code == 409
    deadline = time_mod.time() + 5
    while time_mod.time() < deadline:
        if client.get("/api/status").json()["whoop"]["windows_completed"] >= 2:
            break
    assert client.get("/api/status").json()["whoop"]["windows_completed"] >= 2
    assert client.post("/api/collect/whoop/stop").json()["status"] == "stopping"
    deadline = time_mod.time() + 5
    while client.get("/api/status").json()["whoop"]["running"] and time_mod.time() < deadline:
        time_mod.sleep(0.05)
    final = client.get("/api/status").json()["whoop"]
    assert final["running"] is False
    assert final["last_ok"] is not None


def test_discovery_retry_recovers(client, monkeypatch):
    import time as time_mod

    import unchained.server as server_mod

    attempts = []

    def flaky(seconds, on_records):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("error [discovery]: no WHOOP advertisements were seen")
        return "recovered-session"

    monkeypatch.setattr("unchained.collect.stream_whoop_live", flaky)
    monkeypatch.setattr(server_mod, "DISCOVERY_RETRY_DELAY", 0.05)
    client.post("/api/collect/whoop", json={"seconds": 10, "loop": False})
    deadline = time_mod.time() + 5
    while client.get("/api/status").json()["whoop"]["running"] and time_mod.time() < deadline:
        time_mod.sleep(0.05)
    final = client.get("/api/status").json()["whoop"]
    assert final["running"] is False
    assert final["session_id"] == "recovered-session"
    assert final["error"] is None
    assert len(attempts) == 3


def test_single_window_completes(client, monkeypatch):
    def fake_stream(seconds, on_records):
        on_records(
            [
                {
                    "device_id": "whoop-5",
                    "metric": "heart_rate",
                    "timestamp": "2026-10-31T00:00:00.000Z",
                    "value": 68.0,
                    "unit": "bpm",
                    "quality": 1.0,
                    "method": "test",
                    "session_id": "test-single",
                }
            ]
        )
        return "test-single"

    monkeypatch.setattr("unchained.collect.stream_whoop_live", fake_stream)
    res = client.post("/api/collect/whoop", json={"seconds": 10, "loop": False})
    assert res.json()["status"] == "started"
    import time as time_mod

    deadline = time_mod.time() + 5
    while client.get("/api/status").json()["whoop"]["running"] and time_mod.time() < deadline:
        time_mod.sleep(0.05)
    body = client.get("/api/status").json()
    assert body["whoop"]["running"] is False
    assert body["whoop"]["session_id"] == "test-single"
    assert body["whoop"]["windows_completed"] == 1
    assert body["devices"]["whoop-5"] == "2026-10-31T00:00:00.000Z"


def test_whoop_silent_error_surfaces_without_retry(client, monkeypatch):
    attempts = []

    def silent(seconds, on_records):
        attempts.append(1)
        raise RuntimeError("error [silent] connected but zero HRM notifications")

    monkeypatch.setattr("unchained.collect.stream_whoop_live", silent)
    client.post("/api/collect/whoop", json={"seconds": 10, "loop": False})
    import time as time_mod

    deadline = time_mod.time() + 5
    while client.get("/api/status").json()["whoop"]["running"] and time_mod.time() < deadline:
        time_mod.sleep(0.05)
    final = client.get("/api/status").json()["whoop"]
    assert final["running"] is False
    assert "error [silent]" in final["error"]
    assert len(attempts) == 1
