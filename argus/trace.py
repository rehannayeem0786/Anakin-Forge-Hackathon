"""
ARGUS — live trace bus.

The agent narrates itself. Every thought, tool call, observation, critique and
action is published as a typed event onto an async pub/sub bus. The web UI
subscribes over SSE and renders the trace as it happens — which is the whole
point: an agent you can *watch* reason is an agent you can trust.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field, asdict
from typing import Any

PHASES = ("read", "reason", "act")


@dataclass
class TraceEvent:
    seq: int
    ts: float
    kind: str                      # phase | thought | plan | tool_call | tool_result |
                                   # evidence | critique | decision | approval | action |
                                   # artifact | credits | error | info | run_start | run_end
    phase: str = "read"
    title: str = ""
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    level: str = "info"            # info | success | warn | error

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["clock"] = time.strftime("%H:%M:%S", time.localtime(self.ts))
        return d


class TraceBus:
    """Fan-out pub/sub with replay, so a late subscriber still sees the full run."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._subs: set[asyncio.Queue[TraceEvent | None]] = set()
        self.history: list[TraceEvent] = []
        self._lock = asyncio.Lock()

    def subscribe(self) -> asyncio.Queue[TraceEvent | None]:
        q: asyncio.Queue[TraceEvent | None] = asyncio.Queue(maxsize=4096)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[TraceEvent | None]) -> None:
        self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def emit(
        self,
        kind: str,
        title: str = "",
        detail: str = "",
        *,
        phase: str = "read",
        data: dict[str, Any] | None = None,
        level: str = "info",
    ) -> TraceEvent:
        ev = TraceEvent(
            seq=next(self._counter),
            ts=time.time(),
            kind=kind,
            phase=phase,
            title=title,
            detail=detail,
            data=data or {},
            level=level,
        )
        self.history.append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:  # pragma: no cover
                pass
        return ev

    def close(self) -> None:
        """Signal end-of-stream to every subscriber."""
        for q in list(self._subs):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    def replay(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.history]

    def reset(self) -> None:
        self.history.clear()
