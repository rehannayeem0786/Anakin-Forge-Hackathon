"""
ARGUS — UI contract test.

app.js renders the run from the SSE stream. If the server emits the wrong event
kinds, or the right kinds with the wrong `data` shape, the UI silently renders
nothing — and that is the surface a judge actually watches.

This drives the real HTTP/SSE contract the browser depends on, offline, and
asserts every field app.js reaches for is present.
"""
from __future__ import annotations

import json
import os
import threading
import time

import httpx

# Point at a running ARGUS server. Start one first, e.g.
#   python run.py --offline --no-browser --port 8901
BASE = os.environ.get("ARGUS_URL", "http://127.0.0.1:8901").rstrip("/")
GOAL = ("I need a 65-inch OLED TV under $1,500 for gaming. "
        "Find the best one and prepare the purchase.")

# What app.js switches on in handleEvent().
REQUIRED_KINDS = {
    "phase", "thought", "plan", "tool_call", "tool_result", "evidence",
    "critique", "decision", "approval", "action", "artifact", "credits",
    "run_start", "run_end",
}

events: list[dict] = []
errors: list[str] = []


def approve_when_asked(run_id: str) -> None:
    """The agent blocks on the human gate; approve it like the UI button does."""
    deadline = time.time() + 120
    while time.time() < deadline:
        if any(e.get("kind") == "approval" for e in events):
            r = httpx.post(f"{BASE}/api/approve/{run_id}",
                           json={"approved": True}, timeout=20)
            print(f"  [ui] clicked Approve -> {r.status_code} {r.text[:80]}")
            return
        time.sleep(0.2)
    errors.append("approval event never arrived — the UI would have no button to click")


def main() -> int:
    # ---- /api/config: the pills and presets ------------------------------
    cfg = httpx.get(f"{BASE}/api/config", timeout=20).json()
    for key in ("runtime", "tools", "presets"):
        if key not in cfg:
            errors.append(f"/api/config missing '{key}' (app.js reads it)")
    if not cfg.get("presets"):
        errors.append("/api/config returned no presets (the preset chips would be empty)")
    print(f"  [ui] /api/config ok — mode={cfg['runtime']['mode']}, "
          f"{len(cfg.get('presets', []))} presets, {len(cfg.get('tools', {}))} tools")

    # ---- POST /api/run ---------------------------------------------------
    run = httpx.post(f"{BASE}/api/run", json={"goal": GOAL}, timeout=30).json()
    run_id = run.get("run_id")
    if not run_id:
        errors.append("/api/run did not return run_id")
        return 1
    print(f"  [ui] /api/run ok — run_id={run_id}")

    threading.Thread(target=approve_when_asked, args=(run_id,), daemon=True).start()

    # ---- GET /api/stream/{id}: the SSE the UI renders ---------------------
    print("  [ui] streaming SSE…")
    ended = False
    with httpx.stream("GET", f"{BASE}/api/stream/{run_id}", timeout=180) as s:
        for line in s.iter_lines():
            if not line:
                continue
            if line.startswith("event: end"):
                ended = True
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    errors.append(f"unparseable SSE data line: {line[:80]}")
            if ended and events and events[-1].get("kind") == "run_end":
                break

    kinds = {e.get("kind") for e in events}
    missing = REQUIRED_KINDS - kinds
    if missing:
        errors.append(f"SSE never emitted {sorted(missing)} — the UI cannot render them")
    if not ended:
        errors.append("stream never sent `event: end` — the UI would stay 'Running…' forever")
    print(f"  [ui] {len(events)} events, {len(kinds)} distinct kinds")

    # ---- field-level checks that app.js depends on -----------------------
    def first(kind: str) -> dict:
        return next((e for e in events if e.get("kind") == kind), {})

    for e in events:
        if not e.get("seq") or "clock" not in e or "phase" not in e:
            errors.append(f"event missing seq/clock/phase: {e.get('kind')}")
            break

    dec = first("decision").get("data") or {}
    for f in ("ranked", "criteria", "confidence", "headline", "reasoner", "critique_adjustments"):
        if f not in dec:
            errors.append(f"decision event missing '{f}' (renderDecision reads it)")
    if dec.get("ranked"):
        o = dec["ranked"][0]
        for f in ("rank", "title", "price", "currency", "total_score", "flags", "rationale"):
            if f not in o:
                errors.append(f"ranked option missing '{f}' (rankCard reads it)")

    ev = first("evidence").get("data") or {}
    for f in ("id", "kind", "title", "url", "retrieved_at", "content"):
        if f not in ev:
            errors.append(f"evidence event missing '{f}' (pushEvidence reads it)")

    ap = first("approval").get("data") or {}
    if "target" not in ap:
        errors.append("approval event missing 'target' (renderApproval reads it)")

    tr = first("tool_result").get("data") or {}
    if "result" not in tr:
        errors.append("tool_result missing 'result' (recordSurface reads endpoint/ms/credits)")

    cr = first("credits").get("data") or {}
    for f in ("spent", "free_calls", "budget", "remaining", "calls"):
        if f not in cr:
            errors.append(f"credits event missing '{f}' (renderCredits reads it)")

    # ---- /api/state and /api/artifact ------------------------------------
    st = httpx.get(f"{BASE}/api/state/{run_id}", timeout=30).json()
    for f in ("decision", "action", "dossier", "credits", "trace", "evidence_count"):
        if f not in st:
            errors.append(f"/api/state missing '{f}'")
    print(f"  [ui] /api/state ok — status={st.get('status')}, "
          f"evidence={st.get('evidence_count')}, options={st.get('option_count')}")

    art = httpx.get(f"{BASE}/api/artifact/{run_id}", timeout=30)
    if art.status_code != 200:
        errors.append(f"/api/artifact returned {art.status_code} — the dossier link would 404")
    else:
        md = art.json().get("markdown") or ""
        if "# ARGUS Decision Dossier" not in md:
            errors.append("/api/artifact returned no dossier markdown")
        print(f"  [ui] /api/artifact ok — {len(md):,} chars of markdown")

    # ---- the surface panel's data source ---------------------------------
    surfaces = [e for e in events
                if e.get("kind") == "tool_result" and (e.get("data") or {}).get("result")]
    if not surfaces:
        errors.append("no tool_result carried a 'result' — the surface panel would be empty")
    else:
        print(f"  [ui] surface panel has {len(surfaces)} instrumented calls")

    print()
    if errors:
        print(f"FAILED — {len(errors)} problem(s):")
        for e in errors:
            print(f"   * {e}")
        return 1
    print("PASSED — the UI contract is satisfied end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
