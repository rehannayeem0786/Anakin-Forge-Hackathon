"""
ARGUS — the agent.

One goal in. A grounded decision and a completed action out. The loop is
deliberately three-beat, matching the brief:

    READ    ask Anakin's catalog which of its ~960 sites matter, resolve the
            concrete action per site, run them live, then search for independent
            evidence — including adversarial evidence — and scrape the primary
            sources behind the best citations.
    REASON  normalise, weight, rank, then adversarially critique the winner.
    ACT     propose an action; block on human approval if it is irreversible;
            execute; write an auditable dossier.

Everything it does is published onto the TraceBus as it happens, so the UI can
show the work rather than just the answer.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from .anakin_client import (
    AnakinClient,
    CallResult,
    build_params,
    is_query_capable,
    is_query_param,
    is_satisfiable,
    is_zero_touch_runnable,
    normalize_actions,
)
from .config import Settings, settings as default_settings
from .executor import ActionExecutor
from .models import Decision, Evidence, Option
from . import money
from . import relevance
from .planner import HeuristicPlanner, LLMPlanner, detect_domain, extract_subject, rank_catalogs
from .reasoner import HeuristicReasoner, LLMReasoner, detect_currency, parse_budget
from .trace import TraceBus


class RunState:
    """Mutable state for a single agent run. The server holds one of these."""

    def __init__(self, goal: str, settings: Settings | None = None) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.goal = goal
        self.settings = settings or default_settings
        self.bus = TraceBus()
        self.status = "running"           # running | awaiting_approval | done | error
        self.error: str | None = None
        self.plan = None
        self.decision: Decision | None = None
        self.action = None
        self.dossier: Any = None
        self.evidence: list[Evidence] = []
        self.options: list[Option] = []
        self.credits: dict[str, Any] = {}
        self.started = time.time()
        self.finished: float | None = None
        self.approval: asyncio.Future[bool] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "error": self.error,
            "mode": self.settings.mode,
            "duration_s": round((self.finished or time.time()) - self.started, 1),
            "evidence_count": len(self.evidence),
            "option_count": len(self.options),
            "decision": self.decision.as_dict() if self.decision else None,
            "action": self.action.as_dict() if self.action else None,
            "dossier": self.dossier.as_dict() if self.dossier else None,
            "plan": self.plan.as_dict() if self.plan else None,
            "credits": self.credits,
            "trace": self.bus.replay(),
        }


# --------------------------------------------------------------------------
# tolerant extraction — real Wire payload shapes vary per catalog
# --------------------------------------------------------------------------
_TITLE_KEYS = ("title", "name", "product", "product_name", "productName", "listing",
               "label", "item", "headline", "story_title")
# Ordered by preference: what you would actually pay comes first. Costco
# publishes `delivery_price` and `warehouse_price` and no `price` at all, so a
# schema-naive extractor sees a $1,300 television as priceless and discards it.
_PRICE_KEYS = ("price", "sale_price", "current_price", "price_value", "priceValue",
               "delivery_price", "warehouse_price", "list_price", "original_price",
               "amount", "value", "cost", "price_display")
_CURRENCY_KEYS = ("currency", "price_currency", "priceCurrency", "currencyCode",
                  "currency_code")
_RATING_KEYS = ("rating", "stars", "score", "average_rating", "averageRating",
                "upvote_ratio")
_REVIEW_KEYS = ("reviews", "review_count", "ratings_count", "reviews_count",
                "rating_count", "ratings_total", "total_reviews", "reviewCount",
                "num_reviews", "num_comments", "reviewsCount", "votes", "points")
_URL_KEYS = ("url", "link", "href", "product_url", "productUrl", "listing_url",
             "permalink", "hn_url", "webUrl")


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (dict, list)):
        # A nested object is not a number. `_price_and_currency` handles the
        # {"value": .., "currency": ..} shape properly; coercing it here with
        # str() would scrape digits back out and silently accept "3" from
        # {'currency': 'INR'} as a price.
        return None
    s = str(v).strip()
    cleaned = "".join(c for c in s if c.isdigit() or c in ".-")
    if cleaned.count(".") > 1:
        head, _, tail = cleaned.rpartition(".")
        cleaned = head.replace(".", "") + "." + tail
    cleaned = cleaned.lstrip("-") if cleaned.count("-") > 1 else cleaned
    try:
        return float(cleaned)
    except ValueError:
        return None


def _price_and_currency(item: dict[str, Any]) -> tuple[float | None, str]:
    """
    Pull (price, currency) out of any listing shape.

    Amazon's storefronts nest it — `"price": {"value": 155990.0, "currency":
    "INR"}` — while Walmart, Best Buy and eBay use a flat `price` plus a separate
    `currency` / `price_currency`. Both are normalised here so nothing depends
    on which retailer the catalog ranking happened to pick.
    """
    raw: Any = None
    for k in _PRICE_KEYS:
        v = item.get(k)
        if v not in (None, "", [], {}):
            raw = v
            break

    currency = ""
    for k in _CURRENCY_KEYS:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            currency = v.strip().upper()
            break

    if isinstance(raw, dict):
        value: float | None = None
        for k in ("value", "amount", "price", "current_price", "raw"):
            if raw.get(k) not in (None, ""):
                value = _as_float(raw.get(k))
                break
        if not currency:
            nested = (raw.get("currency") or raw.get("currencyCode")
                      or raw.get("price_currency"))
            if isinstance(nested, str) and nested.strip():
                currency = nested.strip().upper()
        return value, currency

    return _as_float(raw), currency


def _walk_lists(node: Any, depth: int = 0) -> list[list[dict[str, Any]]]:
    """Collect every list-of-dicts anywhere in a JSON payload."""
    found: list[list[dict[str, Any]]] = []
    if depth > 6:
        return found
    if isinstance(node, list):
        dicts = [x for x in node if isinstance(x, dict)]
        if dicts:
            found.append(dicts)
        for x in node:
            found.extend(_walk_lists(x, depth + 1))
    elif isinstance(node, dict):
        for v in node.values():
            found.extend(_walk_lists(v, depth + 1))
    return found


def _best_group(payload: dict[str, Any], want_priced: bool) -> list[dict[str, Any]]:
    best: list[dict[str, Any]] = []
    for group in _walk_lists(payload):
        if want_priced and not any(_first(it, _PRICE_KEYS) is not None for it in group):
            # A group with no prices anywhere is a thread list, an article list
            # or a nav menu — never a set of purchasable candidates. Without
            # this, a Reddit thread titled "LG OLEDs Have A Serious Problem"
            # became a candidate *product* with `price = None`, and a missing
            # price reads as "in budget" in every downstream comparison.
            continue
        score = 0
        for item in group:
            has_title = _first(item, _TITLE_KEYS) is not None
            has_price = _first(item, _PRICE_KEYS) is not None
            has_rating = _first(item, _RATING_KEYS) is not None
            has_url = _first(item, _URL_KEYS) is not None
            if not has_title:
                continue
            if want_priced:
                score += 2 if has_price else 0
                score += 1 if has_rating else 0
                score += 1 if has_url else 0
            else:
                score += 1 if has_url else 0
        if score > len(best):
            best = group
    return best


def extract_listings(
    result: CallResult,
    source_hint: str = "",
    currency_hint: str = "",
) -> list[dict[str, Any]]:
    """Pull normalised purchasable candidates out of any Wire response shape."""
    out: list[dict[str, Any]] = []
    for item in _best_group(result.data, want_priced=True)[:20]:
        title = _first(item, _TITLE_KEYS)
        if not title:
            continue
        price_value, price_currency = _price_and_currency(item)
        # Currency precedence: explicit field (flat or nested) > symbol in the
        # price string > the catalog's domain TLD > USD.
        raw_price = _first(item, _PRICE_KEYS)
        cur = (
            price_currency
            or money.currency_from_text(
                "" if isinstance(raw_price, (dict, list)) else str(raw_price)
            )
            or currency_hint
            or money.DEFAULT_CURRENCY
        )
        specs = {
            k: v for k, v in item.items()
            if k not in set(_TITLE_KEYS + _PRICE_KEYS + _CURRENCY_KEYS
                            + _RATING_KEYS + _REVIEW_KEYS + _URL_KEYS)
            and isinstance(v, (str, int, float, bool))
        }
        if price_value is None:
            # An unpriced item is not a candidate. It is still captured as
            # evidence by `extract_signals` — it just cannot be scored against a
            # budget, and treating "no price" as "in budget" is precisely how a
            # review article outranks a television.
            continue
        out.append({
            "title": str(title)[:160],
            "price": price_value,
            "currency": cur,
            "rating": _as_float(_first(item, _RATING_KEYS)),
            "reviews": int(_as_float(_first(item, _REVIEW_KEYS)) or 0),
            "url": str(_first(item, _URL_KEYS) or ""),
            "source": source_hint or str(item.get("source") or item.get("catalog") or ""),
            "specs": specs,
            "shipping": str(item.get("shipping") or item.get("delivery") or ""),
        })
    return out


def extract_signals(result: CallResult, source_hint: str = "") -> list[dict[str, Any]]:
    """
    Pull non-purchasable structured items (forum threads, articles, launches).
    Keeps ARGUS useful for research goals, not just shopping ones.
    """
    out: list[dict[str, Any]] = []
    for item in _best_group(result.data, want_priced=False)[:15]:
        title = _first(item, _TITLE_KEYS)
        url = _first(item, _URL_KEYS)
        # Some catalogs (Wikipedia among them) return a page id and no URL.
        # Reconstructing a stable citation beats discarding a good source.
        if not url:
            pageid = item.get("pageid") or item.get("page_id")
            if pageid and "wiki" in (source_hint or "").lower():
                url = f"https://en.wikipedia.org/?curid={pageid}"
        if not title or not url:
            continue
        body = _first(item, ("selftext", "story_text", "comment_text", "text",
                             "description", "snippet", "summary")) or ""
        out.append({
            "title": str(title)[:180],
            "url": str(url),
            "text": str(body)[:800],
            "score": _as_float(_first(item, _REVIEW_KEYS)),
            "source": source_hint,
        })
    return out


# Which Wire actions are worth spending a call on.
_WRITEY = ("add_to_cart", "checkout", "buy", "purchase", "place_order", "remove",
           "update", "create", "delete", "post", "comment", "send", "login",
           "subscribe", "cancel", "submit", "add_item", "clear_cart")
_WEAK = ("suggest", "autocomplete", "sitemap", "navigation", "homepage", "menu",
         "store_locator", "categories")


def _action_rank(a: dict[str, Any]) -> float:
    """
    Rank candidate actions. Non-query-capable actions are disqualified outright —
    an action whose only inputs are an `asin` or a browse-node ID cannot answer
    "find me X", no matter how good its name sounds.
    """
    if not is_query_capable(a) or not is_satisfiable(a):
        return -1000.0

    blob = " ".join([
        str(a.get("action_id", "")), str(a.get("name", "")),
        str(a.get("description", "")), " ".join(a.get("tags") or []),
    ]).lower()

    score = 12.0
    if "search" in blob:
        score += 8.0
    if "listing" in blob or "listings" in blob:
        score += 3.0
    if "browse" in blob or "category" in blob:
        score += 1.0
    for k in _WEAK:
        if k in blob:
            score -= 8.0
    for k in _WRITEY:
        if k in blob:
            score -= 40.0

    pnames = " ".join(str(p.get("name", "")).lower() for p in a.get("params") or [])
    if "max_price" in pnames or "min_price" in pnames:
        score += 3.0        # a genuine shopping search, budget-aware
    if "sort" in pnames:
        score += 1.0
    if "limit" in pnames:
        score += 0.5
    score -= min(4.0, float(a.get("credits") or 1))
    return score


def _select_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    capable = [a for a in actions if is_query_capable(a) and is_satisfiable(a)]
    if not capable:
        return None
    capable.sort(key=_action_rank, reverse=True)
    return capable[0]


class ArgusAgent:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or default_settings
        self.planner = LLMPlanner(self.s) if self.s.has_llm else HeuristicPlanner()
        self.reasoner = LLMReasoner(self.s) if self.s.has_llm else HeuristicReasoner()
        self.executor = ActionExecutor(self.s)

    # ------------------------------------------------------------------ public
    async def run(self, state: RunState) -> RunState:
        bus = state.bus

        def emit(kind: str, title: str = "", detail: str = "", **kw: Any):
            return bus.emit(kind, title, detail, **kw)

        emit("run_start", "ARGUS engaged", f'Goal: "{state.goal}"', phase="read",
             data={"goal": state.goal, "mode": self.s.mode,
                   "anakin_key": self.s.has_key, "llm": self.s.has_llm},
             level="success")

        try:
            async with AnakinClient(self.s) as client:
                plan = (await self.planner.plan_async(state.goal)
                        if hasattr(self.planner, "plan_async")
                        else self.planner.plan(state.goal))
                state.plan = plan
                emit("plan", f"Plan: {len(plan.steps)} steps", plan.rationale,
                     phase="read",
                     data={"planner": plan.planner, "domain": plan.domain,
                           "steps": [s.as_dict() for s in plan.steps]})

                await self._phase_read(state, client, emit)
                await self._phase_reason(state, client, emit)
                await self._phase_act(state, client, emit)

                state.credits = client.ledger.as_dict()
                state.credits["trial_credits_remaining"] = client.trial_credits
                state.credits["signup_url"] = client.signup_url
                emit("credits", "Anakin credit ledger",
                     f"{state.credits['calls']} calls · "
                     f"{state.credits['spent']:g} credits spent · "
                     f"{state.credits['free_calls']} free (Zero Touch)",
                     phase="act", data=state.credits)

            state.status = "done"
            state.finished = time.time()
            if state.decision is not None:
                closing = (
                    f"{len(state.evidence)} evidence items · "
                    f"{len(state.options)} candidates · confidence "
                    f"{state.decision.confidence:.0%}"
                )
            else:
                closing = "completed with no decision"
            emit("run_end", "Run complete", closing, phase="act", level="success",
                 data={"duration_s": round(state.finished - state.started, 1)})
        except Exception as exc:  # noqa: BLE001 - surface, never swallow
            state.status = "error"
            state.error = f"{type(exc).__name__}: {exc}"
            state.finished = time.time()
            emit("error", "Run failed", state.error, phase="read", level="error")
        finally:
            bus.close()
        return state

    # ------------------------------------------------------------------- READ
    async def _phase_read(self, state: RunState, client: AnakinClient, emit: Any) -> None:
        emit("phase", "READ",
             "Asking Anakin which sites matter, then pulling live data.",
             phase="read")

        subject = extract_subject(state.goal)
        domain = detect_domain(state.goal)

        # --- 1. capability discovery over the whole catalog ---------------
        emit("tool_call", "wire_catalog", "list every supported site", phase="read",
             data={"tool": "wire_catalog"})
        cat = await client.wire_catalog()
        catalogs = (cat.data.get("catalog") or []) if cat.ok else []
        total_actions = sum(int(c.get("action_count") or 0) for c in catalogs)
        emit("tool_result", f"{len(catalogs)} sites · {total_actions:,} actions available",
             f"{cat.endpoint} · {cat.elapsed_ms}ms · free (no key)", phase="read",
             data={"result": cat.as_dict(),
                   "sample": [{"slug": c.get("slug"), "name": c.get("name"),
                               "category": c.get("category"),
                               "action_count": c.get("action_count")}
                              for c in catalogs[:12]]},
             level="success" if catalogs else "warn")

        # --- 1b. resolve: what does Anakin itself think this goal needs? ---
        # The published plan starts here, so it runs here. It is free, keyless
        # and fast (~0.5s), and it is the only surface that maps raw intent
        # straight onto concrete action ids.
        #
        # It is also close to useless for this class of goal — and *saying so* is
        # the point. Asked for "65 inch OLED TV", the live API answers with TMDB
        # and the A.V. Club, because it matches the word "TV" and not the thing.
        # So ARGUS calls it, shows the answer, and then declines to trust it.
        # A tool you have measured beats a tool you have assumed.
        emit("tool_call", "wire_resolve", f'resolve intent "{subject}"', phase="read",
             data={"tool": "wire_resolve"})
        resolve = await client.wire_resolve(f"search {subject}")
        resolved_actions = normalize_actions(resolve.data) if resolve.ok else []
        resolved_ids = {str(a.get("action_id") or "") for a in resolved_actions}
        emit("tool_result",
             f"wire_resolve → {len(resolved_actions)} candidate action(s)",
             (f"{resolve.endpoint} · {resolve.elapsed_ms}ms · free (no key) · "
              + (" · ".join(f"{a['catalog_slug']}/{a['action_id']}"
                            for a in resolved_actions[:4]) or "no match"))
             if resolve.ok else (resolve.error or "failed"),
             phase="read",
             data={"result": resolve.as_dict(),
                   "resolved": [{"action_id": a["action_id"],
                                 "catalog": a["catalog_slug"],
                                 "auth_required": a["auth_required"]}
                                for a in resolved_actions]},
             level="success" if resolve.ok else "warn")

        # --- 2. rank the catalog against the goal -------------------------
        # Fan out wider than the shortlist needs: some catalogs have no
        # query-capable action at all, and some have one whose upstream site is
        # down. A narrow fan-out means a single dead retailer silently shrinks
        # the evidence base.
        ranked = rank_catalogs(catalogs, state.goal, domain,
                               limit=max(6, min(12, self.s.max_parallel * 3)))
        emit("thought",
             f"Ranked {len(catalogs)} sites → top {len(ranked)} for this goal",
             "ARGUS never assumes which sites matter. It scores the whole catalog on "
             "category fit, brand recognition, goal-token overlap and whether the site "
             "can run keyless — then picks targets. "
             + " · ".join(f"{c.get('name')}" for c in ranked[:8]),
             phase="read",
             data={"ranked": [{"slug": c.get("slug"), "name": c.get("name"),
                               "category": c.get("category"),
                               "actions": c.get("action_count")} for c in ranked]})

        # --- 3. resolve the concrete action per site ----------------------
        async def detail(c: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            slug = str(c.get("slug") or "")
            actions = await client.wire_actions_for_catalog(slug)
            return c, actions

        details = await asyncio.gather(*(detail(c) for c in ranked),
                                       return_exceptions=True)

        budget = parse_budget(state.goal)
        currency = detect_currency(state.goal)

        chosen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        skipped: list[str] = []
        for item in details:
            if isinstance(item, BaseException):
                continue
            c, actions = item
            runnable = [a for a in actions if is_zero_touch_runnable(a)]
            best = _select_action(runnable)
            if best is None:
                skipped.append(str(c.get("name") or c.get("slug")))
                emit("tool_result",
                     f"{c.get('name')} · skipped",
                     f"{len(runnable)} keyless actions, but none accepts a free-text query "
                     f"— cannot answer the goal without a product ID first.",
                     phase="read", level="warn",
                     data={"catalog": c.get("slug"),
                           "actions": [a["action_id"] for a in runnable[:8]]})
                continue
            chosen.append((c, best))
            emit("tool_result",
                 f"{c.get('name')} · {len(actions)} actions, {len(runnable)} keyless",
                 f"selected `{best['action_id']}` — {best.get('name')} "
                 f"({best.get('credits')} cr) · accepts query via "
                 f"`{', '.join(p['name'] for p in best['params'] if is_query_param(p))}`",
                 phase="read",
                 data={"catalog": c.get("slug"), "action": best,
                       "all_actions": [a["action_id"] for a in actions[:10]]},
                 level="success")

        emit("thought", f"{len(chosen)} query-capable actions selected",
             "Hard gate: an action must expose a free-text parameter to be used for "
             "discovery — otherwise it can only fetch a record we don't have yet. "
             "Zero Touch runs these with no key and no account."
             + (f" Skipped: {', '.join(skipped)}." if skipped else ""),
             phase="read")

        # Reconcile the two independent discovery paths, out loud.
        selected_ids = {str(a["action_id"]) for _c, a in chosen}
        agreed = sorted(resolved_ids & selected_ids)
        if resolved_ids and chosen:
            emit("thought",
                 f"Two discovery paths agree on {len(agreed)} of {len(chosen)} actions",
                 ("Where they agree, that is genuine corroboration — resolve's intent ranking "
                  "and the catalog-derived selection landed on the same action independently."
                  if agreed else
                  "Resolve surfaced none of the selected actions, and that is the honest "
                  "result: it ranks by keyword and matched the word rather than the product. "
                  "The catalog-derived selection is authoritative here, and resolve is kept "
                  "as a cross-check rather than treated as an answer."),
                 phase="read",
                 data={"agreed": agreed, "selected": sorted(selected_ids),
                       "resolved": sorted(x for x in resolved_ids if x)},
                 level="success" if agreed else "info")

        # --- 4. execute the fan-out in parallel ---------------------------
        if chosen:
            async def run_one(c: dict[str, Any], a: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], CallResult]:
                params = build_params(a, subject, budget=budget, currency=currency)
                r = await client.wire_run(str(a["action_id"]), params)
                return c, a, r

            results = await asyncio.gather(*(run_one(c, a) for c, a in chosen),
                                           return_exceptions=True)
            for item in results:
                if isinstance(item, BaseException):
                    emit("tool_result", "Wire action raised", str(item),
                         phase="read", level="warn")
                    continue
                c, a, r = item
                catalog = str(c.get("name") or c.get("slug"))
                if not r.ok:
                    emit("tool_result", f"{catalog} · {a['action_id']}",
                         r.error or "failed", phase="read", level="warn",
                         data={"result": r.as_dict()})
                    continue

                cur_hint = money.currency_for_domain(str(c.get("domain") or ""))
                listings = extract_listings(
                    r, source_hint=str(c.get("slug") or catalog), currency_hint=cur_hint)
                signals = extract_signals(r, source_hint=str(c.get("slug") or catalog))
                emit("tool_result",
                     f"{catalog} · {len(listings)} listings, {len(signals)} signals",
                     f"{a['action_id']} · {r.elapsed_ms}ms · {r.credits:g} credits"
                     f" · market {cur_hint}",
                     phase="read",
                     data={"result": r.as_dict(),
                           "preview": [{"title": l["title"], "price": l["price"],
                                        "currency": l["currency"], "rating": l["rating"]}
                                       for l in listings[:4]]},
                     level="success" if (listings or signals) else "warn")

                for l in listings:
                    ev = Evidence(
                        id=f"ev_{uuid.uuid4().hex[:8]}", kind="listing",
                        title=l["title"], url=l["url"],
                        content=json.dumps(l, default=str)[:1200], data=l,
                        source_tool=f"wire_run:{a['action_id']}",
                        credits=r.credits / max(1, len(listings)), confidence=0.85,
                    )
                    state.evidence.append(ev)
                    emit("evidence", l["title"],
                         f"{money.format_money(l['price'], l['currency'])} · {catalog}",
                         phase="read", data=ev.as_dict())

                for sg in signals:
                    ev = Evidence(
                        id=f"ev_{uuid.uuid4().hex[:8]}", kind="signal",
                        title=sg["title"], url=sg["url"], content=sg["text"],
                        data=sg, source_tool=f"wire_run:{a['action_id']}",
                        credits=r.credits / max(1, len(signals)), confidence=0.7,
                    )
                    state.evidence.append(ev)
                    emit("evidence", sg["title"], (sg["text"] or "")[:180],
                         phase="read", data=ev.as_dict())

        # --- 5. independent + adversarial search --------------------------
        queries = [s.args.get("q") for s in state.plan.steps
                   if s.tool == "search" and s.args.get("q")]
        # Short and clean beats the plan's richer phrasing for community search:
        # forum engines match poorly against long natural-language sentences.
        community_query = f"{subject} problems"

        if queries and (self.s.has_key or self.s.offline):
            emit("thought", f"{len(queries)} search legs, including an adversarial one",
                 "Searching for reasons NOT to buy, not just reasons to. "
                 "Positive-only retrieval is marketing, not research.",
                 phase="read")

            async def search_one(q: str) -> tuple[str, CallResult]:
                return q, await client.search(q)

            for q, r in await asyncio.gather(*(search_one(q) for q in queries)):
                if not r.ok:
                    emit("tool_result", f"search · {q}", r.error or "failed",
                         phase="read", level="warn")
                    continue
                hits = r.data.get("results") or r.data.get("citations") or []
                emit("tool_result", f"search · {len(hits)} sources",
                     f'"{q}" · {r.elapsed_ms}ms', phase="read",
                     data={"result": r.as_dict(),
                           "hits": [{"title": h.get("title"), "url": h.get("url"),
                                     "snippet": (h.get("snippet") or "")[:220],
                                     "score": h.get("score")} for h in hits[:5]]},
                     level="success" if hits else "warn")
                for h in hits:
                    ev = Evidence(
                        id=f"ev_{uuid.uuid4().hex[:8]}", kind="review",
                        title=str(h.get("title") or q),
                        url=str(h.get("url") or ""),
                        content=str(h.get("snippet") or ""),
                        data={"query": q, "score": h.get("score")},
                        source_tool="search",
                        credits=r.credits / max(1, len(hits)),
                        confidence=float(h.get("score") or 0.75),
                    )
                    state.evidence.append(ev)
                    emit("evidence", ev.title, ev.content[:180], phase="read",
                         data=ev.as_dict())

            # Keyed upgrade: Anakin's 4-stage agentic pipeline
            # (refine → search → scrape → synthesise). It costs 10 credits plus
            # 1/URL, so it runs once on the primary query rather than per leg —
            # and only with a key, because Zero Touch does not serve it.
            if not self._out_of_budget(state, emit, "agentic research"):
                emit("tool_call", "agentic_search", f'refine + research "{subject}"',
                     phase="read", data={"tool": "agentic_search"})
                ar = await client.agentic_search(f"best {subject} 2026")
                if ar.ok:
                    stages = ar.data.get("stages") or []
                    cites = ar.data.get("citations") or []
                    emit("tool_result",
                         f"agentic_search → {len(stages)} stages, {len(cites)} citations",
                         f"{ar.endpoint} · {ar.elapsed_ms}ms · {ar.credits:g} credits",
                         phase="read",
                         data={"result": ar.as_dict(),
                               "stages": [s.get("stage") for s in stages],
                               "report_excerpt": str(ar.data.get("report") or "")[:600]},
                         level="success" if cites else "warn")
                    for c in cites:
                        url = str(c.get("url") or "")
                        if not url:
                            continue
                        ev = Evidence(
                            id=f"ev_{uuid.uuid4().hex[:8]}", kind="review",
                            title=str(c.get("title") or url), url=url,
                            content=str(c.get("snippet") or c.get("summary") or "")[:800],
                            data={"channel": "agentic_search", **c},
                            source_tool="agentic_search", confidence=0.86,
                        )
                        state.evidence.append(ev)
                        emit("evidence", ev.title, ev.content[:180], phase="read",
                             data=ev.as_dict())
                else:
                    emit("tool_result", "agentic_search", ar.error or "failed",
                         phase="read", level="warn")
        elif queries:
            emit("thought", "Independent-evidence leg routed through Wire",
                 "Anakin's /search endpoint needs a key, so keyless runs substitute a "
                 "community-search leg over Wire's discussion sites. Same intent — "
                 "third-party opinion rather than vendor copy — no key required.",
                 phase="read")

        # --- 5b. keyless community evidence -------------------------------
        # Works with or without a key, and is the only independent-opinion source
        # available on the Zero Touch tier.
        if not self._out_of_budget(state, emit, "community search"):
            await self._community_evidence(state, client, catalogs, community_query, emit)

        # --- 6. ground on the primary sources behind the best citations ----
        review_urls = self._grounding_targets(state)
        if review_urls and not self._out_of_budget(state, emit, "source grounding"):
            emit("thought", f"Grounding on {len(review_urls)} primary sources",
                 "Scraping the full page behind the top citations so the decision rests "
                 "on primary text, not search snippets. Run in parallel, with a per-page "
                 "cap so a slow forum thread cannot stall the whole run.", phase="read")

            async def ground(url: str) -> tuple[str, CallResult]:
                return url, await client.scrape(url, timeout=self.s.scrape_timeout)

            for url, r in await asyncio.gather(*(ground(u) for u in review_urls)):
                if not r.ok:
                    emit("tool_result", "scrape", f"{url} · {r.error}",
                         phase="read", level="warn")
                    continue
                md = str(r.data.get("markdown") or "")
                ev = Evidence(
                    id=f"ev_{uuid.uuid4().hex[:8]}", kind="page",
                    title=url.split("/")[2] if "//" in url else url,
                    url=url, content=md[:4000],
                    data={"chars": len(md), "cached": r.data.get("cached")},
                    source_tool="scrape", credits=r.credits, confidence=0.9,
                )
                state.evidence.append(ev)
                emit("tool_result", f"scrape · {len(md):,} chars",
                     f"{url} · {r.elapsed_ms}ms", phase="read",
                     data={"result": r.as_dict(), "excerpt": md[:600]},
                     level="success")
                emit("evidence", ev.title, md[:200], phase="read", data=ev.as_dict())

        emit("thought", "READ complete",
             f"{len(state.evidence)} grounded items · "
             f"{sum(1 for e in state.evidence if e.kind == 'listing')} live listings · "
             f"{sum(1 for e in state.evidence if e.kind == 'review')} review sources",
             phase="read", level="success")

    # ------------------------------------------------------------ read helpers
    def _out_of_budget(self, state: RunState, emit: Any, label: str) -> bool:
        """
        Has the run used up its wall-clock budget?

        Optional work is dropped rather than allowed to overrun. An agent that
        hangs on a slow third-party site and never returns an answer has failed,
        even if every individual call was correct.
        """
        elapsed = time.time() - state.started
        if elapsed <= self.s.max_run_seconds:
            return False
        emit("thought", f"Skipping {label} — run budget reached",
             f"{elapsed:.0f}s elapsed of a {self.s.max_run_seconds:.0f}s budget. "
             "Optional work is dropped so the agent still returns a decision.",
             phase="read", level="warn")
        return True

    @staticmethod
    def _grounding_targets(state: RunState, limit: int = 3) -> list[str]:
        """
        Pick which citations to scrape in full.

        Prefers article-like sources over forums and video: they carry measured
        findings, and they return in seconds rather than timing out.
        """
        slow = ("reddit.com", "youtube.com", "youtu.be", "news.ycombinator.com",
                "twitter.com", "x.com", "facebook.com", "instagram.com", "tiktok.com")

        def penalty(url: str) -> int:
            return 1 if any(s in url for s in slow) else 0

        urls = [e.url for e in state.evidence if e.kind == "review" and e.url]
        ranked = sorted(urls, key=lambda u: (penalty(u), urls.index(u)))
        return ranked[:limit]

    async def _community_evidence(
        self,
        state: RunState,
        client: AnakinClient,
        catalogs: list[dict[str, Any]],
        query: str,
        emit: Any,
    ) -> None:
        """
        Independent third-party opinion, sourced through Wire instead of /search.

        This is what makes ARGUS fully functional on the keyless tier: Anakin's
        paid /search endpoint requires a key, but Wire's read-only discussion
        sites do not. Without this leg a keyless run has only vendor listings —
        which is exactly why an early version reported 29% confidence: it was
        being honest about having no independent evidence.
        """
        discussion = rank_catalogs(catalogs, state.goal, "discussion", limit=5)
        if not discussion:
            return

        async def detail(c: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            return c, await client.wire_actions_for_catalog(str(c.get("slug") or ""))

        details = await asyncio.gather(*(detail(c) for c in discussion),
                                       return_exceptions=True)

        chosen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in details:
            if isinstance(item, BaseException):
                continue
            c, actions = item
            best = _select_action([a for a in actions if is_zero_touch_runnable(a)])
            if best is not None:
                chosen.append((c, best))
        if not chosen:
            return

        emit("thought", f"Community leg: {len(chosen)} discussion sites",
             f'Querying with the adversarial angle — "{query}". '
             "Third-party opinion instead of vendor copy, still keyless. Sites: "
             + " · ".join(str(c.get("name")) for c, _ in chosen),
             phase="read")

        async def run_one(c: dict[str, Any], a: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], CallResult]:
            return c, a, await client.wire_run(str(a["action_id"]),
                                               build_params(a, query))

        for item in await asyncio.gather(*(run_one(c, a) for c, a in chosen),
                                         return_exceptions=True):
            if isinstance(item, BaseException):
                continue
            c, a, r = item
            name = str(c.get("name") or c.get("slug"))
            if not r.ok:
                emit("tool_result", f"{name} · {a['action_id']}", r.error or "failed",
                     phase="read", level="warn")
                continue
            signals = extract_signals(r, source_hint=str(c.get("slug") or name))
            emit("tool_result", f"{name} · {len(signals)} community items",
                 f"{a['action_id']} · {r.elapsed_ms}ms · {r.credits:g} credits",
                 phase="read",
                 data={"result": r.as_dict(),
                       "preview": [{"title": s["title"], "score": s["score"]}
                                   for s in signals[:5]]},
                 level="success" if signals else "warn")
            for sg in signals:
                ev = Evidence(
                    id=f"ev_{uuid.uuid4().hex[:8]}", kind="review",
                    title=sg["title"], url=sg["url"], content=sg["text"],
                    data={**sg, "channel": "community"},
                    source_tool=f"wire_run:{a['action_id']}",
                    credits=r.credits / max(1, len(signals)), confidence=0.72,
                )
                state.evidence.append(ev)
                emit("evidence", ev.title, (ev.content or "")[:180], phase="read",
                     data=ev.as_dict())

    # ----------------------------------------------------------------- REASON
    async def _phase_reason(self, state: RunState, client: AnakinClient, emit: Any) -> None:
        emit("phase", "REASON", "Scoring candidates, then attacking the winner.",
             phase="reason")

        goal_cur = detect_currency(state.goal)
        converted = 0
        excluded_accessories: list[str] = []
        seen_titles: set[str] = set()
        for ev in state.evidence:
            if ev.kind != "listing":
                continue
            d = ev.data
            key = str(d.get("title", "")).lower()[:80]
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)

            # A retrieved listing is not automatically a candidate. A 65-inch
            # "TV Screen Protector" and a "TV Wall Mount Bracket" both came back
            # for a 65-inch TV goal, and one of them ranked third on merit.
            # Retrieved is not the same as relevant.
            title = str(d.get("title") or "")
            is_accessory, _why = relevance.is_accessory(title)
            if is_accessory:
                excluded_accessories.append(title)
                continue

            price_local = d.get("price")
            cur_local = str(d.get("currency") or money.DEFAULT_CURRENCY)
            if price_local is not None and cur_local != goal_cur:
                price_norm = round(money.convert(price_local, cur_local, goal_cur), 2)
                converted += 1
            else:
                price_norm = price_local
            state.options.append(Option(
                id=ev.id, title=title, url=str(d.get("url") or ev.url),
                price=price_norm, currency=goal_cur,
                price_local=price_local, currency_local=cur_local,
                rating=d.get("rating"), reviews=d.get("reviews"),
                source=str(d.get("source") or ""), specs=dict(d.get("specs") or {}),
                shipping=str(d.get("shipping") or ""),
            ))

        if excluded_accessories:
            preview = " · ".join(t[:58] for t in excluded_accessories[:3])
            more = (f" (+{len(excluded_accessories) - 3} more)"
                    if len(excluded_accessories) > 3 else "")
            emit("thought",
                 f"{len(excluded_accessories)} listing(s) excluded — accessories, not products",
                 f"Retrieved but not relevant: {preview}{more}. An accessory is not the "
                 "thing the user asked to buy, however well it matches the search terms. "
                 "Retrieval quality is not decision quality.",
                 phase="reason", level="warn",
                 data={"excluded": excluded_accessories[:12]})

        if not state.options:
            # Say *why* there is nothing, rather than leaving the user to guess.
            # An offline run that recognises none of its scenarios must explain
            # itself instead of quietly serving an unrelated one.
            hint = (
                "Offline fixtures cover three scenarios only — TV, travel and software. This "
                "goal matches none of them, so nothing was served rather than data from an "
                "unrelated scenario. Re-run without --offline for live results."
                if self.s.offline else
                "No site in the catalog returned a usable, priced listing for this goal. "
                "That is a retrieval failure, not a recommendation — widen the goal or add "
                "an ANAKIN_API_KEY for deeper search."
            )
            emit("thought", "No candidates retrieved", hint, phase="reason", level="warn")

        emit("thought", f"{len(state.options)} candidate options assembled",
             "De-duplicated and normalised into a single comparable set."
             + (f" {converted} listing(s) converted from a foreign currency onto "
                f"{goal_cur} so the budget constraint means something."
                if converted else ""),
             phase="reason")

        emit("tool_call", "score", f"{len(state.options)} options", phase="reason",
             data={"tool": "score"})
        decision: Decision
        if hasattr(self.reasoner, "decide_async"):
            decision = await self.reasoner.decide_async(
                state.goal, state.options, state.evidence)
        else:
            decision = self.reasoner.decide(state.goal, state.options, state.evidence)
        state.decision = decision

        if excluded_accessories:
            decision.excluded_listings = list(excluded_accessories)
            decision.critique_adjustments.insert(
                0,
                f"{len(excluded_accessories)} retrieved listing(s) were dropped as "
                f"accessories rather than ranked as products — e.g. "
                f"“{excluded_accessories[0][:64]}”.",
            )

        emit("tool_result", f"Ranked {len(decision.ranked)} options",
             f"reasoner={decision.reasoner}", phase="reason", level="success",
             data={"criteria": decision.criteria,
                   "ranking": [{"rank": o.rank, "title": o.title, "score": o.total_score,
                                "price": o.price, "currency": o.currency,
                                "flags": o.flags} for o in decision.ranked[:8]]})

        emit("tool_call", "critique", "adversarial re-read", phase="reason",
             data={"tool": "critique"})
        emit("critique", "Self-critique", decision.critique, phase="reason",
             data={"adjustments": decision.critique_adjustments,
                   "confidence": decision.confidence, "risks": decision.risks},
             level="warn" if decision.critique_adjustments else "info")

        emit("decision", decision.headline,
             f"confidence {decision.confidence:.0%} · "
             f"{len(decision.ranked)} candidates ranked",
             phase="reason", level="success", data=decision.as_dict())

    # -------------------------------------------------------------------- ACT
    async def _phase_act(self, state: RunState, client: AnakinClient, emit: Any) -> None:
        emit("phase", "ACT",
             "Proposing the action. Anything irreversible waits for you.",
             phase="act")
        assert state.decision is not None

        kind = "artifact"
        for s in state.plan.steps:
            if s.tool == "act":
                kind = str(s.args.get("kind") or "artifact")

        rec = self.executor.propose(goal=state.goal, kind=kind, decision=state.decision)
        state.action = rec

        emit("action", f"Proposed: {rec.title}", rec.detail, phase="act",
             data={"action": rec.as_dict(), "requires_approval": rec.requires_approval},
             level="warn" if rec.requires_approval else "info")

        if rec.requires_approval:
            state.status = "awaiting_approval"
            state.approval = asyncio.get_running_loop().create_future()
            emit("approval", "Awaiting your approval",
                 "Irreversible action paused. Nothing is purchased. Approve to let ARGUS "
                 "prepare the form — it still stops before submit.",
                 phase="act", level="warn",
                 data={"action_id": rec.id, "kind": rec.kind,
                       "reversible": rec.reversible,
                       "target": rec.payload.get("target_url")})
            try:
                approved = await asyncio.wait_for(state.approval, timeout=600)
            except asyncio.TimeoutError:
                approved = False
                emit("action", "Approval timed out",
                     "Falling back to the safe artifact action.", phase="act", level="warn")
                rec.requires_approval = False
                rec.kind = "artifact"
                rec.title = "Generate decision dossier"
                rec.detail = "Writes the full decision dossier to ./artifacts."
        else:
            approved = True

        emit("thought", "Executing action" if approved else "Action declined",
             f"mode={'approved' if approved else 'rejected'}", phase="act")

        rec = await self.executor.execute(
            rec, approved=approved, decision=state.decision,
            evidence=state.evidence, client=client, emit=emit,
        )
        state.action = rec

        emit("action", f"{rec.title} → {rec.status}",
             f"mode={rec.output.get('mode', 'n/a')}", phase="act",
             level="success" if rec.status == "executed" else "warn",
             data={"action": rec.as_dict()})

        # Always leave an auditable dossier behind, whatever else happened.
        if rec.kind != "artifact":
            dossier = self.executor.propose(goal=state.goal, kind="artifact",
                                            decision=state.decision)
            dossier.requires_approval = False
            dossier = await self.executor.execute(
                dossier, approved=True, decision=state.decision,
                evidence=state.evidence, client=client, emit=emit,
            )
            state.dossier = dossier
