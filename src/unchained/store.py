"""SQLite store: validated inserts, window queries, per-device comparison."""

from __future__ import annotations

import math
import sqlite3
import statistics
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

_METRICS = {
    "heart_rate": ("bpm", 20.0, 300.0),
    # every raw frame's implied bpm, outliers included; invalid frames carry
    # quality 0.0 so summaries exclude them via min_quality
    "heart_rate_raw": ("bpm", 1.0, 1000.0),
    # raw inter-beat intervals; drives the rhythm strip (valid only)
    "ibi_interval": ("ms", 1.0, 10000.0),
    "skin_temp": ("celsius", 10.0, 45.0),
    "hrv_rmssd": ("ms", 0.0, 1000.0),
    "resting_heart_rate": ("bpm", 20.0, 300.0),
    "sleep_duration": ("min", 0.0, 1440.0),
    "recovery_score": ("percent", 0.0, 100.0),
    "spo2": ("percent", 0.0, 100.0),
}


class ValidationError(ValueError):
    pass


def _iso_utc(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"bad timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"timestamp needs a UTC offset: {value!r}")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    schema = resources.files("unchained").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    return conn


def insert_records(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Validate + insert canonical records. Returns rows written."""
    rows = []
    for r in records:
        metric = r.get("metric")
        if metric not in _METRICS:
            raise ValidationError(f"unknown metric: {metric!r}")
        unit, lo, hi = _METRICS[metric]
        if r.get("unit") != unit:
            raise ValidationError(f"{metric} must use unit {unit}, got {r.get('unit')!r}")
        value = r.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValidationError(f"value must be finite numeric: {value!r}")
        if not lo <= value <= hi:
            raise ValidationError(f"{metric}={value} outside [{lo}, {hi}]")
        quality = r.get("quality")
        if not isinstance(quality, (int, float)) or not 0.0 <= quality <= 1.0:
            raise ValidationError(f"quality must be 0..1: {quality!r}")
        for field in ("device_id", "method"):
            if not r.get(field) or not isinstance(r[field], str):
                raise ValidationError(f"{field} must be a non-empty string")
        rows.append(
            (
                r["device_id"],
                metric,
                _iso_utc(r["timestamp"]),
                float(value),
                unit,
                float(quality),
                r["method"],
                r.get("session_id"),
            )
        )
    conn.executemany(
        """INSERT OR IGNORE INTO records
           (device_id, metric, timestamp, value, unit, quality, method, session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return conn.total_changes


def latest(conn: sqlite3.Connection, device_id: str, metric: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM records WHERE device_id = ? AND metric = ? ORDER BY timestamp DESC LIMIT 1",
        (device_id, metric),
    ).fetchone()
    return dict(row) if row else None


def window(
    conn: sqlite3.Connection,
    device_id: str,
    metric: str,
    start: str,
    end: str,
    min_quality: float = 0.0,
) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM records WHERE device_id = ? AND metric = ?"
        " AND timestamp >= ? AND timestamp <= ? AND quality >= ?"
        " ORDER BY timestamp",
        (device_id, metric, _iso_utc(start), _iso_utc(end), min_quality),
    ).fetchall()
    return [dict(r) for r in rows]


def summarize(samples: list[dict]) -> dict | None:
    """Median + spread + quality stats over windowed samples."""
    if not samples:
        return None
    values = [s["value"] for s in samples]
    qualities = [s["quality"] for s in samples]
    return {
        "n": len(samples),
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
        "mean_quality": statistics.fmean(qualities),
        "min_quality": min(qualities),
        "start": samples[0]["timestamp"],
        "end": samples[-1]["timestamp"],
    }


def compare(
    conn: sqlite3.Connection, metric: str, start: str, end: str, min_quality: float = 0.0
) -> dict[str, dict | None]:
    """Per-device summaries over the same window — the discrepancy input."""
    devices = [r[0] for r in conn.execute("SELECT DISTINCT device_id FROM records").fetchall()]
    return {d: summarize(window(conn, d, metric, start, end, min_quality)) for d in devices}
