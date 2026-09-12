"""
ARGUS — Anakin client.

A thin, complete, async wrapper over every Anakin surface ARGUS uses:

  Zero Touch (NO API KEY, works out of the box)
    POST /url-scraper/scrape      scrape a URL -> clean markdown, inline
    GET  /wire/resolve            natural-language intent -> action_id
    GET  /wire/catalog            every supported site + action counts
    POST /wire-run                run a read-only Wire action, inline

  Keyed (ANAKIN_API_KEY set — free tier gives 300 credits)
    POST /url-scraper             async submit   + GET /url-scraper/{id}
    POST /url-scraper/batch       up to 10 URLs  + GET /url-scraper/batch/{id}
    POST /wire/task               write actions, account-connected runs
    POST /search                  AI web search with citations (sync)
    POST /agentic-search          multi-stage research pipeline
    POST /map                     discover every URL on a domain
    POST /crawl                   multi-page crawl
    wss  /browser-connect         stealth cloud browser over CDP

Design notes
------------
* The key is OPTIONAL. If absent we simply omit the header — Anakin's Zero Touch
  tier serves read-only work and meters per IP. Same endpoint, same body, same
  response shape. Upgrading is literally one header.
* Every call is wrapped in a `CallResult` so the UI can render a truthful trace
  (endpoint, latency, credits, cache hit, error) rather than a happy-path lie.
* `ARGUS_OFFLINE=1` swaps the transport for deterministic fixtures. A live demo
  should never die because conference wifi did.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import fixtures
from .config import Settings, settings as default_settings

# --------------------------------------------------------------------------
# Endpoint paths (single place to change if the API moves)
# --------------------------------------------------------------------------
P_SCRAPE_INLINE = "/url-scraper/scrape"
P_SCRAPE_ASYNC = "/url-scraper"
P_SCRAPE_BATCH = "/url-scraper/batch"
P_WIRE_CATALOG = "/wire/catalog"
P_WIRE_RESOLVE = "/wire/resolve"
P_WIRE_RUN = "/wire-run"
P_WIRE_TASK = "/wire/task"
P_WIRE_JOB = "/wire/jobs"
P_SEARCH = "/search"
P_AGENTIC = "/agentic-search"
P_MAP = "/map"
P_CRAWL = "/crawl"

# Credit cost model, straight from anakin.io/docs/documentation/pricing
CREDIT_COST = {
    "scrape": 1.0,
    "scrape_json": 3.0,
    "batch": 1.0,          # per URL
    "map": 1.0,
    "crawl": 1.0,          # per page
    "search": 3.0,
    "agentic": 10.0,       # + 1 per URL scraped
    "browser": 1.0,        # per 2-minute interval
    "wire": 1.0,           # varies per action; refined from the catalog
}


@dataclass
class CallResult:
    """A truthful record of one Anakin API interaction."""

    ok: bool
    endpoint: str
    method: str = "POST"
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    status_code: int | None = None
    credits: float = 0.0
    cached: bool = False
    elapsed_ms: int = 0
    source: str = "live"  # "live" | "fixture"
    rate_limited: bool = False

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.data
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k)
            if node is None:
                return default
        return node

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "endpoint": self.endpoint,
            "method": self.method,
            "error": self.error,
            "status_code": self.status_code,
            "credits": round(self.credits, 2),
            "cached": self.cached,
            "elapsed_ms": self.elapsed_ms,
            "source": self.source,
            "rate_limited": self.rate_limited,
        }


class CreditLedger:
    """Tracks estimated spend so the agent can stay inside a budget."""

    def __init__(self, budget: float = 300.0) -> None:
        self.budget = budget
        self.spent = 0.0
        self.calls = 0
        self.free_calls = 0

    def charge(self, credits: float, *, free: bool = False) -> None:
        self.calls += 1
        if free:
            self.free_calls += 1
            return
        self.spent = round(self.spent + credits, 2)

    @property
    def remaining(self) -> float:
        return round(max(0.0, self.budget - self.spent), 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "spent": round(self.spent, 2),
            "remaining": self.remaining,
            "calls": self.calls,
            "free_calls": self.free_calls,
        }


class AnakinError(RuntimeError):
    pass


class AnakinClient:
    """Async Anakin API client. Key optional by design."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or default_settings
        self.ledger = CreditLedger()
        self.trial_credits: int | None = None
        self.signup_url: str = "https://anakin.io/signup"
        self._client: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(max(1, self.s.max_parallel))

    # ------------------------------------------------------------------ infra
    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ARGUS/1.0 (+anakin-forge-hackathon)",
        }
        if self.s.has_key:
            h["X-API-Key"] = self.s.anakin_api_key  # type: ignore[assignment]
        return h

    async def __aenter__(self) -> "AnakinClient":
        self._client = httpx.AsyncClient(
            base_url=self.s.anakin_base_url,
            timeout=httpx.Timeout(self.s.request_timeout, connect=15.0),
            headers=self._headers(),
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        credits: float = 0.0,
        free: bool = False,
        retries: int = 2,
        fixture_key: str | None = None,
        fixture_payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        started = time.perf_counter()

        if self.s.offline:
            await asyncio.sleep(0.12)  # keep the trace legible
            payload_in = (
                fixture_payload if fixture_payload is not None
                else (json_body or params or {})
            )
            payload = fixtures.lookup(fixture_key or path, payload_in)
            self.ledger.charge(0.0, free=True)
            return CallResult(
                ok=True,
                endpoint=path,
                method=method,
                data=payload,
                credits=0.0,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                source="fixture",
            )

        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.s.anakin_base_url,
                timeout=httpx.Timeout(self.s.request_timeout, connect=15.0),
                headers=self._headers(),
                follow_redirects=True,
            )

        last_err: str | None = None
        for attempt in range(retries + 1):
            try:
                async with self._sem:
                    resp = await self._client.request(
                        method, path, params=params, json=json_body
                    )
                elapsed = int((time.perf_counter() - started) * 1000)

                # Zero Touch metering headers
                if "X-Trial-Credits-Remaining" in resp.headers:
                    try:
                        self.trial_credits = int(resp.headers["X-Trial-Credits-Remaining"])
                    except ValueError:
                        pass
                if "X-Anakin-Signup" in resp.headers:
                    self.signup_url = resp.headers["X-Anakin-Signup"]

                if resp.status_code == 429:
                    try:
                        retry_after = float(resp.headers.get("Retry-After", 2 * (attempt + 1)))
                    except (TypeError, ValueError):
                        retry_after = 2.0 * (attempt + 1)
                    # Cap the wait. An upstream that asks for 60s would eat a third
                    # of the run budget, and there are other sites worth trying —
                    # the trace should report the wait we actually took.
                    wait = min(max(retry_after, 0.5), 8.0)
                    last_err = f"rate limited (429), retrying in {wait:.0f}s"
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    last_err = f"server error {resp.status_code}"
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue

                if resp.status_code == 402:
                    return CallResult(
                        ok=False,
                        endpoint=path,
                        method=method,
                        error=(
                            "Zero Touch allowance exhausted. Sign up free at "
                            f"{self.signup_url} for 300 credits, then set ANAKIN_API_KEY."
                        ),
                        status_code=402,
                        credits=0.0,
                        elapsed_ms=elapsed,
                    )

                try:
                    body = resp.json()
                except (json.JSONDecodeError, ValueError):
                    body = {"raw": resp.text[:2000]}

                if resp.status_code >= 400:
                    msg = (
                        body.get("message")
                        or body.get("error")
                        or f"HTTP {resp.status_code}"
                        if isinstance(body, dict)
                        else f"HTTP {resp.status_code}"
                    )
                    # A failed call consumes nothing. Charging for it would make the
                    # ledger lie, and the ledger is what the UI shows the user.
                    return CallResult(
                        ok=False,
                        endpoint=path,
                        method=method,
                        data=body if isinstance(body, dict) else {},
                        error=str(msg),
                        status_code=resp.status_code,
                        credits=0.0,
                        elapsed_ms=elapsed,
                    )

                if not isinstance(body, dict):
                    body = {"data": body}

                # Keyless metering also arrives in the body, not just headers.
                trial = body.get("trial")
                if isinstance(trial, dict):
                    if isinstance(trial.get("remaining_credits"), int):
                        self.trial_credits = trial["remaining_credits"]
                    if trial.get("signup_url"):
                        self.signup_url = str(trial["signup_url"])

                self.ledger.charge(credits, free=free)
                return CallResult(
                    ok=True,
                    endpoint=path,
                    method=method,
                    data=body,
                    status_code=resp.status_code,
                    credits=credits,
                    cached=bool(body.get("cached")),
                    elapsed_ms=elapsed,
                )

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.6 * (attempt + 1))

        return CallResult(
            ok=False,
            endpoint=path,
            method=method,
            error=last_err or "request failed",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    # ------------------------------------------------------------- READ: web
    async def scrape(
        self,
        url: str,
        *,
        use_browser: bool = False,
        generate_json: bool = False,
        country: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        """Scrape one URL inline. Zero-Touch capable (no key needed)."""
        body: dict[str, Any] = {
            "url": url,
            "country": country or self.s.default_country,
            "useBrowser": use_browser,
            "generateJson": generate_json,
        }
        if session_id:
            body["sessionId"] = session_id
        cost = CREDIT_COST["scrape_json"] if generate_json else CREDIT_COST["scrape"]
        res = await self._request(
            "POST",
            P_SCRAPE_INLINE,
            json_body=body,
            credits=cost,
            free=not self.s.has_key,
            fixture_key="scrape",
            timeout=timeout,
        )
        # A scrape that "failed" upstream still returns 200 — surface it honestly.
        if res.ok and res.data.get("status") == "failed":
            res.ok = False
            res.error = res.data.get("error") or "scrape failed"
        return res

    async def scrape_async(self, url: str, **kw: Any) -> CallResult:
        """Submit an async scrape job; returns the job id for polling."""
        body = {
            "url": url,
            "country": kw.get("country", self.s.default_country),
            "useBrowser": bool(kw.get("use_browser")),
            "generateJson": bool(kw.get("generate_json")),
        }
        return await self._request(
            "POST", P_SCRAPE_ASYNC, json_body=body, credits=CREDIT_COST["scrape"],
            fixture_key="scrape_async",
        )

    async def scrape_job(self, job_id: str) -> CallResult:
        return await self._request(
            "GET", f"{P_SCRAPE_ASYNC}/{job_id}", credits=0.0, fixture_key="scrape_job"
        )

    async def batch_scrape(self, urls: list[str], **kw: Any) -> CallResult:
        urls = urls[:10]
        return await self._request(
            "POST",
            P_SCRAPE_BATCH,
            json_body={
                "urls": urls,
                "country": kw.get("country", self.s.default_country),
                "useBrowser": bool(kw.get("use_browser")),
                "generateJson": bool(kw.get("generate_json")),
            },
            credits=CREDIT_COST["batch"] * len(urls),
            fixture_key="batch",
        )

    async def search(self, query: str) -> CallResult:
        """Synchronous AI web search with citations. 3 credits."""
        return await self._request(
            "POST", P_SEARCH, json_body={"query": query},
            credits=CREDIT_COST["search"], fixture_key="search",
        )

    async def agentic_search(self, query: str, **kw: Any) -> CallResult:
        """Multi-stage research pipeline (10 credits + 1/URL). Keyed only."""
        if not self.s.has_key and not self.s.offline:
            return CallResult(
                ok=False, endpoint=P_AGENTIC,
                error="agentic search needs a key (Zero Touch is read-only). "
                      "Set ANAKIN_API_KEY — free tier includes 300 credits.",
            )
        return await self._request(
            "POST", P_AGENTIC, json_body={"query": query, **kw},
            credits=CREDIT_COST["agentic"], fixture_key="agentic",
        )

    async def map_site(self, url: str) -> CallResult:
        return await self._request(
            "POST", P_MAP, json_body={"url": url},
            credits=CREDIT_COST["map"], fixture_key="map",
        )

    async def crawl(self, url: str, max_pages: int = 10) -> CallResult:
        return await self._request(
            "POST", P_CRAWL, json_body={"url": url, "maxPages": max_pages},
            credits=CREDIT_COST["crawl"] * max_pages, fixture_key="crawl",
        )

    # ------------------------------------------------------------ READ: Wire
    async def wire_catalog(self, scope: str | None = None) -> CallResult:
        params = {"scope": scope} if scope else None
        return await self._request(
            "GET", P_WIRE_CATALOG, params=params, credits=0.0, free=True,
            fixture_key="catalog",
        )

    async def wire_catalog_detail(self, slug: str) -> CallResult:
        return await self._request(
            "GET", f"{P_WIRE_CATALOG}/{slug}", credits=0.0, free=True,
            fixture_key="catalog_detail", fixture_payload={"slug": slug},
        )

    async def wire_resolve(
        self,
        q: str,
        *,
        catalog: str | None = None,
        category: str | None = None,
        auth_mode: str | None = None,
    ) -> CallResult:
        """Natural language intent -> concrete Wire action_id. Free, no key."""
        params: dict[str, Any] = {"q": q}
        if catalog:
            params["catalog"] = catalog
        if category:
            params["category"] = category
        if auth_mode:
            params["auth_mode"] = auth_mode
        return await self._request(
            "GET", P_WIRE_RESOLVE, params=params, credits=0.0, free=True,
            fixture_key="resolve",
        )

    async def wire_run(self, action_id: str, params: dict[str, Any] | None = None) -> CallResult:
        """Run a read-only Wire action inline. Zero-Touch capable (no key)."""
        res = await self._request(
            "POST", P_WIRE_RUN,
            json_body={"action_id": action_id, "params": params or {}},
            credits=CREDIT_COST["wire"], free=not self.s.has_key,
            fixture_key="wire_run",
        )
        # The API returns 200 with status="failed" for upstream site errors.
        if res.ok and str(res.data.get("status", "")).lower() == "failed":
            res.ok = False
            res.error = str(res.data.get("error") or "wire action failed upstream")
        return res

    async def wire_actions_for_catalog(self, slug: str) -> list[dict[str, Any]]:
        """
        Every action a catalog exposes, normalised.

        This is the reliable discovery surface: `GET /wire/resolve` ranks weakly
        and (as of writing) ignores its own category/catalog filters, whereas
        catalog detail returns the full, schema-complete action list per site.
        """
        res = await self.wire_catalog_detail(slug)
        if not res.ok:
            return []
        return normalize_catalog_actions(res.data, catalog_slug=slug)

    async def wire_task(
        self,
        action_id: str,
        params: dict[str, Any] | None = None,
        credential_id: str | None = None,
    ) -> CallResult:
        """Keyed async Wire execution — required for WRITE actions."""
        if not self.s.has_key and not self.s.offline:
            return CallResult(
                ok=False, endpoint=P_WIRE_TASK,
                error="write actions require a keyed account (Zero Touch is read-only).",
            )
        body: dict[str, Any] = {"action_id": action_id, "params": params or {}}
        if credential_id:
            body["credential_id"] = credential_id
        return await self._request(
            "POST", P_WIRE_TASK, json_body=body,
            credits=CREDIT_COST["wire"], fixture_key="wire_task",
        )

    async def wire_job(self, job_id: str) -> CallResult:
        return await self._request(
            "GET", f"{P_WIRE_JOB}/{job_id}", credits=0.0, fixture_key="wire_job"
        )

    # ---------------------------------------------------------------- ACT
    def browser_connect_url(
        self,
        *,
        country: str | None = None,
        record: bool = False,
        session: str | None = None,
    ) -> str:
        """CDP WebSocket URL for Anakin's stealth cloud browser (Playwright/Puppeteer)."""
        ws = self.s.anakin_base_url.replace("https://", "wss://").replace("http://", "ws://")
        url = f"{ws}/browser-connect"
        qs: list[str] = []
        if country:
            qs.append(f"country={country}")
        if record:
            qs.append("record=true")
        if session:
            qs.append(f"session={session}")
        return f"{url}?{'&'.join(qs)}" if qs else url

    # ------------------------------------------------------------ utility
    async def ping(self) -> CallResult:
        """Cheap liveness probe: resolve a trivially-safe intent."""
        return await self.wire_resolve("search products", auth_mode="none")


# --------------------------------------------------------------------------
# Normalisation
#
# The live API and the published docs disagree in two places, so every action
# is normalised to one internal shape:
#
#   docs / GET /wire/resolve      ->  { catalog_slug, catalog_name,
#                                       params: [ {name,type,required,default} ] }
#   live / GET /wire/resolve      ->  { catalog: "flipkart",
#                                       params: { required: [...], optional: [...] } }
#   live / GET /wire/catalog/{s}  ->  { action_id, name, description, tags,
#                                       type, mode, auth_mode, parameters: [...] }
#
# Handling both means the same agent code works keyless today and keyed later.
# --------------------------------------------------------------------------
_QUERY_PARAM_NAMES = {
    "query", "q", "search", "search_query", "searchquery", "search_term",
    "search_term_st", "search_text", "searchtext", "search_string", "keyword",
    "keywords", "term", "terms", "text", "k", "searchstring", "searchinput",
}

_QUERY_DESC_HINTS = (
    "keyword", "search term", "search query", "search string", "free-text",
    "free text", "search text", "what to search",
)


def is_query_param(p: dict[str, Any]) -> bool:
    """
    Does this parameter accept arbitrary free text?

    This single predicate is what separates an action that can answer
    "find me the best X" from one that can only fetch a record given an ID.
    Getting it wrong is how an agent confidently recommends a screen protector
    to someone shopping for earbuds.
    """
    name = str(p.get("name") or "").lower()
    if name in _QUERY_PARAM_NAMES:
        return True
    if any(tok in name for tok in ("query", "search", "keyword", "term")):
        return True
    desc = str(p.get("description") or "").lower()
    return any(h in desc for h in _QUERY_DESC_HINTS)


def query_params(action: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (action.get("params") or []) if is_query_param(p)]


def is_query_capable(action: dict[str, Any]) -> bool:
    """Can this action be driven by a natural-language query at all?"""
    return bool(query_params(action))


def is_satisfiable(action: dict[str, Any]) -> bool:
    """Do we have a value for every required parameter?"""
    for p in action.get("params") or []:
        if p.get("required") and not is_query_param(p) and p.get("default") is None:
            return False
    return True


def build_params(
    action: dict[str, Any],
    query: str,
    *,
    budget: float | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """
    Fill an action's parameter schema.

    Query-like parameters take the subject. Price ceilings take the parsed
    budget. Everything else required falls back to its documented default.
    """
    params: dict[str, Any] = {}
    for p in action.get("params") or []:
        name = str(p.get("name") or "")
        low = name.lower()
        if not name:
            continue
        if is_query_param(p):
            params[name] = query
        elif budget is not None and "max" in low and "price" in low:
            params[name] = str(int(budget))
        elif budget is not None and "min" in low and "price" in low:
            continue  # leave the floor open
        elif "currency" in low and currency:
            params[name] = currency
        elif p.get("required"):
            default = p.get("default")
            if default is not None:
                params[name] = default
    if not params:
        params = {"query": query}
    return params


def _flatten_params(raw: Any) -> list[dict[str, Any]]:
    """Accept a list of param descriptors OR the {required, optional} object form."""
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for p in raw:
            if isinstance(p, dict) and p.get("name"):
                out.append({
                    "name": p["name"],
                    "type": p.get("type", "string"),
                    "required": bool(p.get("required", False)),
                    "default": p.get("default"),
                    "description": p.get("description", ""),
                })
    elif isinstance(raw, dict):
        for group, required in (("required", True), ("optional", False)):
            for p in raw.get(group) or []:
                if isinstance(p, dict) and p.get("name"):
                    out.append({
                        "name": p["name"],
                        "type": p.get("type", "string"),
                        "required": required,
                        "default": p.get("default"),
                        "description": p.get("description", ""),
                    })
    return out


def normalize_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise a /wire/resolve response (either shape)."""
    out: list[dict[str, Any]] = []
    for a in payload.get("results") or []:
        if not isinstance(a, dict):
            continue
        slug = a.get("catalog_slug") or a.get("catalog") or ""
        auth_required = bool(a.get("auth_required", False))
        out.append({
            "action_id": a.get("action_id") or "",
            "catalog_slug": slug,
            "catalog_name": a.get("catalog_name") or slug.replace("-", " ").title(),
            "name": a.get("name") or a.get("action_id") or "",
            "description": a.get("description") or "",
            "tags": a.get("tags") or [],
            "type": a.get("type") or "read",
            "mode": a.get("mode") or "sync",
            "auth_mode": a.get("auth_mode") or ("required" if auth_required else "none"),
            "auth_required": auth_required,
            "credits": a.get("credits") or 1,
            "params": _flatten_params(a.get("params")),
        })
    return out


def normalize_catalog_actions(payload: dict[str, Any], catalog_slug: str = "") -> list[dict[str, Any]]:
    """Normalise a /wire/catalog/{slug} response."""
    out: list[dict[str, Any]] = []
    for a in payload.get("actions") or []:
        if not isinstance(a, dict):
            continue
        auth_mode = a.get("auth_mode") or ("required" if a.get("auth_required") else "none")
        out.append({
            "action_id": a.get("action_id") or "",
            "catalog_slug": catalog_slug,
            "catalog_name": catalog_slug.replace("-", " ").title(),
            "name": a.get("name") or "",
            "description": a.get("description") or "",
            "tags": a.get("tags") or [],
            "type": a.get("type") or "read",
            "mode": a.get("mode") or "async",
            "auth_mode": auth_mode,
            "auth_required": bool(a.get("auth_required", auth_mode == "required")),
            "credits": a.get("credits") or 1,
            "params": _flatten_params(a.get("parameters") or a.get("params")),
        })
    return out


def is_zero_touch_runnable(action: dict[str, Any]) -> bool:
    """Can this action run keyless, read-only, synchronously?"""
    if str(action.get("type", "read")).lower() != "read":
        return False
    if action.get("auth_required"):
        return False
    if str(action.get("auth_mode", "none")).lower() == "required":
        return False
    return bool(action.get("action_id"))

