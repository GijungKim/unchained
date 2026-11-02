"""Muse agent: fuse multi-source vitals and answer questions over local data.

The agent only ever sees the local store's quality-annotated windows — never
raw BLE, never a vendor cloud. Deterministic fusion (quality-weighted) runs
first; Muse then judges discrepancies and explains in plain language.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from . import store

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MODEL = "muse-spark-1.3-contributor"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"


class AgentError(Exception):
    pass


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from the repo .env without overriding real env."""
    target = path or REPO_ROOT / ".env"
    if not target.exists():
        return
    for line in target.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def config() -> tuple[str, str, str]:
    """(base_url, api_key, model). Raises AgentError when unusable."""
    load_dotenv()
    key = os.environ.get("MODEL_API_KEY", "").strip()
    if not key:
        raise AgentError("MODEL_API_KEY is not set — add it to unchained/.env (see .env.example)")
    model = os.environ.get("MUSE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    base = os.environ.get("MUSE_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    return base.rstrip("/"), key, model


def chat(messages: list[dict], temperature: float = 0.0, timeout: float = 60.0) -> str:
    """One chat-completions call. Returns the assistant text."""
    base, key, model = config()
    try:
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": messages, "temperature": temperature},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise AgentError(f"Muse API unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise AgentError(f"Muse API error {resp.status_code}: {resp.text[:300]}")
    try:
        return str(resp.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AgentError(f"Muse API returned unexpected payload: {resp.text[:300]}") from exc


def _fmt(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def build_context(
    conn: sqlite3.Connection, minutes: int = 10, metric: str = "heart_rate"
) -> dict:
    """Per-device summaries plus validity stats — everything the agent may cite."""
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)
    start_s, end_s = _fmt(start), _fmt(end)
    devices = [r[0] for r in conn.execute("SELECT DISTINCT device_id FROM records").fetchall()]
    sources: dict[str, dict] = {}
    for device in devices:
        valid = store.summarize(store.window(conn, device, metric, start_s, end_s))
        raw_n = len(store.window(conn, device, f"{metric}_raw", start_s, end_s))
        temp = store.latest(conn, device, "skin_temp")
        entry: dict = {"samples": valid}
        if valid and raw_n:
            entry["validity_rate"] = round(valid["n"] / raw_n, 3)
            entry["raw_frames"] = raw_n
        if temp:
            entry["skin_temp_c"] = temp["value"]
        sources[device] = entry
    return {"minutes": minutes, "metric": metric, "start": start_s, "end": end_s, "sources": sources}


def render_context(ctx: dict) -> str:
    lines = [
        f"Local vitals window: last {ctx['minutes']} min ({ctx['start']} .. {ctx['end']}),",
        f"metric {ctx['metric']}. Quality is 0..1 signal confidence per sample.",
    ]
    if not ctx["sources"]:
        lines.append("No devices have any records.")
        return "\n".join(lines)
    for device, entry in ctx["sources"].items():
        s = entry["samples"]
        if not s:
            lines.append(f"- {device}: no samples in window.")
            continue
        lines.append(
            f"- {device}: n={s['n']} median={s['median']:.1f} range={s['min']:.0f}–{s['max']:.0f} "
            f"mean_quality={s['mean_quality']:.2f}"
            + (
                f" validity={entry['validity_rate']:.0%} ({s['n']}/{entry['raw_frames']} frames kept)"
                if "validity_rate" in entry
                else ""
            )
            + (f" skin_temp={entry['skin_temp_c']:.1f}C" if "skin_temp_c" in entry else "")
        )
    return "\n".join(lines)


def deterministic_fuse(ctx: dict) -> dict | None:
    """Quality-weighted mean of device medians. Weight = n * mean_quality.

    Mean, not median: per-device medians are already robust summaries, and a
    median-of-medians degenerates with two sources (it just copies whichever
    side holds half the weight). This is fixed-effect meta-analysis: each
    device votes its median, weighted by evidence volume times quality.
    """
    weighted: list[tuple[float, float]] = []
    weights: dict[str, float] = {}
    for device, entry in ctx["sources"].items():
        s = entry["samples"]
        if not s:
            continue
        w = s["n"] * s["mean_quality"]
        weights[device] = round(w, 2)
        if w > 0:
            weighted.append((s["median"], w))
    if not weighted:
        return None
    total = sum(w for _, w in weighted)
    fused = sum(median * w for median, w in weighted) / total
    return {"fused_bpm": round(fused, 1), "weights": weights}


RECONCILE_SYSTEM = (
    "You are the fusion judge for a local health dashboard. The wearer reads "
    "their own devices directly, with no vendor app and no vendor cloud. "
    "Below is a "
    "deterministic quality-weighted proposal plus per-source stats; judge it, "
    "call out discrepancies, and explain in plain language. Reply with JSON "
    "only, no prose: {\"fused_bpm\": number|null, \"confidence\": 0..1, "
    "\"verdict\": \"1-2 sentences\", \"per_source\": {device: \"one-line trust "
    "note\"}, \"discrepancies\": [\"...\"]}. If the data is insufficient for a "
    "reading, set fused_bpm to null and say so in the verdict."
)


def _parse_json(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def reconcile(
    conn: sqlite3.Connection, minutes: int = 10, metric: str = "heart_rate"
) -> dict:
    """Fuse sources over the window: deterministic proposal + Muse judgment."""
    _check_minutes(minutes)
    ctx = build_context(conn, minutes, metric)
    proposal = deterministic_fuse(ctx)
    _, _, model = config()
    if proposal is None:
        return {
            "model": model,
            "minutes": minutes,
            "metric": metric,
            "window": {"start": ctx["start"], "end": ctx["end"]},
            "sources": ctx["sources"],
            "proposal": None,
            "judgment": {
                "fused_bpm": None,
                "confidence": 0.0,
                "verdict": "No usable samples in this window.",
                "per_source": {},
                "discrepancies": [],
            },
        }
    user = (
        f"{render_context(ctx)}\n\nDeterministic proposal: fused_bpm={proposal['fused_bpm']} "
        f"weights={json.dumps(proposal['weights'])}"
    )
    reply = chat(
        [
            {"role": "system", "content": RECONCILE_SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    judgment = _parse_json(reply) or {"verdict": reply.strip(), "structured": False}
    return {
        "model": model,
        "minutes": minutes,
        "metric": metric,
        "window": {"start": ctx["start"], "end": ctx["end"]},
        "sources": ctx["sources"],
        "proposal": proposal,
        "judgment": judgment,
    }


ASK_SYSTEM = (
    "You answer questions about the wearer's recent vitals using ONLY the "
    "provided local-store window. Never use general knowledge, never guess beyond "
    "the numbers. Cite sample counts when making claims. If the data cannot "
    "answer, say so plainly. Keep answers to 2-4 sentences unless asked for "
    "detail."
)


def _check_minutes(minutes: int) -> None:
    if not 1 <= minutes <= 60:
        raise AgentError("minutes must be 1..60")


def ask(conn: sqlite3.Connection, question: str, minutes: int = 10) -> dict:
    """Answer a free-text question over the recent window."""
    _check_minutes(minutes)
    question = (question or "").strip()
    if not question:
        raise AgentError("question must be a non-empty string")
    if len(question) > 2000:
        raise AgentError("question is too long (max 2000 chars)")
    ctx = build_context(conn, minutes)
    _, _, model = config()
    reply = chat(
        [
            {"role": "system", "content": ASK_SYSTEM},
            {
                "role": "user",
                "content": f"{render_context(ctx)}\n\nQuestion: {question}",
            },
        ],
        temperature=0.2,
    )
    return {
        "model": model,
        "question": question,
        "minutes": minutes,
        "window": {"start": ctx["start"], "end": ctx["end"]},
        "answer": reply.strip(),
        "sources": {d: e["samples"] for d, e in ctx["sources"].items()},
    }

