"""
ARGUS — FastAPI server.

Serves the UI and streams the agent's reasoning live over SSE.

  GET  /                       the app
  GET  /api/config             runtime mode, tools, presets
  POST /api/run                start a run   -> { run_id }
  GET  /api/stream/{run_id}    SSE live trace
  POST /api/approve/{run_id}   human-in-the-loop gate for irreversible actions
  GET  /api/state/{run_id}     full run snapshot (decision, action, trace)
  GET  /api/artifact/{run_id}  the generated decision dossier (markdown)
  GET  /api/catalog            live Anakin Wire catalog (Zero Touch, no key)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from argus import __tagline__, __version__
from argus.agent import ArgusAgent, RunState
from argus.anakin_client import AnakinClient
from argus.config import settings
from argus.planner import TOOLS

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ARGUS", version=__version__, description=__tagline__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNS: dict[str, RunState] = {}
TASKS: dict[str, asyncio.Task[Any]] = {}


PRESETS = [
    {
        "label": "Buy a TV",
        "goal": "I need a 65-inch OLED TV under $1,500 for gaming. Find the best one and prepare the purchase.",
        "domain": "commerce",
    },
    {
        "label": "Plan a trip",
        "goal": "Plan a 3-day Goa weekend for 2 people under ₹20,000 and hold the best stay.",
        "domain": "travel",
    },
    {
        "label": "Choose software",
        "goal": "Compare the best AI note-taking tools for a 20-person team and recommend one to buy.",
        "domain": "software",
    },
]


class RunRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=500)


class ApprovalRequest(BaseModel):
    approved: bool = True


# --------------------------------------------------------------------------
@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "name": "ARGUS",
        "version": __version__,
        "tagline": __tagline__,
        "runtime": settings.describe(),
        "tools": TOOLS,
        "presets": PRESETS,
    }


@app.get("/api/catalog")
async def get_catalog() -> dict[str, Any]:
    """Live Wire catalog. Works with no API key thanks to Anakin Zero Touch."""
    async with AnakinClient(settings) as client:
        res = await client.wire_catalog()
    if not res.ok:
        return {"ok": False, "error": res.error, "catalog": []}
    entries = res.data.get("catalog") or []
    return {
        "ok": True,
        "count": len(entries),
        "total_actions": sum(int(e.get("action_count") or 0) for e in entries),
        "catalog": [
            {"slug": e.get("slug"), "name": e.get("name"), "category": e.get("category"),
             "action_count": e.get("action_count"), "description": e.get("description")}
            for e in entries
        ],
        "source": res.source,
    }


@app.post("/api/run")
async def start_run(req: RunRequest) -> dict[str, Any]:
    state = RunState(req.goal.strip(), settings)
    RUNS[state.id] = state
    agent = ArgusAgent(settings)
    TASKS[state.id] = asyncio.create_task(agent.run(state))
    return {"run_id": state.id, "mode": settings.mode}


@app.get("/api/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")

    async def gen() -> AsyncIterator[str]:
        q = state.bus.subscribe()
        try:
            # Replay anything that already happened (late subscriber safety).
            for ev in state.bus.replay():
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev is None:
                    break
                yield f"data: {json.dumps(ev.as_dict())}\n\n"
        finally:
            state.bus.unsubscribe(q)
        yield f"event: end\ndata: {json.dumps({'status': state.status})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/approve/{run_id}")
async def approve(run_id: str, req: ApprovalRequest) -> dict[str, Any]:
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")
    if state.approval is None or state.approval.done():
        return {"ok": False, "detail": "no pending approval for this run"}
    state.approval.set_result(bool(req.approved))
    state.status = "running"
    return {"ok": True, "approved": bool(req.approved)}


@app.get("/api/state/{run_id}")
async def get_state(run_id: str) -> dict[str, Any]:
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")
    return state.summary()


@app.get("/api/runs")
async def list_runs() -> dict[str, Any]:
    return {
        "runs": [
            {"id": s.id, "goal": s.goal, "status": s.status,
             "started": s.started, "events": len(s.bus.history)}
            for s in sorted(RUNS.values(), key=lambda s: -s.started)[:25]
        ]
    }


@app.get("/api/artifact/{run_id}")
async def get_artifact(run_id: str) -> dict[str, Any]:
    state = RUNS.get(run_id)
    if state is None:
        raise HTTPException(404, "unknown run")
    dossier = getattr(state, "dossier", None) or state.action
    if dossier is None or not dossier.output:
        raise HTTPException(404, "no artifact for this run yet")
    return {
        "markdown": dossier.output.get("decision_md", ""),
        "dir": dossier.output.get("dir", ""),
        "files": dossier.output.get("files", []),
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": settings.mode,
        "runs": len(RUNS),
        "subscribers": sum(s.bus.subscriber_count for s in RUNS.values()),
        "uptime_s": round(time.time() - _BOOT, 1),
    }


_BOOT = time.time()

# Static UI last so /api/* wins.
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
