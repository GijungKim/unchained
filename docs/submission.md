# Submission package — Unchained Health (indie track)

Due: **Tue Nov 4, 9:00 PM ET.** This file is the copy-paste source for the
submission form. Bracketed items need the human (account actions).

## Title

Unchained Health

## Tagline

Your hardware, your data, your AI key — your WHOOP streamed straight to your
Mac and watched over by Muse. No vendor app, no cloud, no subscription.

## Description (long)

Your WHOOP knows every heartbeat — but the data lives in someone else's
cloud, behind someone else's subscription, visible only through their app.
Unchained Health cuts the cord: it reads a WHOOP 5 directly over standard
Bluetooth Heart Rate Service into a local store on your Mac, then puts a
Muse agent on top that fuses the stream, judges signal quality honestly,
and answers plain-language questions about your body.

- **Bring your own hardware.** Live heart rate streams straight from a
  strap you own using the open BLE HRS profile — the only setup is the
  strap's built-in Heart Rate Broadcast toggle. No vendor app open, no
  cloud round-trip, no subscription check. A replay mode runs the whole
  demo with no radio at all.
- **Honest signal.** Optical sensors drop out, so every sample carries 0–1
  quality: the dashboard separates valid beats from contact-loss gaps and
  shows gaps as gaps. Where RR intervals are present (replay fixture) it
  derives HRV RMSSD from measured intervals and draws a rhythm strip from
  real beats — and where the strap sends HR only, it says so instead of
  inventing a trace.
- **Bring your own Muse key.** The agent runs on your Meta API key against
  your local data only. It opens with a deterministic quality-weighted
  fusion, then Muse judges the proposal — calling out spread-vs-trust
  mismatches and dropout rates — and answers questions like "any spikes in
  the last 10 minutes?" citing sample counts. When the signal is shaky it
  says "treat as tentative" instead of inventing certainty.
- **One screen.** A local dashboard shows live BPM, the beat chart, and
  the Muse verdict side by side — plus HRV and the rhythm strip whenever
  RR intervals are present.

## How we used Meta AI tooling

The product's intelligence is a Muse agent (default `muse-spark-1.3`,
override via `MUSE_MODEL`) called through the Meta Model API. It does three
things beyond a basic integration: (1) fuses each window with per-source
trust notes instead of a blind average; (2) audits its own deterministic
proposal — during development it caught and overruled a naive fusion,
which we then fixed; (3) answers free-text health questions grounded only
in the local window, refusing to guess past the numbers. Muse never sees
raw Bluetooth or any vendor cloud — only quality-annotated local data.

## Architecture (for the technical-depth reader)

```
WHOOP 5 ──BLE HRS──▶ collectors/whoop_hrs.py ─▶ ingest ─▶ SQLite ─▶ Muse agent ─▶ dashboard + LAN API
                                                                              (fuse · judge · Q&A)
```

The submission repo holds the collector, contract, store, agent,
dashboard, synthetic fixtures, and tests (26 passing) — the whole system,
no private dependencies.

## Links

- Repo (REQUIRED — rules say repos must be public): [push `unchained/` to GitHub, paste URL]
- Demo video (live): [record the Whoop take, upload unlisted, paste URL]
- X cut: [post the take or a trimmed cut, paste URL]

## Built during the hackathon (rules require this disclosure)

Everything in the repo was built Oct 27 – Nov 2, 2026 for this event. No
pre-existing code is used or highlighted: the collector speaks only the
standard, decades-old BLE Heart Rate Service profile — no protocol
research, no vendor internals, nothing to reverse-engineer. All code,
fonts (OFL), fixtures (synthetic), and demo footage are owned or
licensed; no third-party code is vendored. The device name (WHOOP) appears
nominatively to describe hardware compatibility only.

(Note: an earlier ring-themed animated explainer was cut with a scope
change and is NOT part of the submission; it stays local as a portfolio
piece. The demo video is the live Whoop take only.)

## Demo script (live backup if video fails)

1. Open the dashboard (replay loaded: HRV + rhythm alive from RR). (0:00–0:20)
2. Start stream wearing the strap; watch live BPM + trend take over. (0:20–1:00)
3. Point at the honest states: HR-only tiles on live, measured RMSSD on replay. (1:00–1:20)
4. Click Fuse sources: read the verdict + trust notes aloud. (1:20–2:00)
5. Ask "any spikes in the last 10 minutes?": read the cited answer. (2:00–2:30)
6. Close: "your hardware, your data, your AI key." (2:30)

## Human checklist

- [ ] Record the Whoop live take (strap on wrist, 60s hands-off)
- [ ] Upload the take unlisted; paste URL above
- [ ] Post the X cut
- [ ] Push `unchained/` to a PUBLIC GitHub repo (required by the rules).
      `git init && git add -A && git status` (review!), then commit + push.
- [ ] Fill the Cerebral Valley form from this file; double-check the track
      (indie) and the Nov 4 9pm ET deadline in the form itself
- [ ] Rotate the Meta API key after submitting (it lived in `.env` during dev)
