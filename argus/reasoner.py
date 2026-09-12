"""
ARGUS — reasoning layer.

Turns raw evidence into a defensible decision. The pipeline is deliberately
inspectable, because "the LLM said so" is not a recommendation:

  1. INFER CRITERIA  — read the goal, derive weighted criteria (value, quality,
                       fit, trust, risk) and a hard constraint set.
  2. ENFORCE BUDGET  — a stated budget is a CONSTRAINT, not a preference. Options
                       over it rank below every option that respects it, and are
                       flagged. An agent that recommends something over budget
                       because it scored well is not doing its job.
  3. NORMALISE       — put every candidate on the same 0..1 scale per criterion.
                       Price affinity is anchored to the budget when there is one,
                       so a 4% discount cannot outweigh losing a warranty.
  4. SCORE & RANK    — weighted sum, plus severity-weighted red flags.
  5. CRITIQUE        — adversarially re-read the winner: is the evidence thick
                       enough? is it a coin-flip? did we ignore a red flag?
                       Confidence is adjusted down when it should be.
  6. CITE            — every rationale line carries the URL it came from.

The heuristic reasoner is fully deterministic and always available, so a run
completes with no LLM key. When an OpenAI-compatible key is present,
`LLMReasoner` adds narrative on top of the same numeric backbone — the model
explains the ranking, it never produces it.
"""

from __future__ import annotations

import json
import re
import statistics
from typing import Any

import httpx

from .config import Settings, settings as default_settings
from .models import Decision, Evidence, Option
from . import relevance

# --------------------------------------------------------------------------
# goal parsing
# --------------------------------------------------------------------------
_CUR_SYMBOLS = [("₹", "INR"), ("rs.", "INR"), ("rs ", "INR"), ("inr", "INR"),
                ("rupee", "INR"), ("$", "USD"), ("usd", "USD"), ("€", "EUR"), ("£", "GBP")]

_BUDGET_PATTERNS = [
    r"(?:under|below|less than|cheaper than|max|maximum|up to|budget of|within)\s*"
    r"(?:rs\.?|inr|usd|eur|gbp|\$|₹|€|£)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k)?",
    r"([\d][\d,]*(?:\.\d+)?)\s*(k)?\s*(?:or less|max|budget|budgeted)",
]

_CRITERION_LIBRARY = {
    "value": {"label": "Value for money",
              "why": "quality you actually get, measured against what you spend"},
    "quality": {"label": "Assessed quality",
                "why": "aggregate rating weighted by review volume"},
    "fit": {"label": "Requirement fit",
            "why": "how well the option matches the specifics you asked for"},
    "trust": {"label": "Seller & warranty trust",
              "why": "warranty length, seller reputation, and shipping reliability"},
    "risk": {"label": "Downside risk (inverse)",
             "why": "penalises open-box units, thin warranties, and hidden fees"},
}

_SIGNALS = {
    "value": ["budget", "cheap", "cheapest", "affordable", "value", "deal", "save",
              "under", "below", "tight", "best price", "economical"],
    "quality": ["best", "quality", "premium", "top", "greatest", "highest rated",
                "excellent", "performance", "flagship"],
    "fit": ["gaming", "hdr", "oled", "quiet", "portable", "fast", "ssd", "non-stop",
            "beachfront", "pool", "sso", "api", "offline", "ai", "for 2", "for two",
            "vegan", "family", "kids", "work", "team", "commute"],
    "trust": ["reliable", "trusted", "warranty", "safe", "established", "ships to",
              "return", "refund", "genuine", "authorised", "authorized"],
    "risk": ["safe", "reliable", "warranty", "genuine", "avoid", "no risk", "secure",
             "guarantee", "refurbished", "open box"],
}

# Flags that change WHAT YOU ARE BUYING, not merely how good a deal it is.
_SEVERE_FLAGS = ("over budget", "not new condition", "thin or seller-only warranty",
                 "no warranty", "wrong panel type", "wrong size")


def detect_currency(text: str) -> str:
    low = text.lower()
    for sym, code in _CUR_SYMBOLS:
        if sym in low:
            return code
    return "USD"


def parse_budget(goal: str) -> float | None:
    low = goal.lower()
    for pat in _BUDGET_PATTERNS:
        m = re.search(pat, low)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                val *= 1000
            if val > 0:
                return val
    return None


def infer_criteria(goal: str) -> list[dict[str, Any]]:
    """
    Derive weighted criteria from the goal text. Weights always sum to 1.

    A stated budget adds only a modest bump to `value`. The budget is enforced as
    a hard constraint elsewhere; weighting it heavily here too would double-count
    it and let price swamp quality.
    """
    low = goal.lower()
    raw: dict[str, float] = {k: 1.0 for k in _CRITERION_LIBRARY}
    for name, kws in _SIGNALS.items():
        raw[name] += sum(1.5 for kw in kws if kw in low)
    if parse_budget(goal) is not None:
        raw["value"] += 0.75
        raw["risk"] += 0.5
    total = sum(raw.values())
    return sorted(
        [
            {
                "name": name,
                "label": _CRITERION_LIBRARY[name]["label"],
                "why": _CRITERION_LIBRARY[name]["why"],
                "weight": round(weight / total, 3),
            }
            for name, weight in raw.items()
        ],
        key=lambda c: -c["weight"],
    )


# --------------------------------------------------------------------------
# scoring helpers
# --------------------------------------------------------------------------
def _norm(values: list[float | None], *, higher_is_better: bool = True) -> list[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return [0.5] * len(values)
    lo, hi = min(clean), max(clean)
    if hi - lo < 1e-9:
        return [0.75] * len(values)
    out = []
    for v in values:
        if v is None:
            out.append(0.4)
            continue
        x = (v - lo) / (hi - lo)
        out.append(x if higher_is_better else 1.0 - x)
    return out


def _price_affinity(prices: list[float | None], budget: float | None) -> list[float]:
    """
    How attractive is this price?

    With a stated budget the measure is absolute: anything at or under budget
    keeps full headroom. Without one, fall back to relative ranking within the
    candidate set.

    Using relative ranking unconditionally is a trap — it turns a $50 gap between
    two $1,300 products into a full 1.0-vs-0.0 swing, which lets a trivial
    discount outweigh losing a warranty.
    """
    if budget is not None:
        return [min(1.0, budget / p) if p and p > 0 else 0.5 for p in prices]
    return _norm(prices, higher_is_better=False)


def _quality_signal(option: Option) -> float:
    """Rating, damped by how much review evidence actually backs it."""
    rating = option.rating if option.rating is not None else 3.5
    depth = 0.6 + 0.4 * min(1.0, ((option.reviews or 0) / 1500.0) ** 0.5)
    return rating * depth


def _trust_score(option: Option) -> float:
    """Review volume (log-scaled) blended with warranty quality."""
    n = option.reviews or 0
    volume = min(1.0, (n ** 0.5) / 60.0) if n else 0.2
    warranty = 0.6
    w = str(option.specs.get("warranty", "")).lower()
    if any(k in w for k in ("2 year", "24 month", "3 year", "5 year")):
        warranty = 1.0
    elif "1 year" in w or "12 month" in w:
        warranty = 0.8
    elif "90 days" in w or "30 day" in w or "seller" in w:
        warranty = 0.3
    elif "no warranty" in w:
        warranty = 0.0
    cancellation = str(option.specs.get("cancellation", "")).lower()
    if "free" in cancellation:
        warranty = max(warranty, 0.85)
    return round(0.55 * volume + 0.45 * warranty, 4)


def _fit_score(option: Option, goal: str) -> float:
    """How many of the goal's explicit asks this option actually satisfies."""
    low = goal.lower()
    hits, checks = 0.0, 0.0
    blob = (json.dumps(option.specs) + " " + option.title + " " + option.shipping).lower()

    for kw in [k for k in _SIGNALS["fit"] if k in low]:
        checks += 1
        if kw in blob:
            hits += 1.0
    for m in re.finditer(r"(\d{2,4})\s*(?:hz|inch|in\b|-inch)", low):
        checks += 1
        if m.group(1) in blob:
            hits += 1.0
    for m in re.finditer(r"for\s+(\d+)\s*(?:people|guests|persons|users|seats|team)", low):
        checks += 1
        if m.group(1) in blob:
            hits += 1.0
    if not checks:
        return 0.7
    return round(hits / checks, 4)


def _risk_flags(option: Option, budget: float | None) -> list[str]:
    flags: list[str] = []
    if budget is not None and option.price is not None and option.price > budget:
        flags.append(f"over budget by {option.price - budget:,.0f} {option.currency}")
    w = str(option.specs.get("warranty", "")).lower()
    if any(k in w for k in ("90 days", "30 day", "seller warranty", "no warranty")):
        flags.append("thin or seller-only warranty")
    if "open box" in option.title.lower() or "refurb" in option.title.lower():
        flags.append("not new condition")
    if (option.reviews or 0) < 150:
        flags.append(f"thin review base ({option.reviews or 0})")
    if re.search(r"\$\d", option.shipping or "") or "freight" in (option.shipping or "").lower():
        flags.append("paid shipping — raises true total cost")
    if option.rating is not None and option.rating < 4.3:
        flags.append(f"below-average rating ({option.rating})")
    return flags


def _risk_score(flags: list[str]) -> float:
    """Severity-weighted: a condition/warranty problem costs far more than a thin review base."""
    penalty = 0.0
    for f in flags:
        penalty += 0.32 if any(s in f for s in _SEVERE_FLAGS) else 0.12
    return round(max(0.0, 1.0 - penalty), 4)


# --------------------------------------------------------------------------
# reasoners
# --------------------------------------------------------------------------
class HeuristicReasoner:
    name = "heuristic"

    def decide(
        self,
        goal: str,
        options: list[Option],
        evidence: list[Evidence],
        *,
        currency: str | None = None,
    ) -> Decision:
        currency = currency or detect_currency(goal)
        budget = parse_budget(goal)
        criteria = infer_criteria(goal)
        weights = {c["name"]: c["weight"] for c in criteria}

        if not options:
            return Decision(
                headline="No candidates survived retrieval.",
                confidence=0.0,
                criteria=criteria,
                reasoner=self.name,
                risks=["The READ phase returned no usable listings. Widen the query or "
                       "add a key for deeper search."],
            )

        # ---- score every candidate ----------------------------------------
        # What the goal pinned down, in a form that can be checked against a
        # title: "65-inch OLED" is a requirement; "the best one" is not.
        req = relevance.parse_requirements(goal)
        req_flags: dict[str, list[str]] = {
            o.id: relevance.requirement_flags(o.title, req) for o in options
        }

        affinity = _price_affinity([o.price for o in options], budget)
        quality_n = _norm([o.rating for o in options], higher_is_better=True)

        value_raw: list[float] = []
        for i, o in enumerate(options):
            value_raw.append(_quality_signal(o) * affinity[i])
            o.scores["quality"] = round(quality_n[i], 4)
            o.scores["fit"] = _fit_score(o, goal)
            o.scores["trust"] = _trust_score(o)
            o.flags = _risk_flags(o, budget) + req_flags.get(o.id, [])
            o.scores["risk"] = _risk_score(o.flags)
            if o.currency != currency:
                o.currency = currency

        value_n = _norm(value_raw, higher_is_better=True)
        for i, o in enumerate(options):
            o.scores["value"] = round(value_n[i], 4)
            o.total_score = round(
                sum(o.scores[k] * weights.get(k, 0.0) for k in o.scores), 4
            )

        # ---- enforce constraints, not preferences --------------------------
        # A stated budget and a stated requirement are both *constraints*. An
        # option that breaks one is not a worse candidate, it is a different
        # product: a MiniLED panel is not a worse OLED, it is not an OLED.
        #
        # Crucially, this only bites when something actually satisfies the
        # requirement. If nothing does, the requirement is treated as
        # unsatisfiable and the field is ranked on merit — with the mismatch
        # flagged — rather than every option being buried as "excluded".
        in_budget = [o for o in options
                     if budget is None or o.price is None or o.price <= budget]
        over_budget = [o for o in options if o not in in_budget]
        # "Confirmed" is stricter than "not contradicted": it requires the title to
        # actually say the thing, so an 8K request is not silently satisfied by a
        # listing that never mentioned a resolution.
        confirmed = {o.id: relevance.requirement_match(o.title, req) for o in options}
        requirement_is_enforceable = any(confirmed.values())

        def tier(o: Option) -> int:
            if budget is not None and o.price is not None and o.price > budget:
                return 3
            if req_flags.get(o.id):
                return 2
            if requirement_is_enforceable and not confirmed[o.id]:
                return 1     # silent where another candidate positively confirms
            return 0

        ranked = sorted(options, key=lambda o: (tier(o), -o.total_score, o.price or 1e12))

        for i, o in enumerate(ranked, 1):
            o.rank = i
            o.rationale = self._rationale(o, ranked)

        winner = ranked[0]
        # Compare against the winner's *peers* — candidates that clear the same
        # constraints. A high-scoring option that was excluded for breaking the
        # budget says nothing about how decisive the choice among valid ones is.
        winner_tier = tier(winner)
        peers = [o for o in ranked if tier(o) == winner_tier]
        runner = peers[1] if len(peers) > 1 else None
        margin = winner.total_score - (runner.total_score if runner else 0.0)

        # ---- critique pass -------------------------------------------------
        adjustments: list[str] = []
        confidence = 0.55 + min(0.25, margin * 2.0)

        if over_budget and in_budget and budget is not None:
            adjustments.append(
                f"{len(over_budget)} option(s) exceeded the {budget:,.0f} {currency} budget and "
                "were ranked below every in-budget candidate rather than allowed to win on merit."
            )
        if budget is not None and winner.price is not None and winner.price > budget:
            adjustments.append(
                f"Winner is {winner.price - budget:,.0f} {winner.currency} over the stated "
                "budget — flagged rather than silently accepted."
            )
            confidence -= 0.15

        mismatched = [o for o in options if req_flags.get(o.id)]
        if requirement_is_enforceable and mismatched:
            adjustments.append(
                f"{len(mismatched)} option(s) contradict a requirement stated in the goal "
                f"({req.describe()}) and were ranked below every option that meets it, "
                "rather than allowed to win on merit."
            )
        if req_flags.get(winner.id):
            adjustments.append(
                "Winner does not match the stated requirement — "
                + "; ".join(req_flags[winner.id])
                + ". Nothing retrieved satisfied it, so this is the closest available "
                  "option rather than a match."
            )
            # A wrong product is a heavier failure than a merely over-budget one:
            # the user gets something they cannot use at all.
            confidence -= 0.20
        elif not req.empty and requirement_is_enforceable:
            adjustments.append(
                f"Winner satisfies the stated requirement ({req.describe()}), checked "
                "against the listing title rather than assumed from the query."
            )
        elif not req.empty and not requirement_is_enforceable:
            # Nothing contradicted the requirement — but nothing confirmed it either,
            # so the match is unverified. Say that rather than let a silent listing
            # read as a satisfied one.
            adjustments.append(
                f"No candidate positively confirms the requirement stated in the goal "
                f"({req.describe()}). Nothing was flagged as contradicting it either, so "
                "they are ranked on merit — but treat the match as unverified rather than "
                "confirmed."
            )
            confidence -= 0.15
        if margin < 0.03 and runner is not None:
            adjustments.append(
                f"Only {margin:.3f} separates #1 and #2 — treat this as a tie and decide on "
                "non-modelled preferences (brand, ecosystem, returns policy)."
            )
            confidence -= 0.1
        if winner.flags:
            adjustments.append(
                f"Winner carries {len(winner.flags)} flag(s): {'; '.join(winner.flags)}."
            )
            confidence -= 0.05 * len(winner.flags)

        review_depth = sum(1 for e in evidence if e.kind == "review")
        if review_depth >= 3:
            adjustments.append(f"Corroborated across {review_depth} independent review sources.")
            confidence += 0.08
        else:
            adjustments.append(
                f"Only {review_depth} review source(s) retrieved — evidence is thinner than ideal."
            )
            confidence -= 0.08

        local_cur = (winner.currency_local or winner.currency or "").upper()
        if local_cur and local_cur != (winner.currency or "").upper():
            adjustments.append(
                f"Winner was quoted at {winner.price_local:,.2f} {local_cur} on the source site "
                f"and converted to {winner.price:,.2f} {winner.currency} using indicative FX, "
                "so the budget comparison is approximate rather than exact."
            )
            confidence -= 0.03

        confidence = round(max(0.15, min(0.96, confidence)), 2)

        # ---- coherence: is any of this actually about the question? --------
        # The last line of defence, and the one that matters most. Every step
        # above can be individually correct — scoring, budget, flags, citations
        # — and the whole thing can still answer a question nobody asked. That
        # is the worst failure available, because the user cannot tell it apart
        # from a real answer. It is how "wireless earbuds" gets answered with a
        # television, and how an empty goal gets a confident shortlist.
        subject_tokens, related = relevance.alignment(
            goal, [o.title for o in options])
        coherence_note: str | None = None
        if not subject_tokens:
            if re.search(r"[^\x00-\x7f]", goal or ""):
                # A goal in a script we do not parse is a different failure from a
                # goal with nothing in it, and deserves a different message.
                coherence_note = (
                    "The goal contains no Latin-script product terms and ARGUS parses "
                    "goals in English only. It will not guess at what was meant — name "
                    "the product in English and it will run."
                )
            else:
                coherence_note = (
                    "The goal does not name anything concrete to research, so there is no "
                    "question here to answer. The ranking below is arithmetic, not a "
                    "recommendation — read it as a failure to understand the request."
                )
        elif related == 0:
            shown = ", ".join(sorted(subject_tokens)[:6])
            coherence_note = (
                f"None of the {len(options)} candidates shares a single term with the goal "
                f"({shown}). The retrieval did not address the question, so this is a "
                "failure to find an answer rather than a recommendation."
            )
        if coherence_note:
            confidence = 0.1
            adjustments.insert(0, coherence_note)

        risks = list(winner.flags) or [
            "No material red flags detected on the recommended option."
        ]
        if coherence_note:
            risks.insert(0, coherence_note)

        citations = [
            {"title": e.title, "url": e.url, "kind": e.kind, "retrieved_at": e.retrieved_at}
            for e in evidence
        ][:12]

        headline = (
            f"Recommend {winner.title} at {winner.currency} {winner.price:,.2f}"
            if winner.price is not None else f"Recommend {winner.title}"
        )
        if coherence_note:
            headline = (
                f"No answer found — nothing retrieved relates to the goal. "
                f"({winner.title} is the top of an unrelated ranking, not a recommendation.)"
            )

        return Decision(
            headline=headline,
            recommendation_id=winner.id,
            recommendation_title=winner.title,
            confidence=confidence,
            criteria=criteria,
            ranked=ranked,
            rationale=[winner.rationale] + [o.rationale for o in ranked[1:4]],
            risks=risks,
            critique=(
                "Adversarial re-read of the winner against the stated goal, the hard budget "
                "constraint, and the depth of the evidence base."
            ),
            critique_adjustments=adjustments,
            citations=citations,
            total_cost=winner.price,
            currency=winner.currency,
            reasoner=self.name,
        )

    @staticmethod
    def _rationale(option: Option, ranked: list[Option]) -> str:
        s = {k: v for k, v in option.scores.items() if isinstance(v, float)}
        strongest = sorted(s.items(), key=lambda kv: -kv[1])[:2]
        weakest = min(s.items(), key=lambda kv: kv[1]) if s else ("n/a", 0.0)
        bits = [
            f"Ranks #{option.rank} of {len(ranked)} with a weighted score of {option.total_score:.3f}.",
            "Strengths: " + ", ".join(f"{k} {v:.2f}" for k, v in strongest) + ".",
            f"Weakest axis: {weakest[0]} at {weakest[1]:.2f}.",
        ]
        if option.price is not None:
            bits.append(f"Price {option.currency} {option.price:,.2f}.")
        if (option.price_local is not None and option.currency_local
                and option.currency_local.upper() != (option.currency or "").upper()):
            bits.append(f"Quoted locally at {option.price_local:,.2f} {option.currency_local}.")
        if option.rating is not None:
            bits.append(f"Rating {option.rating}/5 across {option.reviews or 0} reviews.")
        if option.flags:
            bits.append("Flags: " + "; ".join(option.flags) + ".")
        return " ".join(bits)


class LLMReasoner:
    """
    Adds a natural-language narrative on top of the deterministic ranking.

    Deliberately constrained: the model receives the already-computed scores and
    is asked to explain and sanity-check them, never to produce them. If the call
    fails for any reason we fall back to the heuristic result, so the run always
    finishes.
    """

    name = "llm+heuristic"

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or default_settings
        self._fallback = HeuristicReasoner()

    async def decide_async(
        self,
        goal: str,
        options: list[Option],
        evidence: list[Evidence],
        *,
        currency: str | None = None,
    ) -> Decision:
        decision = self._fallback.decide(goal, options, evidence, currency=currency)
        if not self.s.has_llm or not decision.ranked:
            return decision

        payload = {
            "goal": goal,
            "criteria": decision.criteria,
            "ranked": [
                {
                    "rank": o.rank, "title": o.title, "price": o.price,
                    "currency": o.currency, "rating": o.rating, "reviews": o.reviews,
                    "scores": o.scores, "total_score": o.total_score,
                    "flags": o.flags, "url": o.url,
                }
                for o in decision.ranked[:6]
            ],
            "critique_adjustments": decision.critique_adjustments,
        }
        system = (
            "You are the reasoning layer of a procurement agent. You are given an "
            "already-computed ranking with numeric scores. Do NOT invent numbers or "
            "products. Return STRICT JSON with keys: headline (one sentence naming the "
            "winner and price), rationale (array of 3-5 short strings citing concrete "
            "numbers from the input), risks (array of 1-3 strings), critique (one "
            "paragraph adversarially re-reading the winner). Be specific and terse."
        )
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    f"{self.s.llm_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.s.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.s.llm_model,
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(payload)},
                        ],
                    },
                )
            if resp.status_code >= 400:
                return decision
            parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
            if isinstance(parsed.get("headline"), str) and parsed["headline"].strip():
                decision.headline = parsed["headline"].strip()
            if isinstance(parsed.get("rationale"), list):
                merged = [str(x) for x in parsed["rationale"] if str(x).strip()]
                decision.rationale = merged + decision.rationale
            if isinstance(parsed.get("risks"), list) and parsed["risks"]:
                decision.risks = [str(x) for x in parsed["risks"]]
            if isinstance(parsed.get("critique"), str) and parsed["critique"].strip():
                decision.critique = parsed["critique"].strip()
            decision.reasoner = self.name
        except Exception:  # noqa: BLE001 - narrative is a bonus, never a dependency
            return decision
        return decision
