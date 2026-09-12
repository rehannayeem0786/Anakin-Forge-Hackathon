"""
ARGUS — domain models.

Small, explicit objects. The point is that every recommendation ARGUS makes is
traceable: a Decision points at Options, Options point at Evidence, and Evidence
points at a live URL with a retrieval timestamp. No claim without a citation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any


def _now() -> str:
    return time.strftime("%H:%M:%S")


@dataclass
class Evidence:
    """One grounded fact pulled from the live web."""

    id: str
    kind: str              # "listing" | "review" | "page" | "search" | "catalog"
    title: str
    url: str
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    source_tool: str = ""
    credits: float = 0.0
    retrieved_at: str = field(default_factory=_now)
    confidence: float = 0.8

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Option:
    """A candidate the agent is choosing between."""

    id: str
    title: str
    url: str
    price: float | None = None          # normalised into the goal's currency
    currency: str = "USD"               # currency of `price`
    price_local: float | None = None    # as quoted by the source site
    currency_local: str = ""            # currency as quoted by the source site
    rating: float | None = None
    reviews: int | None = None
    source: str = ""
    specs: dict[str, Any] = field(default_factory=dict)
    shipping: str = ""
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    rank: int = 0
    flags: list[str] = field(default_factory=list)
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Decision:
    """The reasoned output: a pick, the runner-up, and why."""

    headline: str = ""
    recommendation_id: str = ""
    recommendation_title: str = ""
    confidence: float = 0.0
    criteria: list[dict[str, Any]] = field(default_factory=list)
    ranked: list[Option] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    critique: str = ""
    critique_adjustments: list[str] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    # Retrieved listings that were not candidates — accessories, parts, things
    # that merely mention the product. Kept for the audit trail so a reader can
    # see what was thrown away and why, rather than trusting the shortlist.
    excluded_listings: list[str] = field(default_factory=list)
    total_cost: float | None = None
    currency: str = "USD"
    reasoner: str = "heuristic"

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ranked"] = [o.as_dict() for o in self.ranked]
        return d


@dataclass
class Step:
    """One planned action in the agent's plan."""

    id: int
    phase: str             # "read" | "reason" | "act"
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    status: str = "pending"   # pending | running | done | failed | skipped
    result_summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    goal: str
    domain: str = "general"
    steps: list[Step] = field(default_factory=list)
    planner: str = "heuristic"
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "domain": self.domain,
            "planner": self.planner,
            "rationale": self.rationale,
            "steps": [s.as_dict() for s in self.steps],
        }


@dataclass
class ActionRecord:
    """An action ARGUS took, or is waiting for a human to approve."""

    id: str
    kind: str              # "browser_form" | "artifact" | "webhook" | "monitor"
    title: str
    detail: str = ""
    reversible: bool = True
    requires_approval: bool = True
    status: str = "proposed"   # proposed | awaiting_approval | approved | executed | rejected | failed
    payload: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
