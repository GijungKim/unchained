"""Agent tests: fusion math, prompt plumbing, and failure modes (no network)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from unchained import agent, store


def _seed(conn, device="whoop-5", n=12, bpm=72.0, raw_extra=20):
    now = datetime.now(UTC)
    records = []
    for i in range(n):
        ts = (now - timedelta(seconds=i * 5)).isoformat().replace("+00:00", "Z")
        records.append(
            {
                "device_id": device,
                "metric": "heart_rate",
                "timestamp": ts,
                "value": bpm + (i % 3),
                "unit": "bpm",
                "quality": 0.9,
                "method": "test",
                "session_id": "s1",
            }
        )
    for i in range(raw_extra):
        ts = (now - timedelta(seconds=i * 5)).isoformat().replace("+00:00", "Z")
        records.append(
            {
                "device_id": device,
                "metric": "heart_rate_raw",
                "timestamp": ts,
                "value": 400.0,
                "unit": "bpm",
                "quality": 0.0,
                "method": "test-raw",
                "session_id": "s1",
            }
        )
    store.insert_records(conn, records)


@pytest.fixture()
def conn(tmp_path):
    return store.connect(tmp_path / "test.sqlite3")


@pytest.fixture()
def keyed(monkeypatch):
    monkeypatch.setattr(agent, "load_dotenv", lambda path=None: None)
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.delenv("MUSE_MODEL", raising=False)
    monkeypatch.delenv("MUSE_BASE_URL", raising=False)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _fake_post(reply, seen=None):
    def post(url, headers=None, json=None, timeout=None):
        if seen is not None:
            seen.update({"url": url, "headers": headers, "json": json})
        return FakeResponse({"choices": [{"message": {"content": reply}}]})

    return post


def test_deterministic_fuse_weights_quality(conn):
    _seed(conn, device="whoop-5", n=10, bpm=70.0)
    _seed(conn, device="duo-camera", n=10, bpm=90.0)
    # degrade the camera so the strap wins the weighting
    conn.execute("UPDATE records SET quality = 0.1 WHERE device_id = 'duo-camera'")
    ctx = agent.build_context(conn, minutes=10)
    proposal = agent.deterministic_fuse(ctx)
    # weighted mean: strap ~71 @ w=9 vs degraded camera ~91 @ w=1 -> ~73
    assert proposal["fused_bpm"] == pytest.approx(73.0, abs=2.0)
    assert proposal["weights"]["whoop-5"] > proposal["weights"]["duo-camera"]
    assert ctx["sources"]["whoop-5"]["validity_rate"] < 1.0


def test_reconcile_parses_fenced_json(conn, keyed, monkeypatch):
    _seed(conn)
    seen: dict = {}
    reply = '```json\n{"fused_bpm": 72.5, "confidence": 0.9, "verdict": "Steady.", "per_source": {}, "discrepancies": []}\n```'
    monkeypatch.setattr(agent.httpx, "post", _fake_post(reply, seen))
    out = agent.reconcile(conn, minutes=10)
    assert out["judgment"]["fused_bpm"] == 72.5
    assert out["judgment"]["verdict"] == "Steady."
    assert out["proposal"]["fused_bpm"] == pytest.approx(73.0, abs=2.0)
    assert seen["url"] == "https://api.meta.ai/v1/chat/completions"
    assert seen["json"]["model"] == "muse-spark-1.3-contributor"
    assert seen["headers"]["Authorization"] == "Bearer test-key"


def test_reconcile_falls_back_to_raw_verdict(conn, keyed, monkeypatch):
    _seed(conn)
    monkeypatch.setattr(agent.httpx, "post", _fake_post("just some prose, no json"))
    out = agent.reconcile(conn, minutes=10)
    assert out["judgment"]["verdict"] == "just some prose, no json"
    assert out["judgment"]["structured"] is False


def test_reconcile_empty_window_skips_api(conn, keyed, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("API must not be called with no data")

    monkeypatch.setattr(agent.httpx, "post", boom)
    out = agent.reconcile(conn, minutes=10)
    assert out["proposal"] is None
    assert out["judgment"]["fused_bpm"] is None


def test_ask_returns_answer(conn, keyed, monkeypatch):
    _seed(conn)
    monkeypatch.setattr(agent.httpx, "post", _fake_post("Your pulse is steady."))
    out = agent.ask(conn, "How am I doing?")
    assert out["answer"] == "Your pulse is steady."
    assert out["question"] == "How am I doing?"
    assert out["sources"]["whoop-5"]["n"] == 12


def test_ask_rejects_empty_question(conn, keyed):
    with pytest.raises(agent.AgentError, match="non-empty"):
        agent.ask(conn, "   ")


def test_missing_key_names_env_var(conn, monkeypatch):
    monkeypatch.setattr(agent, "load_dotenv", lambda path=None: None)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(agent.AgentError, match="MODEL_API_KEY"):
        agent.ask(conn, "hi")


def test_api_error_never_leaks_key(conn, keyed, monkeypatch):
    def fail(url, headers=None, json=None, timeout=None):
        return FakeResponse(status_code=401, text="bad key")

    monkeypatch.setattr(agent.httpx, "post", fail)
    with pytest.raises(agent.AgentError) as excinfo:
        agent.ask(conn, "hi")
    assert "401" in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)
