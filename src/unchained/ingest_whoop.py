"""Ingest Whoop 5 live samples (hrs_live.py --report JSON / --stream-frames).

Two metrics, mirroring the ring leg:
- heart_rate: the strap's computed bpm per notification.
- ibi_interval: RR intervals from the same notification, backdated so the
  last interval ends at arrival (each interval's end is known; its start is
  the previous interval's end).

Quality: HRS carries no per-sample signal quality. The strap only transmits
device-computed readings, so presence implies device confidence: quality 1.0,
except 0.0 when the contact bit explicitly reports no skin contact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

DEVICE_ID = "whoop-5"
METHOD_HR = "whoop5-hrs-broadcast"
METHOD_RR = "whoop5-hrs-rr"


def _iso(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class SampleIngester:
    """Incremental HRS sample -> records."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id

    def feed(self, sample: dict) -> list[dict]:
        arrival: float = sample["hostArrivalTimestamp"]
        quality = 0.0 if sample.get("contact") is False else 1.0
        records: list[dict] = []
        hr = sample.get("hr")
        if isinstance(hr, (int, float)) and not isinstance(hr, bool) and 20 <= hr <= 300:
            records.append(
                {
                    "device_id": DEVICE_ID,
                    "metric": "heart_rate",
                    "timestamp": _iso(arrival),
                    "value": round(float(hr), 1),
                    "unit": "bpm",
                    "quality": quality,
                    "method": METHOD_HR,
                    "session_id": self.session_id,
                }
            )
        rr_list = sample.get("rrMs") or []
        # Walk backwards: the last interval ends at arrival.
        cursor = arrival
        for interval in reversed(rr_list):
            if not isinstance(interval, (int, float)) or not 1 <= interval <= 10000:
                continue
            cursor -= interval / 1000.0
            records.append(
                {
                    "device_id": DEVICE_ID,
                    "metric": "ibi_interval",
                    "timestamp": _iso(cursor),
                    "value": float(interval),
                    "unit": "ms",
                    "quality": quality,
                    "method": METHOD_RR,
                    "session_id": self.session_id,
                }
            )
        return records


def parse_report(report: dict, session_id: str | None = None) -> list[dict]:
    ingester = SampleIngester(session_id)
    out: list[dict] = []
    for sample in report.get("samples", []):
        out.extend(ingester.feed(sample))
    return out


def parse_file(path: Path, session_id: str | None = None) -> list[dict]:
    return parse_report(json.loads(path.read_text()), session_id or path.stem)


def parse_frame_line(line: str) -> dict | None:
    """Parse one `--stream-frames` stdout line. None for anything else."""
    line = line.strip()
    if not line.startswith("FRAME "):
        return None
    try:
        sample = json.loads(line[6:])
    except json.JSONDecodeError:
        return None
    return sample if isinstance(sample, dict) else None
