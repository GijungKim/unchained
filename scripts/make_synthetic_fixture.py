"""Generate a synthetic Whoop HRS --report fixture. Seeded, obviously fake.

Mirrors the real report shape (1 Hz notifications with heart rate + RR
intervals) with a random-walk BPM around 72 and ~5% contact-loss samples.
Never real biometrics.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic-whoop.json"


def main(seed: int = 8, seconds: int = 60) -> Path:
    rng = random.Random(seed)
    base = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC).timestamp()
    bpm = 72.0
    samples = []
    t = base
    for _ in range(seconds):
        t += 1.0
        bpm = max(60.0, min(90.0, bpm + rng.uniform(-2.0, 2.0)))
        contact = rng.random() >= 0.05
        rr_base = 60000.0 / bpm
        rr = [round(rr_base + rng.uniform(-25.0, 25.0), 1) for _ in range(rng.choice([1, 1, 2]))]
        samples.append(
            {
                "hostArrivalTimestamp": t,
                "hr": round(bpm),
                "rrMs": rr,
                "contact": contact,
            }
        )
    report = {"device": "whoop-5", "synthetic": True, "samples": samples}
    OUT.write_text(json.dumps(report, indent=1))
    return OUT


if __name__ == "__main__":
    print(main())
