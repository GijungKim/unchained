"""unchained CLI: ingest fixtures, query the store, serve the dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import agent, ingest_whoop, store


def data_dir() -> Path:
    root = Path(os.environ.get("UNCHAINED_DATA_DIR", "~/.unchained")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def cmd_ingest(args: argparse.Namespace) -> int:
    conn = store.connect(data_dir() / "unchained.sqlite3")
    path = Path(args.fixture)
    records = ingest_whoop.parse_file(path, session_id=args.session or path.stem)
    n = store.insert_records(conn, records)
    print(f"ingested {n} records from {path.name} (session={args.session or path.stem})")
    return 0


def cmd_latest(args: argparse.Namespace) -> int:
    conn = store.connect(data_dir() / "unchained.sqlite3")
    row = store.latest(conn, args.device, args.metric)
    print(json.dumps(row, indent=1) if row else "no data")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    conn = store.connect(data_dir() / "unchained.sqlite3")
    result = store.compare(conn, args.metric, args.start, args.end, args.min_quality)
    print(json.dumps(result, indent=1))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    conn = store.connect(data_dir() / "unchained.sqlite3")
    try:
        result = agent.reconcile(conn, minutes=args.minutes)
    except agent.AgentError as exc:
        print(f"agent unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=1))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    conn = store.connect(data_dir() / "unchained.sqlite3")
    try:
        result = agent.ask(conn, args.question, minutes=args.minutes)
    except agent.AgentError as exc:
        print(f"agent unavailable: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=1))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    print(f"Unchained dashboard at http://{args.host}:{args.port}")
    uvicorn.run("unchained.server:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unchained")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="ingest a Whoop HRS --report fixture")
    p.add_argument("--fixture", required=True)
    p.add_argument("--session", default=None)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("latest", help="latest reading for a device+metric")
    p.add_argument("--device", default="whoop-5")
    p.add_argument("--metric", default="heart_rate")
    p.set_defaults(func=cmd_latest)

    p = sub.add_parser("compare", help="per-device summaries over a window")
    p.add_argument("--metric", default="heart_rate")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--min-quality", type=float, default=0.0)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("reconcile", help="fuse sources with the Muse agent")
    p.add_argument("--minutes", type=int, default=10)
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("ask", help="ask Muse about the recent window")
    p.add_argument("question")
    p.add_argument("--minutes", type=int, default=10)
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("serve", help="run the dashboard server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
