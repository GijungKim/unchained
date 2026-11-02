"use strict";

const $ = (id) => document.getElementById(id);
const state = { minutes: 10, running: false, runStarted: 0, runSeconds: 0, beats: [], lastBeatKey: "" };
const ECG_SPAN = 8000;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour12: false });

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

function setPill(mode, text) {
  const pill = $("status-pill");
  pill.className = `pill ${mode}`;
  pill.textContent = text;
}

async function refreshStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch (e) {
    setPill("error", "server down");
    return;
  }
  {
    const w = s.whoop;
    state.running = w.running;
    const btn = $("connect");
    btn.textContent = state.running ? "Stop" : "Start stream";
    btn.disabled = false;
    $("replay").hidden = s.records > 0 || state.running;
    if (w.running) {
      if (w.streaming) setPill("live", "streaming");
      else setPill("working", "reading");
      state.runStarted = Date.parse(w.started_at);
      state.runSeconds = w.seconds;
      $("progress-wrap").hidden = false;
      const n = w.windows_completed;
      $("device-sub").textContent = w.streaming
        ? `live window ${n + 1} · ${s.records} records`
        : "reading from strap…";
    } else if (w.error) {
      setPill("error", "error");
      $("progress-wrap").hidden = true;
      showError(w.error);
    } else if (s.records > 0) {
      setPill("idle", "idle");
      $("progress-wrap").hidden = true;
      if (!w.session_id) {
        const last = (s.devices || {})["whoop-5"];
        $("device-sub").textContent = last ? `last data ${fmtTime(last)}` : "";
      }
    } else {
      setPill("idle", "idle");
    }
    if (w.session_id) {
      $("session").textContent = w.session_id;
      if (!w.running) $("device-sub").textContent = `last session ${w.session_id}`;
    }
  }
}

function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(showError._t);
  showError._t = setTimeout(() => { el.hidden = true; }, 12000);
}

function tickProgress() {
  if (!state.running) return;
  const elapsed = (Date.now() - state.runStarted) / 1000;
  const pct = Math.min(100, (elapsed / Math.max(1, state.runSeconds)) * 100);
  $("progress").style.width = `${pct}%`;
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

async function refreshData() {
  let data;
  try {
    data = await api(`/api/window?device=whoop-5&metric=heart_rate&minutes=${state.minutes}`);
  } catch {
    return;
  }
  const raw = data.samples;
  const valid = raw.filter((s) => s.quality > 0);
  const recent = valid.slice(-12).map((s) => s.value);
  const bpm = median(recent);
  $("bpm").textContent = bpm === null ? "—" : Math.round(bpm);
  $("bpm-note").textContent = bpm === null ? "" : `median of last ${recent.length} valid beats`;
  const lastBeat = $("last-beat");
  const latest = valid[valid.length - 1];
  const beatKey = latest ? `${latest.timestamp}:${Math.round(latest.value)}` : "";
  if (!reducedMotion && bpm !== null && beatKey && beatKey !== state.lastBeatKey) {
    state.lastBeatKey = beatKey;
    for (const el of [$("bpm"), document.querySelector(".bpm-heart")]) {
      if (!el) continue;
      el.classList.remove("beat");
      void el.offsetWidth;
      el.classList.add("beat");
    }
  }
  if (!latest) {
    lastBeat.textContent = "";
  } else {
    const ageS = Math.max(0, Math.round((Date.now() - Date.parse(latest.timestamp)) / 1000));
    const age = ageS < 2 ? "just now" : `${ageS}s ago`;
    lastBeat.textContent = `last beat ${Math.round(latest.value)} · ${age}`;
    lastBeat.classList.toggle("stale", ageS > 10);
  }

  if (raw.length) {
    const coverage = Math.round((valid.length / raw.length) * 100);
    $("beats").textContent = `${valid.length} / ${raw.length}`;
    if (valid.length) {
      const q = valid.map((s) => s.quality);
      $("quality").textContent = `${Math.round(median(q) * 100)}% · ${coverage}% kept`;
    } else {
      $("quality").textContent = `0% kept`;
    }
  } else {
    $("quality").textContent = "—";
    $("beats").textContent = "—";
  }

  try {
    const ibi = await api(`/api/window?device=whoop-5&metric=ibi_interval&minutes=10`);
    state.beats = beatsFromIbi(ibi.samples);
    const vals = ibi.samples.filter((s) => s.quality > 0).map((s) => s.value);
    if (vals.length > 1) {
      let sum = 0;
      for (let i = 1; i < vals.length; i++) sum += (vals[i] - vals[i - 1]) ** 2;
      $("hrv").textContent = `${Math.round(Math.sqrt(sum / (vals.length - 1)))} ms`;
    } else {
      // Whoop 5 HRS broadcast sends HR without RR intervals, so RMSSD is
      // unavailable on live data and activates on RR-bearing replay instead.
      $("hrv").textContent = valid.length ? "HR only" : "—";
    }
  } catch { /* keep last */ }

  drawChart(raw, valid);
  drawStrip(raw);
  drawEcg();
  refreshCompare();
}

async function refreshCompare() {
  try {
    const cmp = await api(`/api/compare?metric=heart_rate&minutes=${state.minutes}`);
    const names = Object.keys(cmp);
    const tbody = document.querySelector("#compare-table tbody");
    const cell = (text, cls) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      return td;
    };
    if (!names.length) {
      const tr = document.createElement("tr");
      const td = cell("no data yet — start the stream or load a replay", "empty");
      td.colSpan = 5;
      tr.appendChild(td);
      tbody.replaceChildren(tr);
      return;
    }
    const rows = names.map((d) => {
      const s = cmp[d];
      const tr = document.createElement("tr");
      tr.appendChild(cell(d));
      if (!s) {
        const td = cell("no samples in window", "empty");
        td.colSpan = 4;
        tr.appendChild(td);
        return tr;
      }
      tr.appendChild(cell(`${s.median.toFixed(1)} bpm`));
      tr.appendChild(cell(`${s.min.toFixed(0)}–${s.max.toFixed(0)}`));
      tr.appendChild(cell(String(s.n)));
      tr.appendChild(cell(`${Math.round(s.mean_quality * 100)}%`));
      return tr;
    });
    tbody.replaceChildren(...rows);
    $("compare-note").textContent = `valid beats, last ${state.minutes}m`;
  } catch { /* keep last */ }
}

function rollingMedian(points, k) {
  return points.map((p, i) => {
    const win = points.slice(Math.max(0, i - k + 1), i + 1).map((q) => q.v);
    return { t: p.t, v: median(win) };
  });
}

function drawChart(raw, valid) {
  const canvas = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight || 150;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const padL = 44, padR = 12, padT = 14, padB = 22;
  const iw = w - padL - padR, ih = h - padT - padB;
  const now = Date.now();
  const span = state.minutes * 60 * 1000;
  const x = (t) => padL + ((t - (now - span)) / span) * iw;

  // domain from valid beats only; outliers are clamped to the edges
  const values = valid.map((s) => s.value);
  let lo = values.length ? Math.min(...values) : 60;
  let hi = values.length ? Math.max(...values) : 80;
  const pad = Math.max(3, (hi - lo) * 0.25);
  lo = Math.floor(lo - pad);
  hi = Math.ceil(hi + pad);
  const yRaw = (v) => padT + (1 - (v - lo) / (hi - lo)) * ih;
  const y = (v) => Math.max(padT, Math.min(padT + ih, yRaw(v)));

  ctx.strokeStyle = "#262b30";
  ctx.fillStyle = "#5d6367";
  ctx.font = "11px ui-monospace, monospace";
  ctx.lineWidth = 1;
  const step = Math.max(5, Math.round((hi - lo) / 4 / 5) * 5);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    ctx.beginPath();
    ctx.moveTo(padL, y(v));
    ctx.lineTo(w - padR, y(v));
    ctx.stroke();
    ctx.fillText(`${v}`, 8, y(v) + 4);
  }

  if (!raw.length) {
    ctx.fillStyle = "#5d6367";
    ctx.font = "13px sans-serif";
    ctx.fillText("no samples in range", padL + 8, padT + 20);
    return;
  }

  // layer 1: raw outliers (quality 0), dim
  ctx.fillStyle = "rgba(154, 160, 163, 0.45)";
  for (const s of raw) {
    if (s.quality > 0) continue;
    ctx.beginPath();
    ctx.arc(x(Date.parse(s.timestamp)), y(s.value), 1.6, 0, Math.PI * 2);
    ctx.fill();
  }

  const pts = valid.map((s) => ({ t: Date.parse(s.timestamp), v: s.value }));
  if (!pts.length) return;

  // layer 2: valid beats, connected with gap breaks
  ctx.strokeStyle = "#e8e6e1";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  let prevT = 0;
  pts.forEach((p, i) => {
    if (i === 0 || p.t - prevT > 5000) ctx.moveTo(x(p.t), y(p.v));
    else ctx.lineTo(x(p.t), y(p.v));
    prevT = p.t;
  });
  ctx.stroke();

  // layer 3: rolling-median trend
  const trend = rollingMedian(pts, 5);
  ctx.strokeStyle = "#e4513b";
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.beginPath();
  prevT = 0;
  trend.forEach((p, i) => {
    if (i === 0 || p.t - prevT > 5000) ctx.moveTo(x(p.t), y(p.v));
    else ctx.lineTo(x(p.t), y(p.v));
    prevT = p.t;
  });
  ctx.stroke();

  const last = trend[trend.length - 1];
  ctx.fillStyle = "#e4513b";
  ctx.beginPath();
  ctx.arc(x(last.t), y(last.v), 3.5, 0, Math.PI * 2);
  ctx.fill();
}

function drawStrip(raw) {
  const strip = $("strip");
  const buckets = 90;
  const now = Date.now();
  const span = state.minutes * 60 * 1000;
  const hits = new Array(buckets).fill(0); // 0 none, 1 outlier, 2 valid
  for (const s of raw) {
    const i = Math.floor(((Date.parse(s.timestamp) - (now - span)) / span) * buckets);
    if (i < 0 || i >= buckets) continue;
    hits[i] = Math.max(hits[i], s.quality > 0 ? 2 : 1);
  }
  const cells = hits.map((h) => {
    const i = document.createElement("i");
    if (h === 2) i.className = "hit";
    else if (h === 1) i.className = "miss";
    return i;
  });
  strip.replaceChildren(...cells);
}

$("connect").addEventListener("click", async () => {
  try {
    if (state.running) {
      await api("/api/collect/whoop/stop", { method: "POST" });
    } else {
      await api("/api/collect/whoop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seconds: 60, loop: true }),
      });
    }
    refreshStatus();
  } catch (e) {
    showError(String(e.message || e));
  }
});

function setVerdict(fusedBpm, confidence, text) {
  // DOM-built, never innerHTML: model text is untrusted.
  const el = $("verdict");
  el.replaceChildren();
  if (fusedBpm !== null && fusedBpm !== undefined) {
    const strong = document.createElement("strong");
    strong.textContent = confidence !== null && confidence !== undefined
      ? `${Math.round(fusedBpm)} bpm · ${Math.round(confidence * 100)}% — `
      : `${Math.round(fusedBpm)} bpm — `;
    el.appendChild(strong);
  }
  el.appendChild(document.createTextNode(text));
}

$("fuse").addEventListener("click", async () => {
  const btn = $("fuse");
  btn.disabled = true;
  setVerdict(null, null, "Fusing…");
  try {
    const out = await api("/api/agent/reconcile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes: state.minutes }),
    });
    $("agent-model").textContent = out.model || "";
    const j = out.judgment || {};
    setVerdict(j.fused_bpm ?? out.proposal?.fused_bpm ?? null, j.confidence ?? null,
      j.verdict || "No verdict returned.");
  } catch (e) {
    setVerdict(null, null, `Agent unavailable: ${e.message || e}`);
  } finally {
    btn.disabled = false;
  }
});

$("ask-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const input = $("ask-q");
  const q = input.value.trim();
  if (!q) return;
  const ans = $("answer");
  ans.hidden = false;
  ans.textContent = "Thinking…";
  try {
    const out = await api("/api/agent/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, minutes: state.minutes }),
    });
    $("agent-model").textContent = out.model || "";
    ans.textContent = out.answer;
  } catch (e) {
    ans.textContent = `Agent unavailable: ${e.message || e}`;
  }
});

$("replay").addEventListener("click", async () => {
  try {
    await api("/api/replay", { method: "POST" });
    refreshStatus();
    refreshData();
  } catch (e) {
    showError(String(e.message || e));
  }
});

function beatsFromIbi(samples) {
  // Frames arrive ~1Hz carrying the latest IBI, so beats are reconstructed by
  // filling each inter-frame gap with evenly spaced beats at the local mean
  // interval. Count and average rate are measured; intra-gap placement is not.
  const valid = samples
    .filter((s) => s.quality > 0)
    .sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const beats = [];
  valid.forEach((s, i) => {
    const tCur = Date.parse(s.timestamp);
    if (i === 0) {
      beats.push(tCur);
      return;
    }
    const tPrev = Date.parse(valid[i - 1].timestamp);
    const gap = tCur - tPrev;
    const ok =
      gap <= 5000 && s.value > 200 && s.value < 3000 &&
      valid[i - 1].value > 200 && valid[i - 1].value < 3000;
    if (!ok) {
      beats.push(tCur);
      return;
    }
    const avgIbi = (s.value + valid[i - 1].value) / 2;
    const n = Math.max(1, Math.round(gap / avgIbi));
    for (let k = 1; k < n; k++) beats.push(tPrev + (gap * k) / n);
    beats.push(tCur);
  });
  return beats.sort((a, b) => a - b);
}

function qrs(dt) {
  const g = (x, s) => Math.exp(-(x * x) / (2 * s * s));
  return -0.12 * g(dt + 32, 9) + g(dt, 7) - 0.2 * g(dt - 32, 10) + 0.22 * g(dt - 200, 45);
}

function drawEcg() {
  const canvas = $("ecg");
  if (!canvas.clientWidth) return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight || 90;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const padL = 8, padR = 10, padT = 10, padB = 10;
  const iw = w - padL - padR;
  const base = h - padB - (h - padT - padB) * 0.25;
  const amp = (h - padT - padB) * 0.62;
  const now = Date.now();
  // live data sweeps; stale data pins to the most recent rhythm instead of flatlining
  const lastBeat = state.beats.length ? state.beats[state.beats.length - 1] : 0;
  const live = now - lastBeat < ECG_SPAN;
  const end = live ? now : lastBeat + 1000;
  const x = (t) => padL + ((t - (end - ECG_SPAN)) / ECG_SPAN) * iw;

  ctx.strokeStyle = "#22272b";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let s = 0; s <= ECG_SPAN / 1000; s++) {
    const gx = padL + (s / (ECG_SPAN / 1000)) * iw;
    ctx.moveTo(gx, padT);
    ctx.lineTo(gx, h - padB);
  }
  ctx.stroke();
  ctx.strokeStyle = "#2e3439";
  ctx.beginPath();
  ctx.moveTo(padL, base);
  ctx.lineTo(w - padR, base);
  ctx.stroke();

  if (!state.beats.length) {
    // HR-only broadcast: no measured beat timing exists, so say so instead
    // of drawing a flat line that reads as asystole.
    ctx.fillStyle = "#5d6367";
    ctx.font = "12px ui-monospace, monospace";
    ctx.fillText("no RR intervals — strap broadcast sends heart rate only", padL + 4, base - 8);
    return;
  }

  const beats = state.beats.filter((t) => t > end - ECG_SPAN - 500 && t <= end + 100);
  const grad = ctx.createLinearGradient(padL, 0, w - padR, 0);
  grad.addColorStop(0, "rgba(88, 179, 104, 0.15)");
  grad.addColorStop(1, "rgba(88, 179, 104, 1)");
  ctx.strokeStyle = grad;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.beginPath();
  let first = true;
  for (let t = end - ECG_SPAN; t <= end; t += 4) {
    let v = 0;
    for (const b of beats) {
      const dt = t - b;
      if (dt > -60 && dt < 420) v += qrs(dt);
    }
    if (first) {
      ctx.moveTo(x(t), base - v * amp);
      first = false;
    } else {
      ctx.lineTo(x(t), base - v * amp);
    }
  }
  ctx.stroke();

  ctx.fillStyle = "#58b368";
  ctx.beginPath();
  ctx.arc(w - padR, base, 3, 0, Math.PI * 2);
  ctx.fill();

  if (!live && state.beats.length) {
    const ageS = Math.round((now - lastBeat) / 1000);
    const age = ageS < 90 ? `${ageS}s ago` : `${Math.round(ageS / 60)}m ago`;
    ctx.fillStyle = "#5d6367";
    ctx.font = "11px ui-monospace, monospace";
    ctx.fillText(`last captured rhythm · ${age}`, padL + 4, padT + 12);
  }
}

refreshStatus();
refreshData();
setInterval(refreshStatus, 2000);
setInterval(refreshData, 1500);
setInterval(tickProgress, 250);
if (!reducedMotion) {
  const loop = () => {
    drawEcg();
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}
