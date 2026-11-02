"""Local dashboard server: live collection, store queries, replay, LAN API."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, collect, ingest_whoop, store

STATIC = Path(__file__).resolve().parent / "static"
FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def data_dir() -> Path:
    root = Path(os.environ.get("UNCHAINED_DATA_DIR", "~/.unchained")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db():
    return store.connect(data_dir() / "unchained.sqlite3")


app = FastAPI(title="Unchained")


@app.middleware("http")
async def _no_cache_dashboard(request, call_next):
    # The dashboard is a live instrument, not a document: never let the
    # browser serve stale HTML/JS from cache after an update.
    response = await call_next(request)
    if request.url.path in ("/", "/index.html", "/app.js", "/styles.css"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _fresh_device_state() -> dict:
    return {
        "running": False,
        "streaming": False,
        "session_id": None,
        "started_at": None,
        "seconds": 0,
        "windows_completed": 0,
        "error": None,
        "last_ok": None,
    }


_state = {"whoop": _fresh_device_state()}
_lock = threading.Lock()
_stop = threading.Event()


class CollectRequest(BaseModel):
    seconds: int = 60
    loop: bool = False


class AgentWindowRequest(BaseModel):
    minutes: int = 10


class AskRequest(BaseModel):
    question: str
    minutes: int = 10


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@app.get("/api/status")
def status():
    conn = _db()
    with _lock:
        whoop = dict(_state["whoop"])
    n = conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()["c"]
    devices = conn.execute(
        "SELECT device_id, MAX(timestamp) AS last FROM records GROUP BY device_id"
    ).fetchall()
    return {
        "records": n,
        "devices": {r["device_id"]: r["last"] for r in devices},
        "whoop": whoop,
    }


DISCOVERY_RETRIES = 3
DISCOVERY_RETRY_DELAY = 10


# Per-device streaming config. The stream function is resolved from the collect
# module at call time so tests can monkeypatch it.
_STREAMS = {
    "whoop": {
        "stream_fn": "stream_whoop_live",
        "retry_msg": "strap not advertising (retry {attempt}/{total})…",
        "final_error": "strap not advertising after retries — tap it awake and restart",
    },
}


def _stream_loop(kind: str, seconds: int, loop: bool):
    cfg = _STREAMS[kind]

    def on_records(records: list[dict]):
        conn = _db()
        store.insert_records(conn, records)
        with _lock:
            _state[kind]["last_ok"] = _iso_now()

    def run_window() -> str:
        return getattr(collect, cfg["stream_fn"])(seconds, on_records)

    try:
        while True:
            with _lock:
                _state[kind].update(started_at=_iso_now(), seconds=seconds, error=None)
            try:
                session_id = run_window()
            except RuntimeError as exc:
                # wearables sleep between windows; retry discovery a few times
                # so a tap-to-wake rescues the stream instead of killing it.
                # Anything else (e.g. a silent but connected strap) surfaces
                # immediately with the collector's own guidance.
                if "error [discovery]" not in str(exc):
                    raise
                recovered = False
                for attempt in range(1, DISCOVERY_RETRIES + 1):
                    with _lock:
                        _state[kind]["error"] = cfg["retry_msg"].format(
                            attempt=attempt, total=DISCOVERY_RETRIES
                        )
                    _stop.wait(DISCOVERY_RETRY_DELAY)
                    if _stop.is_set():
                        break
                    try:
                        with _lock:
                            _state[kind].update(started_at=_iso_now(), error=None)
                        session_id = run_window()
                        recovered = True
                        break
                    except RuntimeError:
                        continue
                if not recovered:
                    raise RuntimeError(cfg["final_error"]) from exc
            with _lock:
                st = _state[kind]
                st.update(session_id=session_id, windows_completed=st["windows_completed"] + 1)
            if not loop or _stop.is_set():
                break
    except Exception as exc:  # noqa: BLE001 - surfaced to the status endpoint
        with _lock:
            _state[kind]["error"] = str(exc)[:300]
    finally:
        with _lock:
            _state[kind].update(running=False, streaming=False)


def _start_collection(kind: str, req: CollectRequest) -> dict:
    if not 10 <= req.seconds <= 120:
        raise HTTPException(400, "seconds must be 10..120")
    with _lock:
        if _state[kind]["running"]:
            raise HTTPException(409, "collection already running")
        _state[kind].update(
            running=True,
            streaming=req.loop,
            started_at=_iso_now(),
            seconds=req.seconds,
            error=None,
            session_id=None,
            windows_completed=0,
        )
    _stop.clear()
    thread = threading.Thread(target=_stream_loop, args=(kind, req.seconds, req.loop), daemon=True)
    thread.start()
    return {"status": "streaming" if req.loop else "started", "seconds": req.seconds}


def _stop_collection(kind: str) -> dict:
    with _lock:
        running = _state[kind]["running"]
    _stop.set()
    return {"status": "stopping" if running else "idle"}


@app.post("/api/collect/whoop")
def collect_whoop(req: CollectRequest):
    return _start_collection("whoop", req)


@app.post("/api/collect/whoop/stop")
def stop_whoop():
    return _stop_collection("whoop")


@app.post("/api/replay")
def replay():
    """Ingest the synthetic fixture as a fresh session, time-shifted to now.

    No radio needed. Shifting means every replay lands in the live chart
    window instead of deduping against the fixture's fixed timestamps.
    """
    fixture = FIXTURES / "synthetic-whoop.json"
    if not fixture.exists():
        raise HTTPException(500, "synthetic fixture missing")
    session_id = f"replay-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
    records = ingest_whoop.parse_file(fixture, session_id=session_id)
    latest = max(datetime.fromisoformat(r["timestamp"]) for r in records)
    shift = (datetime.now(UTC) - latest).total_seconds()
    for r in records:
        ts = datetime.fromisoformat(r["timestamp"])
        shifted = ts + timedelta(seconds=shift)
        r["timestamp"] = shifted.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    conn = _db()
    n = store.insert_records(conn, records)
    with _lock:
        _state["whoop"].update(last_ok=_iso_now(), session_id=session_id)
    return {"status": "ok", "session_id": session_id, "records": n}


@app.get("/api/window")
def window(
    device: str = "whoop-5",
    metric: str = "heart_rate",
    minutes: int = 10,
    min_quality: float = 0.0,
):
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)

    def fmt(dt):
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    conn = _db()
    samples = store.window(conn, device, metric, fmt(start), fmt(end), min_quality)
    return {"samples": samples, "summary": store.summarize(samples)}


@app.get("/api/compare")
def compare(metric: str = "heart_rate", minutes: int = 10, min_quality: float = 0.0):
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)

    def fmt(dt):
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    conn = _db()
    return store.compare(conn, metric, fmt(start), fmt(end), min_quality)


@app.post("/api/agent/reconcile")
def agent_reconcile(req: AgentWindowRequest):
    try:
        return agent.reconcile(_db(), minutes=req.minutes)
    except agent.AgentError as exc:
        raise HTTPException(503, str(exc)[:300]) from exc


@app.post("/api/agent/ask")
def agent_ask(req: AskRequest):
    try:
        return agent.ask(_db(), req.question, minutes=req.minutes)
    except agent.AgentError as exc:
        raise HTTPException(503, str(exc)[:300]) from exc


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
