#!/usr/bin/env python3
"""Whoop 5 live collector: standard BLE Heart Rate Service (0x180D).

No vendor app, no pairing, no custom protocol — the strap broadcasts HRS when
Heart Rate Broadcast is enabled, and any host can subscribe to the Heart Rate
Measurement characteristic (0x2A37).

Contract:
  whoop_hrs.py --live --seconds N --report PATH [--stream-frames]

- Discovery scans for a "whoop"* advertiser with 0x180D. On failure exits 2
  with "error [discovery] ..." on stderr (the server retries these).
- With --stream-frames, each HRM notification prints one `FRAME {json}` line:
    {"hostArrivalTimestamp": <epoch s>, "hr": <bpm>,
     "rrMs": [<ms>, ...], "contact": true|false|null}
- With --report PATH, writes {"device": ..., "samples": [...]} at the end.
- Exit 2 "error [silent] ..." when connected but zero notifications arrived
  (strap off-wrist or loose); the server surfaces this without retrying.
- Exit 1 for anything else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
import time

from bleak import BleakClient, BleakScanner

HRS = "0000180d-0000-1000-8000-00805f9b34fb"
HRM = "00002a37-0000-1000-8000-00805f9b34fb"


def decode_hrm(pkt: bytes) -> tuple[int, list[float], bool | None]:
    """Decode one Heart Rate Measurement value (Bluetooth HRS spec)."""
    if not pkt:
        raise ValueError("empty HRM packet")
    flags = pkt[0]
    i = 1
    if flags & 0x01:
        (hr,) = struct.unpack_from("<H", pkt, i)
        i += 2
    else:
        hr = pkt[i]
        i += 1
    if flags & 0x08:  # energy expended present
        i += 2
    rr: list[float] = []
    if flags & 0x10:  # RR-Interval values present
        while i + 1 < len(pkt):
            (raw,) = struct.unpack_from("<H", pkt, i)
            i += 2
            rr.append(round(raw / 1024 * 1000, 1))
    contact: bool | None = None
    if flags & 0x06:  # sensor contact feature supported
        contact = bool(flags & 0x02)
    return hr, rr, contact


async def find_whoop(timeout: float = 12.0) -> tuple[str, str]:
    """Return (address, name) of a WHOOP advertiser with HRS."""
    found: dict[str, tuple[str, list[str]]] = {}

    def cb(dev, adv):
        name = adv.local_name or dev.name or ""
        uuids = [str(u).lower() for u in adv.service_uuids]
        if "whoop" in name.lower():
            found[dev.address] = (name, uuids)

    async with BleakScanner(cb):
        await asyncio.sleep(timeout)
    with_hrs = [(a, n) for a, (n, u) in found.items() if HRS in u]
    if with_hrs:
        return with_hrs[0]
    if found:
        addrs = ", ".join(f"{n} ({a})" for a, (n, _) in found.items())
        raise RuntimeError(
            f"error [discovery] WHOOP visible but no HRS service ({addrs}) — "
            "enable Heart Rate Broadcast in the Whoop app"
        )
    raise RuntimeError("error [discovery] no WHOOP advertiser seen — tap it awake")


async def stream(address: str, seconds: int, on_sample) -> list[dict]:
    samples: list[dict] = []
    async with BleakClient(address, timeout=15) as client:
        if not client.is_connected:
            raise RuntimeError("error [connect] could not connect to strap")
        def cb(_handle, pkt: bytes):
            try:
                hr, rr, contact = decode_hrm(bytes(pkt))
            except (ValueError, struct.error):
                return
            sample = {
                "hostArrivalTimestamp": time.time(),
                "hr": hr,
                "rrMs": rr,
                "contact": contact,
            }
            samples.append(sample)
            on_sample(sample)

        await client.start_notify(HRM, cb)
        try:
            await asyncio.sleep(seconds)
        finally:
            # Best-effort unsubscribe on the teardown path; the link may
            # already be gone, and there is nothing to recover.
            try:
                await client.stop_notify(HRM)
            except Exception:  # noqa: BLE001, S110 - teardown only
                pass
    return samples


async def main_async(args) -> int:
    try:
        address, name = await find_whoop()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"WHOOP {name} {address}", file=sys.stderr)

    def on_sample(sample: dict):
        if args.stream_frames:
            print(f"FRAME {json.dumps(sample)}", flush=True)

    try:
        samples = await stream(address, args.seconds, on_sample)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - reported, exit 1
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.report:
        with open(args.report, "w") as f:  # noqa: ASYNC230 - one small write
            json.dump({"device": "whoop-5", "address": address, "samples": samples}, f)

    if not samples:
        print(
            "error [silent] connected but zero HRM notifications — "
            "strap off-wrist or loose; put it on snugly",
            file=sys.stderr,
        )
        return 2
    print(f"{len(samples)} notifications", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="hrs_live.py")
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--report", default=None)
    parser.add_argument("--stream-frames", action="store_true")
    args = parser.parse_args(argv)
    if not 10 <= args.seconds <= 120:
        print("error: --seconds must be 10..120", file=sys.stderr)
        return 1
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
