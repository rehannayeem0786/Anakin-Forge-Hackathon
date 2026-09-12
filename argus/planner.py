"""
ARGUS — planning layer.

A goal written in English becomes an executable plan. Two planners:

  HeuristicPlanner — deterministic. Reads the goal, derives the subject, the
                     domain, the query set and the terminal action. Always works.
  LLMPlanner       — asks an OpenAI-compatible model to propose extra search
                     intents, then validates them against the tool registry. If
                     anything is malformed or the call fails, we keep the
                     heuristic plan. The model can suggest, never hijack.

Note the deliberate asymmetry in the query set: alongside "best X" we always
plan a *negative* query ("X problems", "X complaints"). An agent that only
searches for reasons to buy is an agent doing marketing, not research.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Settings, settings as default_settings
from .models import Plan, Step
from . import money
from .reasoner import detect_currency

# Tool registry — what the agent is allowed to do, surfaced to the UI too.
TOOLS: dict[str, dict[str, str]] = {
    "wire_resolve": {
        "layer": "read",
        "label": "Resolve intent → action",
        "blurb": "Map a natural-language intent to a concrete Anakin Wire action_id.",
    },
    "wire_run": {
        "layer": "read",
        "label": "Run Wire action",
        "blurb": "Execute a read-only Wire action against a live site. No key needed.",
    },
    "search": {
        "layer": "read",
        "label": "AI web search",
        "blurb": "AI web search with citations and relevance scores.",
    },
    "scrape": {
        "layer": "read",
        "label": "Scrape URL",
        "blurb": "Fetch a page as clean markdown for grounding.",
    },
    "agentic_search": {
        "layer": "read",
        "label": "Agentic search",
        "blurb": "4-stage research pipeline: refine → search → scrape → synthesise.",
    },
    "score": {
        "layer": "reason",
        "label": "Score & rank",
        "blurb": "Normalise candidates on weighted criteria and rank them.",
    },
    "critique": {
        "layer": "reason",
        "label": "Self-critique",
        "blurb": "Adversarially re-read the winner and adjust confidence.",
    },
    "act": {
        "layer": "act",
        "label": "Execute action",
        "blurb": "Carry out the goal via the cloud browser, with human sign-off.",
    },
}

DOMAIN_HINTS: dict[str, list[str]] = {
    "commerce": ["buy", "purchase", "price", "cheap", "deal", "tv", "laptop", "phone",
                 "headphone", "monitor", "camera", "gpu", "order", "shop", "best price"],
    "travel": ["trip", "travel", "flight", "hotel", "stay", "vacation", "weekend",
               "book", "resort", "airbnb", "itinerary", "goa", "visa"],
    "software": ["software", "tool", "app", "saas", "crm", "platform", "vendor",
                 "subscription", "team", "notes", "compare tools", "cms"],
    "research": ["research", "analyse", "analyze", "report", "investigate", "landscape",
                 "competitor", "market", "due diligence", "who is", "explain"],
}

# Anakin's own catalog categories, mapped to the domains ARGUS understands.
# Used to rank all ~960 supported sites against a goal before touching any of them.
DOMAIN_CATEGORIES: dict[str, list[str]] = {
    "commerce": ["shopping", "ecommerce", "marketplace", "retail", "electronics",
                 "hardware", "fashion-beauty", "grocery", "automotive"],
    "travel": ["travel", "scheduling", "food-dining", "foodservice", "food_delivery"],
    "software": ["ai-tools", "developer-tools", "ai", "business-services", "marketing",
                 "research", "security", "web", "social"],
    "research": ["research", "news-media", "news", "finance", "government-legal",
                 "education", "social", "jobs", "crypto"],
    "general": ["shopping", "ecommerce", "marketplace", "research", "news-media",
                "ai-tools", "developer-tools", "travel"],
    # Used for the independent-evidence leg: sites carrying third-party opinion
    # rather than vendor copy. Deliberately narrow — a generic "research" pass
    # ranks clinicaltrials.gov above Reddit, which is not what a buyer needs.
    "discussion": ["social", "news-media", "news"],
}

# Brands that carry genuine user opinion, for the discussion leg.
DISCUSSION_BRANDS = [
    "reddit", "hackernews", "hacker-news", "ycombinator", "g2", "capterra",
    "trustpilot", "youtube", "stackoverflow", "quora", "producthunt",
    "glassdoor", "tripadvisor", "wikipedia",
]

# Brands a human would immediately recognise. A small, honest prior that keeps
# the fan-out from landing on an obscure reseller when a household name exists.
KNOWN_BRANDS = [
    "amazon", "ebay", "walmart", "best-buy", "bestbuy", "target", "flipkart", "etsy",
    "aliexpress", "alibaba", "costco", "homedepot", "ikea", "newegg", "bhphoto",
    "airbnb", "booking", "expedia", "skyscanner", "tripadvisor", "agoda", "kayak",
    "reddit", "hackernews", "github", "g2", "capterra", "trustpilot", "glassdoor",
    "youtube", "linkedin", "indeed", "producthunt", "stackoverflow", "wikipedia",
]

_STOP = {"the", "a", "an", "for", "and", "with", "under", "best", "good", "need",
         "want", "find", "get", "buy", "me", "my", "i", "to", "of", "in", "on",
         "that", "this", "is", "are", "it", "please", "some", "new", "2026", "2025"}


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.split(r"[^a-z0-9]+", text.lower())
        if len(t) > 2 and t not in _STOP
    }


def rank_catalogs(
    catalogs: list[dict[str, Any]],
    goal: str,
    domain: str,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """
    Rank every site Anakin supports against the goal, and return the best `limit`.

    This is the step that makes ARGUS a *platform* agent rather than a hardcoded
    scraper: it never assumes which sites matter, it asks the catalog and ranks.

    One signal here is not obvious and was learned the hard way. For a commerce
    goal the *market* matters as much as the category. Anakin's catalog holds
    amazon.in, amazon.ca, amazon.com.br, amazon.fr and seven more Amazon
    storefronts, and "amazon" is a brand prior — so a "$1,500 TV" goal ranked
    eight foreign Amazons into the top eight and never reached Best Buy or
    Walmart at all. The agent then did a *correct* cross-currency comparison of
    Indian rupees against a US dollar budget and recommended a TV the user
    could not buy.

    So: when the goal states a currency and the goal is a shopping goal, catalogs
    whose market currency matches are lifted, and the rest are pushed down. The
    penalty is deliberately smaller than the lift so a strong category fit can
    still win when nothing local exists.
    """
    prefs = DOMAIN_CATEGORIES.get(domain, DOMAIN_CATEGORIES["general"])
    brands = DISCUSSION_BRANDS if domain == "discussion" else KNOWN_BRANDS
    goal_tokens = _tokens(goal)
    goal_currency = detect_currency(goal) if domain == "commerce" else None
    scored: list[tuple[float, dict[str, Any]]] = []

    for c in catalogs:
        slug = str(c.get("slug") or "")
        if not slug:
            continue
        score = 0.0

        category = str(c.get("category") or "").lower()
        if category in prefs:
            # earlier categories in the preference list matter more
            score += 12.0 - 1.2 * prefs.index(category)

        name = str(c.get("name") or slug).lower()
        desc = str(c.get("description") or "").lower()
        blob = f"{name} {slug} {desc}"

        # explicit mention in the goal is decisive
        if slug in goal.lower().replace(" ", "-") or name in goal.lower():
            score += 25.0
        for brand in brands:
            if brand in blob:
                score += 6.0
                break

        overlap = len(goal_tokens & _tokens(blob))
        score += 3.5 * overlap

        # prefer catalogs with a real action surface, and keyless-friendly ones
        score += min(3.0, float(c.get("action_count") or 0) * 0.5)
        if not c.get("auth_required"):
            score += 4.0

        # market alignment — only meaningful when the user named a currency
        if goal_currency:
            catalog_currency = money.currency_for_domain(str(c.get("domain") or ""))
            if catalog_currency == goal_currency:
                score += 10.0
            else:
                score -= 9.0

        scored.append((score, c))

    scored.sort(key=lambda kv: -kv[0])
    return [c for _s, c in scored[:limit]]


_LEAD = re.compile(
    r"^\s*(?:please\s+)?(?:can you\s+|could you\s+|i(?:'d| would)?\s+(?:like|want|need)\s+"
    r"(?:you\s+)?to\s+|help me\s+|find me\s+|get me\s+|show me\s+|i need\s+|i want\s+|"
    r"let'?s\s+|go\s+find\s+|find\s+|get\s+|compare\s+|plan\s+|research\s+|look up\s+)",
    re.IGNORECASE,
)
_TAIL = re.compile(
    r"\s*(?:,|\band\b|\bthen\b)?\s*(?:that\s+)?(?:ships? to|under|below|less than|"
    r"for under|within|with|for|and|,).*$",
    re.IGNORECASE,
)
# Leading filler that makes a search query worse, not better.
_LEAD_NOISE = re.compile(
    r"^(?:the|a|an|best|top|good|great|cheapest|cheap|most|popular|new|some|any)\s+",
    re.IGNORECASE,
)


def detect_domain(goal: str) -> str:
    low = goal.lower()
    best, best_hits = "general", 0
    for domain, kws in DOMAIN_HINTS.items():
        hits = sum(1 for kw in kws if kw in low)
        if hits > best_hits:
            best, best_hits = domain, hits
    return best


def extract_subject(goal: str) -> str:
    """
    Pull the thing being researched out of a sentence, and strip the filler that
    makes a search query worse. "Find me the best wireless earbuds under $150"
    should become "wireless earbuds", not "the best wireless earbuds".
    """
    text = _LEAD.sub("", goal.strip())
    text = _TAIL.sub("", text).strip(" .,:;")
    for _ in range(4):
        stripped = _LEAD_NOISE.sub("", text).strip()
        if stripped == text or len(stripped) < 3:
            break
        text = stripped
    text = re.sub(r"\s+", " ", text)
    if len(text) < 3:
        text = goal.strip()
    return text[:90] or "the best option"


def infer_action_kind(goal: str, domain: str) -> str:
    low = goal.lower()
    if any(k in low for k in ("buy", "purchase", "order", "checkout", "add to cart")):
        return "checkout_form"
    if any(k in low for k in ("book", "reserve", "hold", "reservation")):
        return "booking_form"
    if any(k in low for k in ("monitor", "watch", "alert", "track", "notify")):
        return "monitor"
    return "artifact" if domain in {"research", "software", "general"} else "checkout_form"


def build_queries(subject: str, domain: str) -> list[dict[str, str]]:
    """The research query set, including the adversarial/negative leg."""
    year = "2026"
    base = [
        {"q": f"best {subject} {year}", "angle": "positive",
         "why": "establish the current consensus shortlist"},
        {"q": f"{subject} review comparison", "angle": "expert",
         "why": "get measured, comparable expert findings rather than marketing copy"},
        {"q": f"{subject} problems complaints", "angle": "negative",
         "why": "actively hunt for disqualifying issues before recommending"},
    ]
    if domain == "commerce":
        base.append({"q": f"{subject} price history worth it", "angle": "value",
                     "why": "check whether the current price is actually a good one"})
    if domain == "travel":
        base.append({"q": f"{subject} best time to go costs", "angle": "logistics",
                     "why": "surface seasonality and hidden costs"})
    if domain == "software":
        base.append({"q": f"{subject} hidden costs sso pricing", "angle": "value",
                     "why": "total cost of ownership rarely equals list price"})
    return base


class HeuristicPlanner:
    name = "heuristic"

    def plan(self, goal: str) -> Plan:
        domain = detect_domain(goal)
        subject = extract_subject(goal)
        action_kind = infer_action_kind(goal, domain)

        steps: list[Step] = []
        n = 1

        steps.append(Step(
            id=n, phase="read", tool="wire_resolve",
            args={"q": f"search {subject}"},
            why=f"Ask Anakin which of its 940+ pre-built site actions can actually answer "
                f"'{subject}'. We discover capabilities before assuming them.",
        )); n += 1

        steps.append(Step(
            id=n, phase="read", tool="search",
            args={"q": f"best {subject} 2026"},
            why="Independent, citable web search so the shortlist is not just vendor listings.",
        )); n += 1

        for q in build_queries(subject, domain)[2:]:
            steps.append(Step(
                id=n, phase="read", tool="search", args={"q": q["q"]},
                why=q["why"],
            )); n += 1

        steps.append(Step(
            id=n, phase="reason", tool="score",
            args={"subject": subject, "domain": domain},
            why="Normalise every candidate on weighted criteria derived from the goal, "
                "then rank. No hand-waving.",
        )); n += 1

        steps.append(Step(
            id=n, phase="reason", tool="critique",
            args={},
            why="Adversarially re-read the winner: in budget? enough evidence? coin-flip? "
                "confidence is adjusted down when it should be.",
        )); n += 1

        steps.append(Step(
            id=n, phase="act", tool="act",
            args={"kind": action_kind},
            why="Carry the decision through to a real action — with a human sign-off gate "
                "before anything irreversible.",
        ))

        return Plan(
            goal=goal,
            domain=domain,
            steps=steps,
            planner=self.name,
            rationale=(
                f"Domain '{domain}'; subject '{subject}'. Plan covers discovery, positive and "
                f"adversarial retrieval, weighted scoring, self-critique, then a terminal "
                f"'{action_kind}' action behind an approval gate."
            ),
        )


class LLMPlanner:
    """Suggests extra search intents. Cannot remove steps or change the terminal action."""

    name = "llm+heuristic"

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or default_settings
        self._base = HeuristicPlanner()

    async def plan_async(self, goal: str) -> Plan:
        plan = self._base.plan(goal)
        if not self.s.has_llm:
            return plan

        system = (
            "You are the planning layer of a research agent. Given a goal, propose up to 3 "
            "additional web search queries that would materially improve the research and are "
            "NOT already covered. Return STRICT JSON: {\"queries\": [{\"q\": str, \"why\": str}]}. "
            "Queries must be plain search strings. Prefer queries that surface risks, total cost "
            "of ownership, or independent measurement over marketing copy."
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self.s.llm_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.s.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.s.llm_model,
                        "temperature": 0.4,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps({
                                "goal": goal,
                                "domain": plan.domain,
                                "existing_queries": [
                                    s.args.get("q") for s in plan.steps if s.tool == "search"
                                ],
                            })},
                        ],
                    },
                )
            if resp.status_code >= 400:
                return plan
            parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
            existing = {s.args.get("q", "").lower() for s in plan.steps}
            extra: list[Step] = []
            for item in (parsed.get("queries") or [])[:3]:
                q = str(item.get("q", "")).strip()
                if not q or q.lower() in existing:
                    continue
                existing.add(q.lower())
                extra.append(Step(
                    id=0, phase="read", tool="search",
                    args={"q": q},
                    why=str(item.get("why") or "model-proposed query"),
                ))
            if extra:
                # insert before the first reason step, then renumber
                idx = next((i for i, s in enumerate(plan.steps) if s.phase != "read"), len(plan.steps))
                plan.steps[idx:idx] = extra
                for i, s in enumerate(plan.steps, 1):
                    s.id = i
                plan.planner = self.name
                plan.rationale += f" Model proposed {len(extra)} additional query leg(s)."
        except Exception:  # noqa: BLE001 - planning is resilient, never fatal
            return plan
        return plan
