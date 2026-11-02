"""Live collection: run the in-repo HRS collector, ingest its frames.

The collector (collectors/whoop_hrs.py) speaks only standard Bluetooth LE
Heart Rate Service — no vendor app, no pairing, no custom protocol. Windows
always run to completion — stopping applies between windows, never mid-window.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from . import ingest_whoop

COLLECTOR = Path(__file__).resolve().parent / "collectors" / "whoop_hrs.py"


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _whoop_cmd(seconds: int) -> list[str]:
    if not 10 <= seconds <= 120:
        raise ValueError("seconds must be 10..120")
    if not COLLECTOR.exists():
        raise RuntimeError(f"missing collector component: {COLLECTOR}")
    return [sys.executable, str(COLLECTOR)]


def stream_whoop_live(seconds: int, on_records: Callable[[list[dict]], None]) -> str:
    """Run one Whoop HRS window, calling on_records(records) as beats arrive.

    Returns the session_id. Raises RuntimeError on collector failure.
    """
    argv = _whoop_cmd(seconds)
    session_id = f"whoop-5-{_utc_stamp()}"
    ingester = ingest_whoop.SampleIngester(session_id)
    with tempfile.TemporaryDirectory(prefix="unchained-whoop-") as tmp:
        report_path = str(Path(tmp) / "report.json")
        proc = subprocess.Popen(
            [
                *argv,
                "--live",
                "--seconds",
                str(seconds),
                "--report",
                report_path,
                "--stream-frames",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                sample = ingest_whoop.parse_frame_line(line)
                if sample is None:
                    continue
                try:
                    records = ingester.feed(sample)
                except (KeyError, ValueError, TypeError):
                    continue
                if records:
                    on_records(records)
        finally:
            _, err = proc.communicate(timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(
                f"probe failed (exit {proc.returncode}): {(err or '').strip()[-500:]}"
            )
    return session_id
