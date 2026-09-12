#!/usr/bin/env python
"""
ARGUS — one command to run everything.

    python run.py                # start the app on http://127.0.0.1:8787
    python run.py --offline      # deterministic fixtures, no network
    python run.py --port 9000
    python run.py --demo         # run the flagship goal headless, print the trace

No API key required. No build step. No node_modules. That is deliberate.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

BANNER = r"""
   _    ____   ____ _   _ ____
  / \  |  _ \ / ___| | | / ___|
 / _ \ | |_) | |  _| | | \___ \
/ ___ \|  _ <| |_| | |_| |___) |
/_/   \_\_| \_\\____|\___/|____/
"""


def _c(code: str, text: str) -> str:
    if os.name == "nt" and not os.environ.get("WT_SESSION"):
        return text
    return f"\033[{code}m{text}\033[0m"


def banner(mode: str, has_key: bool, has_llm: bool, offline: bool) -> None:
    print(_c("36", BANNER))
    print(_c("1", "  Read the web. Reason it through. Get it done."))
    print()
    print(f"  version   : 1.0.0")
    print(f"  mode      : {_c('33', mode)}")
    print(f"  anakin key: {_c('32', 'present') if has_key else _c('33', 'absent — Zero Touch (no-key) tier')}")
    print(f"  llm key   : {_c('32', 'present') if has_llm else _c('33', 'absent — deterministic reasoner')}")
    if offline:
        print(f"  offline   : {_c('33', 'ON — deterministic fixtures, no network calls')}")
    print()


def run_headless(goal: str) -> int:
    """Run a goal end to end, streaming the trace. Used by --demo and CI."""
    from argus.agent import ArgusAgent, RunState
    from argus.config import settings

    # Unbuffered so `python run.py --demo > out.txt` shows progress, not nothing.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    state = RunState(goal, settings)
    level_colour = {"success": "32", "warn": "33", "error": "31"}

    def show(ev) -> None:
        tag = f"[{ev.phase:>6}]"
        kind = _c(level_colour.get(ev.level, "0"), f"{ev.kind:<28}")
        print(f"{tag} {kind} {ev.title}")
        if ev.detail:
            print(f"         {'':<28} {ev.detail[:150]}")

    async def go() -> RunState:
        agent = ArgusAgent(settings)
        task = asyncio.create_task(agent.run(state))
        q = state.bus.subscribe()
        while not task.done():
            try:
                ev = await asyncio.wait_for(q.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if ev is None:
                break
            show(ev)
            # Headless has no human, so auto-approve to exercise the full path.
            if (state.status == "awaiting_approval" and state.approval
                    and not state.approval.done()):
                state.approval.set_result(True)
                state.status = "running"
        while not q.empty():
            ev = q.get_nowait()
            if ev is not None:
                show(ev)
        state.bus.unsubscribe(q)
        return await task

    final = asyncio.run(go())
    print()
    if final.decision:
        d = final.decision
        print(_c("1", f"DECISION  {d.headline}"))
        print(f"confidence {d.confidence:.0%} · reasoner {d.reasoner}")
        for o in d.ranked[:5]:
            price = f"{o.currency} {o.price:,.2f}" if o.price is not None else "—"
            print(f"  #{o.rank}  {o.total_score:.3f}  {price:>16}  {o.title[:62]}")
        print()
    if final.action:
        print(f"ACTION    {final.action.title} → {final.action.status} "
              f"(mode={final.action.output.get('mode', 'n/a')})")
    if final.dossier and final.dossier.output.get("dir"):
        print(f"DOSSIER   {final.dossier.output['dir']}")
    print(f"CREDITS   {final.credits}")
    print(f"STATUS    {final.status}")
    return 0 if final.status == "done" else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="ARGUS — agentic research & action")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--offline", action="store_true", help="deterministic fixtures, no network")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ap.add_argument("--demo", action="store_true", help="run the flagship goal headless and exit")
    ap.add_argument("--goal", default=None, help="goal to use with --demo")
    ap.add_argument("--reload", action="store_true", help="dev auto-reload")
    args = ap.parse_args()

    if args.offline:
        os.environ["ARGUS_OFFLINE"] = "1"

    from argus.config import settings  # import after env is set

    host = args.host or settings.host
    port = args.port or settings.port

    if args.demo:
        goal = args.goal or (
            "I need a 65-inch OLED TV under $1,500 for gaming. "
            "Find the best one and prepare the purchase."
        )
        return run_headless(goal)

    banner(settings.mode, settings.has_key, settings.has_llm, settings.offline)

    url = f"http://{host}:{port}"
    print(f"  {_c('36', '→')} {url}")
    print(f"  {_c('2', 'Ctrl-C to stop')}\n")

    if not args.no_browser:
        def _open() -> None:
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=_open, daemon=True).start()

    import uvicorn
    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
