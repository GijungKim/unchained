"""Whoop ingest tests: HRS samples -> canonical records (no hardware)."""

from datetime import UTC, datetime

from unchained import ingest_whoop, store


def _sample(**overrides):
    base = {
        "hostArrivalTimestamp": datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC).timestamp(),
        "hr": 72,
        "rrMs": [800.0, 820.0],
        "contact": True,
    }
    base.update(overrides)
    return base


def test_feed_emits_hr_and_backdated_rr():
    records = ingest_whoop.SampleIngester("s1").feed(_sample())
    assert len(records) == 3
    hr = records[0]
    assert (hr["device_id"], hr["metric"], hr["value"], hr["quality"]) == ("whoop-5", "heart_rate", 72.0, 1.0)
    assert hr["timestamp"] == "2026-10-31T12:00:00.000Z"
    rr1, rr2 = records[1], records[2]
    assert rr1["metric"] == "ibi_interval" and rr1["value"] == 820.0
    assert rr2["value"] == 800.0
    # last interval ends at arrival; earlier ones step backwards
    assert rr1["timestamp"] == "2026-10-31T11:59:59.180Z"
    assert rr2["timestamp"] == "2026-10-31T11:59:58.380Z"


def test_no_contact_zeroes_quality():
    records = ingest_whoop.SampleIngester("s1").feed(_sample(contact=False))
    assert records and all(r["quality"] == 0.0 for r in records)


def test_unknown_contact_keeps_full_quality():
    records = ingest_whoop.SampleIngester("s1").feed(_sample(contact=None))
    assert records and all(r["quality"] == 1.0 for r in records)


def test_implausible_values_skipped_not_raised():
    records = ingest_whoop.SampleIngester("s1").feed(_sample(hr=400, rrMs=[0.5, 20000.0, 800.0]))
    assert len(records) == 1 and records[0]["metric"] == "ibi_interval"


def test_parse_frame_line():
    assert ingest_whoop.parse_frame_line("FRAME {\"hr\": 70}") == {"hr": 70}
    assert ingest_whoop.parse_frame_line("WHOOP something") is None
    assert ingest_whoop.parse_frame_line("FRAME nope") is None


def test_records_round_trip_through_store(tmp_path):
    conn = store.connect(tmp_path / "t.sqlite3")
    second = _sample(hr=74, rrMs=[])
    second["hostArrivalTimestamp"] += 1.0
    records = ingest_whoop.parse_report({"samples": [_sample(), second]}, session_id="s9")
    n = store.insert_records(conn, records)
    assert n == 4
    latest = store.latest(conn, "whoop-5", "heart_rate")
    assert latest["session_id"] == "s9"
