# Unchained Health, powered by Muse

Your hardware, your data, your AI key. No vendor app, no vendor cloud, no subscription.

Unchained reads heart data directly from a WHOOP 5 you own over standard
Bluetooth LE Heart Rate Service, with no vendor app involved. It normalizes
that into one local store, and puts a Muse agent on top that fuses the stream,
explains signal quality honestly, and answers questions about your body with
its reasoning shown.

Built for the Meta Global AI Developer Hackathon (indie track).

**Hackathon provenance:** everything in this repo was built Oct 27 – Nov 2,
2026 for this event: the HRS collector, store, ingest, Muse agent,
dashboard, replay fixtures, tests, and demo video. No pre-existing code,
no private dependencies, no protocol research (see `docs/submission.md`).

## Architecture

```
WHOOP 5 ──BLE HRS──▶ collectors/whoop_hrs.py ─▶ ingest ─▶ SQLite store ─▶ Muse agent ─▶ dashboard + LAN API
                                                                             (fuse, judge, Q&A)
Replay fixture ─────────────────────────────────▶▶ (same path, no radio needed)
```

The collector speaks only the standard BLE Heart Rate Service (0x180D),
the same open profile chest straps and watches have used for a decade. The
strap's Heart Rate Broadcast toggle (a built-in vendor feature) is the only
setup; after that, any host can subscribe with no pairing and no app.

## Collector contract (v1)

The collector emits timestamped records:

```json
{"device": "whoop-5", "metric": "heart_rate", "timestamp": "2026-11-02T12:00:03.123Z",
 "value": 72.0, "unit": "bpm", "quality": 1.0, "method": "whoop5-hrs-broadcast"}
```

- `quality` is 0..1 signal confidence, always present. HRS transmits only
  device-computed readings, so presence implies device confidence (1.0);
  samples flagged contact-lost score 0.0 and are shown as gaps, never
  interpolated.
- `method` names the exact decode path so the agent can reason about it.
- When the broadcast carries RR intervals they land in `ibi_interval`
  (ms), backdated so each interval ends at arrival; the dashboard derives
  HRV RMSSD and the rhythm strip from them. (Whoop 5's broadcast sends HR
  only, so live data shows HR + trend while the replay fixture, which
  carries RR, demonstrates HRV + rhythm.)

## Quickstart

```bash
cp .env.example .env        # add MODEL_API_KEY (dev.meta.ai)
uv sync                     # or: python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/unchained ingest --fixture fixtures/synthetic-whoop.json
.venv/bin/unchained serve   # dashboard at http://127.0.0.1:8080
```

Live collection (needs a strap with Heart Rate Broadcast enabled, worn snugly;
HRS only transmits while reading a pulse):

- Dashboard: the Start stream button, or
  `POST /api/collect/whoop {"seconds": 60, "loop": true}`.

Real biometric captures stay out of git. Only synthetic fixtures ship.

## Muse agent

The agent only sees the local store's quality-annotated windows. Deterministic
fusion (quality-weighted mean of device medians) runs first; Muse judges the
proposal, calls out discrepancies, and answers free-text questions with sample
counts cited.

```bash
cp .env.example .env        # add MODEL_API_KEY from https://dev.meta.ai
.venv/bin/unchained reconcile --minutes 10
.venv/bin/unchained ask "any spikes in the last 10 minutes?"
```

Same agent backs the dashboard's Muse panel and `POST /api/agent/reconcile`
+ `POST /api/agent/ask`. Default model is `muse-spark-1.3-contributor`
(override with `MUSE_MODEL`); Contributor stretches hackathon credits in
exchange for training-use consent (see https://dev.meta.ai/docs/pricing-rate-limits).

## Layout

- `src/unchained/`: collector, store, ingest, agent, server, CLI
- `fixtures/`: synthetic replay sessions (no real biometrics, ever)
- `docs/`: submission package
- `demo/`: live demo takes (local only; the submission links the upload)
- `duo/`: (stretch) Duo two-pane camera app (fresh Expo app, later)
