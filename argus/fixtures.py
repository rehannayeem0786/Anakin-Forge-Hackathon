"""
ARGUS — offline fixtures.

A live demo must never die because the conference wifi did. When ARGUS_OFFLINE=1
every Anakin call is served from here with deterministic, realistically-shaped
payloads that match the real API response schemas field for field.

This is also what makes the project judge-runnable: clone, run, see the agent
work, with no account, no key, and no network.
"""

from __future__ import annotations

import hashlib
from typing import Any

# --------------------------------------------------------------------------
# Catalog — the shape of GET /wire/catalog
# --------------------------------------------------------------------------
CATALOG = [
    ("amazon", "Amazon", "shopping", 14, "Search products, fetch product detail, reviews and offers."),
    ("bestbuy", "Best Buy", "electronics", 9, "Search products, check price and store stock."),
    ("walmart", "Walmart", "shopping", 8, "Search catalog, fetch price and availability."),
    ("ebay", "eBay", "marketplace", 7, "Search listings, fetch item detail and seller feedback."),
    ("flipkart", "Flipkart", "shopping", 7, "Search products and fetch price and offers."),
    ("target", "Target", "shopping", 5, "Search catalog and check store availability."),
    ("newegg", "Newegg", "electronics", 6, "Search components and fetch live pricing."),
    ("ikea", "IKEA", "shopping", 4, "Search products and check availability."),
    ("airbnb", "Airbnb", "travel", 4, "Search listings, fetch reviews and host details."),
    ("booking", "Booking.com", "travel", 6, "Search stays, fetch price, rating and cancellation policy."),
    ("skyscanner", "Skyscanner", "travel", 5, "Search flights and compare fares."),
    ("tripadvisor", "Tripadvisor", "travel", 5, "Search hotels and attractions, fetch reviews."),
    ("g2", "G2", "research", 5, "Search software categories, fetch reviews and ratings."),
    ("capterra", "Capterra", "research", 4, "Search software, fetch pricing and review summaries."),
    ("trustpilot", "Trustpilot", "research", 4, "Fetch company reviews and trust scores."),
    ("reddit", "Reddit", "social", 6, "Search threads and fetch comments."),
    ("hackernews", "Hacker News", "news-media", 3, "Search stories and fetch discussion threads."),
    ("youtube", "YouTube", "news-media", 5, "Search videos and fetch metadata and transcripts."),
    ("github", "GitHub", "developer-tools", 8, "Search repositories, issues and releases."),
    ("linkedin", "LinkedIn", "jobs", 7, "Search companies, people and job posts."),
    ("producthunt", "Product Hunt", "ai-tools", 4, "Search launches and fetch product details."),
    ("zomato", "Zomato", "food-dining", 5, "Search restaurants and fetch menus and ratings."),
]

# --------------------------------------------------------------------------
# Scenario universes. The agent picks one from the goal's keywords.
# --------------------------------------------------------------------------
SCENARIOS: dict[str, dict[str, Any]] = {
    "tv": {
        "match": ["tv", "television", "oled", "screen", "monitor", "display", "4k"],
        "catalogs": ["amazon", "bestbuy", "walmart", "ebay"],
        "actions": [
            ("amazon", "az_search_products", "Search Products", "Search the Amazon catalog by keyword and return matching products with price and rating.", 1),
            ("amazon", "az_product_detail", "Get Product Detail", "Fetch a product page including spec table, price history and offers.", 1),
            ("amazon", "az_product_reviews", "Get Product Reviews", "Fetch paginated reviews with ratings and verified-purchase flags.", 2),
            ("bestbuy", "bb_search_products", "Search Products", "Search Best Buy products with price, rating and availability.", 1),
            ("bestbuy", "bb_store_stock", "Check Store Stock", "Check in-store availability for a product near a postcode.", 1),
            ("walmart", "wm_search", "Search Catalog", "Search Walmart catalog and return price and availability.", 1),
            ("ebay", "eb_search_listings", "Search Listings", "Search eBay listings including condition and seller rating.", 1),
        ],
        "listings": [
            {
                "title": "LG C4 65-inch OLED evo 4K Smart TV",
                "brand": "LG",
                "price": 1496.99,
                "currency": "USD",
                "rating": 4.8,
                "reviews": 3421,
                "url": "https://www.amazon.com/dp/B0CVQ8QZ9L",
                "source": "amazon",
                "specs": {
                    "panel": "OLED evo (WOLED)",
                    "refresh": "144Hz",
                    "hdr": "Dolby Vision, HDR10, HLG",
                    "ports": "4x HDMI 2.1",
                    "gaming": "G-Sync, FreeSync Premium, VRR, 0.1ms",
                    "warranty": "1 year",
                },
                "shipping": "Free, 2 days (Prime)",
            },
            {
                "title": "Samsung S90D 65-inch OLED 4K Smart TV",
                "brand": "Samsung",
                "price": 1397.99,
                "currency": "USD",
                "rating": 4.7,
                "reviews": 2180,
                "url": "https://www.bestbuy.com/site/samsung-65-class-s90d-oled/p/6578432.p",
                "source": "bestbuy",
                "specs": {
                    "panel": "QD-OLED",
                    "refresh": "144Hz",
                    "hdr": "HDR10+, HLG",
                    "ports": "4x HDMI 2.1",
                    "gaming": "FreeSync Premium Pro, VRR, 0.1ms",
                    "warranty": "1 year",
                },
                "shipping": "Free, 3 days",
            },
            {
                "title": "Sony BRAVIA 8 65-inch OLED 4K Google TV",
                "brand": "Sony",
                "price": 1698.00,
                "currency": "USD",
                "rating": 4.9,
                "reviews": 1890,
                "url": "https://www.amazon.com/dp/B0CVQ9K7XN",
                "source": "amazon",
                "specs": {
                    "panel": "QD-OLED",
                    "refresh": "120Hz",
                    "hdr": "Dolby Vision, HDR10, HLG",
                    "ports": "4x HDMI 2.1",
                    "gaming": "VRR, ALLM, 0.1ms",
                    "warranty": "1 year",
                },
                "shipping": "Free, 2 days (Prime)",
            },
            {
                "title": "Panasonic Z85A 65-inch OLED 4K Fire TV",
                "brand": "Panasonic",
                "price": 1199.00,
                "currency": "USD",
                "rating": 4.4,
                "reviews": 612,
                "url": "https://www.walmart.com/ip/panasonic-z85a-65/512398471",
                "source": "walmart",
                "specs": {
                    "panel": "OLED (WOLED)",
                    "refresh": "120Hz",
                    "hdr": "Dolby Vision, HDR10+, HLG",
                    "ports": "2x HDMI 2.1, 2x HDMI 2.0",
                    "gaming": "VRR, ALLM",
                    "warranty": "1 year",
                },
                "shipping": "Free, 5 days",
            },
            {
                "title": "LG G4 65-inch OLED evo Gallery Edition (Open Box)",
                "brand": "LG",
                "price": 1349.00,
                "currency": "USD",
                "rating": 4.6,
                "reviews": 208,
                "url": "https://www.ebay.com/itm/295847112233",
                "source": "ebay",
                "specs": {
                    "panel": "OLED evo (MLA)",
                    "refresh": "144Hz",
                    "hdr": "Dolby Vision, HDR10, HLG",
                    "ports": "4x HDMI 2.1",
                    "gaming": "G-Sync, FreeSync Premium, VRR",
                    "warranty": "90 days seller warranty",
                },
                "shipping": "$49 freight",
            },
        ],
        "reviews": [
            ("RTINGS: LG C4 OLED Review", "https://www.rtings.com/tv/reviews/lg/c4-oled",
             "The LG C4 delivers class-leading motion handling and the best gaming feature set in its price bracket. Peak SDR brightness measured 452 nits, HDR 742 nits on a 10% window. Widest HDMI 2.1 support at this price.", 0.96),
            ("RTINGS: Samsung S90D OLED Review", "https://www.rtings.com/tv/reviews/samsung/s90d-oled",
             "QD-OLED panel gives the S90D noticeably higher colour volume and HDR punch than WOLED rivals. Slightly weaker motion clarity than LG's C4, but better out-of-box colour accuracy.", 0.94),
            ("Tom's Guide: Best OLED TVs 2026", "https://www.tomsguide.com/best-picks/best-oled-tv",
             "Our value pick remains the LG C4. The Samsung S90D is the better pick if HDR punch matters more than gaming features. Sony's BRAVIA 8 is the best processor but costs a premium.", 0.91),
            ("Reddit r/4KTV consensus thread", "https://www.reddit.com/r/4KTV/comments/1c4oled/",
             "Community consensus: C4 for gaming, S90D for movies in a bright room, avoid open-box eBay units for a primary TV due to panel lottery and warranty friction.", 0.72),
            ("Consumer Reports: OLED reliability survey", "https://www.consumerreports.org/electronics/oled-reliability/",
             "Across 42,000 surveyed units, LG and Samsung OLED failure rates within 4 years were 3.1% and 3.8% respectively — statistically similar. Extended warranties rarely pay off.", 0.88),
        ],
        "specs_source": "https://www.rtings.com/tv/reviews/lg/c4-oled",
    },
    "travel": {
        "match": ["trip", "travel", "hotel", "flight", "goa", "vacation", "weekend", "stay", "resort", "booking"],
        "catalogs": ["airbnb", "booking", "skyscanner", "tripadvisor"],
        "actions": [
            ("airbnb", "ab_search_listings", "Search Listings", "Search Airbnb listings by query, dates, and guest count.", 1),
            ("booking", "bk_search_hotels", "Search Hotels", "Search Booking.com stays with price, rating and cancellation policy.", 1),
            ("skyscanner", "sk_search_flights", "Search Flights", "Search and compare flight fares across carriers.", 1),
            ("tripadvisor", "ta_search_hotels", "Search Hotels", "Search Tripadvisor hotels with traveller ratings.", 1),
        ],
        "listings": [
            {"title": "Beachfront 2BHK with pool — Candolim", "price": 7400, "currency": "INR",
             "rating": 4.8, "reviews": 312, "source": "airbnb", "url": "https://www.airbnb.com/rooms/51239847",
             "specs": {"type": "Entire apartment", "guests": 2, "pool": "Shared", "cancellation": "Free until 48h",
                       "distance_to_beach": "120 m", "breakfast": "No"},
             "shipping": "Instant book"},
            {"title": "Heritage Portuguese Villa — Panjim", "price": 6200, "currency": "INR",
             "rating": 4.6, "reviews": 188, "source": "booking", "url": "https://www.booking.com/hotel/in/panjim-villa.html",
             "specs": {"type": "Boutique hotel", "guests": 2, "pool": "Yes", "cancellation": "Free until 24h",
                       "distance_to_beach": "9 km", "breakfast": "Included"},
             "shipping": "Free cancellation"},
            {"title": "Designer Studio near Baga — Anjuna", "price": 4950, "currency": "INR",
             "rating": 4.7, "reviews": 96, "source": "airbnb", "url": "https://www.airbnb.com/rooms/62718493",
             "specs": {"type": "Entire studio", "guests": 2, "pool": "No", "cancellation": "Moderate",
                       "distance_to_beach": "800 m", "breakfast": "No"},
             "shipping": "Instant book"},
            {"title": "Boutique Resort with spa — Cavelossim", "price": 9100, "currency": "INR",
             "rating": 4.5, "reviews": 421, "source": "booking", "url": "https://www.booking.com/hotel/in/cavelossim-resort.html",
             "specs": {"type": "Resort", "guests": 2, "pool": "Yes", "cancellation": "Free until 72h",
                       "distance_to_beach": "200 m", "breakfast": "Included"},
             "shipping": "Free cancellation"},
            {"title": "Goa direct return flights (BLR->GOI)", "price": 5600, "currency": "INR",
             "rating": 4.2, "reviews": 1204, "source": "skyscanner", "url": "https://www.skyscanner.co.in/flights/blr/goi",
             "specs": {"carrier": "IndiGo", "stops": "Non-stop", "duration": "1h 15m", "baggage": "7 kg cabin",
                       "flexibility": "Changeable with fee"},
             "shipping": "Instant"},
        ],
        "reviews": [
            ("Goa in monsoon: what actually works", "https://www.tripadvisor.com/ShowTopic-g297604-goa.html",
             "Candolim and Anjuna stay lively through the shoulder season; Cavelossim is quiet and many shacks shut. Expect 40-60% lower rates and brief heavy showers.", 0.9),
            ("Airbnb vs Booking in Goa — fee comparison", "https://www.reddit.com/r/Goa/comments/1cgoa/",
             "Booking.com listings usually include tax in the displayed rate while Airbnb adds 14-18% at checkout. For 2 guests, a Booking boutique hotel is often cheaper than an equivalent Airbnb once fees land.", 0.83),
            ("Tripadvisor traveller ranking: North Goa stays", "https://www.tripadvisor.com/Hotels-g297604-Goa-Hotels.html",
             "Candolim properties dominate the top 10 for beach access; Panjim wins on food and walkability. Anjuna is best for nightlife but noisier at night.", 0.87),
        ],
        "specs_source": "https://www.tripadvisor.com/Hotels-g297604-Goa-Hotels.html",
    },
    "software": {
        "match": ["software", "tool", "app", "saas", "crm", "notes", "team", "vendor", "platform", "compare"],
        "catalogs": ["g2", "capterra", "reddit", "trustpilot"],
        "actions": [
            ("g2", "g2_search_software", "Search Software", "Search G2 categories and return products with ratings.", 1),
            ("g2", "g2_reviews", "Get Reviews", "Fetch reviews for a product with reviewer firmographics.", 2),
            ("capterra", "cp_search_software", "Search Software", "Search Capterra and return pricing and feature matrices.", 1),
            ("reddit", "rd_search_threads", "Search Threads", "Search Reddit for discussions matching a query.", 1),
        ],
        "listings": [
            {"title": "Notion — Team plan", "price": 10.0, "currency": "USD", "rating": 4.7, "reviews": 4820,
             "source": "g2", "url": "https://www.g2.com/products/notion/reviews",
             "specs": {"per": "user/month", "min_seats": 1, "storage": "Unlimited", "ai": "Add-on $8/user",
                       "sso": "Business tier only", "api": "Yes", "offline": "Limited"},
             "shipping": "Self-serve"},
            {"title": "Coda — Team plan", "price": 10.0, "currency": "USD", "rating": 4.6, "reviews": 1290,
             "source": "g2", "url": "https://www.g2.com/products/coda/reviews",
             "specs": {"per": "user/month", "min_seats": 1, "storage": "Unlimited", "ai": "Included (credits)",
                       "sso": "Enterprise", "api": "Yes", "offline": "Yes"},
             "shipping": "Self-serve"},
            {"title": "Obsidian — Commercial licence", "price": 4.17, "currency": "USD", "rating": 4.9, "reviews": 980,
             "source": "capterra", "url": "https://capterra.com/p/obsidian",
             "specs": {"per": "user/month (annual)", "min_seats": 1, "storage": "Local", "ai": "Via plugins",
                       "sso": "No", "api": "Local only", "offline": "Full"},
             "shipping": "Self-serve"},
            {"title": "Slack Canvas + AI — Business+", "price": 12.5, "currency": "USD", "rating": 4.5, "reviews": 6100,
             "source": "g2", "url": "https://www.g2.com/products/slack/reviews",
             "specs": {"per": "user/month", "min_seats": 1, "storage": "1 TB/user", "ai": "Included",
                       "sso": "Business+", "api": "Yes", "offline": "No"},
             "shipping": "Self-serve"},
            {"title": "Confluence — Standard", "price": 6.4, "currency": "USD", "rating": 4.4, "reviews": 5320,
             "source": "g2", "url": "https://www.g2.com/products/confluence/reviews",
             "specs": {"per": "user/month", "min_seats": 1, "storage": "Unlimited", "ai": "Atlassian Intelligence included",
                       "sso": "Premium", "api": "Yes", "offline": "No"},
             "shipping": "Self-serve"},
        ],
        "reviews": [
            ("G2 Grid: Knowledge Base software", "https://www.g2.com/categories/knowledge-base",
             "Notion and Confluence lead on enterprise readiness; Coda scores highest on doc-as-app flexibility; Obsidian leads on privacy and offline but has no admin console.", 0.93),
            ("Capterra pricing snapshot", "https://www.capterra.com/knowledge-management-software/",
             "Median price across 240 tools is $9.20/user/month. SSO is gated behind an average 1.9x price multiplier — the single biggest cost driver for a 20-person team.", 0.89),
            ("Reddit r/ExperiencedDevs on note tools", "https://www.reddit.com/r/ExperiencedDevs/comments/1cnotes/",
             "Recurring complaint: teams standardise on Notion then pay again for an AI add-on. Coda's bundled AI is cited as better value. Obsidian praised but 'a hard sell to non-technical staff'.", 0.81),
        ],
        "specs_source": "https://www.g2.com/categories/knowledge-base",
    },
}

DEFAULT_SCENARIO = "tv"

# Sites that return opinion and discussion rather than purchasable items.
DISCUSSION_CATALOGS = {
    "reddit", "hackernews", "youtube", "trustpilot", "g2", "capterra",
    "linkedin", "github", "producthunt", "tripadvisor",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _scenario_for(text: str) -> dict[str, Any]:
    low = (text or "").lower()
    best, best_hits = None, 0
    for name, sc in SCENARIOS.items():
        hits = sum(1 for kw in sc["match"] if kw in low)
        if hits > best_hits:
            best, best_hits = sc, hits
    return best or SCENARIOS[DEFAULT_SCENARIO]


def _matched_scenario(text: str) -> dict[str, Any] | None:
    """
    The scenario a query actually matches, or None. Never guesses.

    `_scenario_for` falls back to the TV scenario, which is fine for picking a
    scrape page but catastrophic for serving results: it means an offline run
    answers "wireless earbuds" with a television, and a garbled or empty goal
    with a confidently ranked shortlist of TVs. That is the same class of bug
    the action gate fixed — invisible to the user, and worse than returning
    nothing. So results are served only for a goal we genuinely recognise.
    """
    low = (text or "").lower()
    best, best_hits = None, 0
    for sc in SCENARIOS.values():
        hits = sum(1 for kw in sc["match"] if kw in low)
        if hits > best_hits:
            best, best_hits = sc, hits
    return best


_NO_SCENARIO_NOTE = (
    "Offline fixtures cover three scenarios only (TV, travel, software). This goal matches "
    "none of them, so no data is served — rather than data from an unrelated scenario. "
    "Re-run without --offline for live results."
)


def _stable_int(*parts: Any, mod: int = 1000) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:8], 16) % mod


def _catalog_payload() -> dict[str, Any]:
    return {
        "catalog": [
            {
                "id": f"{i:04d}-fixture",
                "slug": slug,
                "name": name,
                "url": f"https://www.{slug}.com",
                "domain": f"{slug}.com",
                "category": cat,
                "description": desc,
                "auth_required": False,
                "auth_types": [],
                "status": "active",
                "action_count": count,
            }
            for i, (slug, name, cat, count, desc) in enumerate(CATALOG)
        ]
    }


def _resolve_payload(payload: dict[str, Any]) -> dict[str, Any]:
    q = str(payload.get("q", ""))
    sc = _scenario_for(q)
    want_catalog = payload.get("catalog")
    results = []
    for slug, action_id, name, desc, credits in sc["actions"]:
        if want_catalog and want_catalog != slug:
            continue
        results.append(
            {
                "action_id": action_id,
                "catalog_name": next((c[1] for c in CATALOG if c[0] == slug), slug.title()),
                "catalog_slug": slug,
                "name": name,
                "description": desc,
                "mode": "sync",
                "auth_mode": "none",
                "auth_required": False,
                "connected": False,
                "params": [
                    {"name": "query", "type": "string", "required": True},
                    {"name": "limit", "type": "integer", "required": False, "default": 10},
                ],
                "credits": credits,
            }
        )
    # Broaden with generic cross-catalog matches so the agent has real breadth.
    if not results:
        for slug, name, cat, _count, _desc in CATALOG[:8]:
            results.append(
                {
                    "action_id": f"{slug[:2]}_search",
                    "catalog_name": name,
                    "catalog_slug": slug,
                    "name": "Search",
                    "description": f"Search {name}.",
                    "mode": "sync",
                    "auth_mode": "none",
                    "auth_required": False,
                    "connected": False,
                    "params": [{"name": "query", "type": "string", "required": True}],
                    "credits": 1,
                }
            )
    return {"results": results}


def _catalog_detail_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Mirror GET /wire/catalog/{slug}: the full, schema-complete action list for one
    site. This is ARGUS's real discovery surface, so the fixture must match it.
    """
    slug = str(payload.get("slug") or "")
    row = next((c for c in CATALOG if c[0] == slug), None)
    actions: list[dict[str, Any]] = []
    for sc in SCENARIOS.values():
        for (s, action_id, name, desc, credits) in sc["actions"]:
            if s != slug:
                continue
            actions.append({
                "id": f"{action_id}-fixture",
                "action_id": action_id,
                "catalog_id": f"{slug}-fixture",
                "name": name,
                "description": desc,
                "tags": [slug, "read", "search"],
                "type": "read",
                "mode": "sync",
                "auth_mode": "none",
                "auth_required": False,
                "credits": credits,
                "parameters": [
                    {"name": "query", "type": "string", "required": True,
                     "description": "Free-text search query."},
                    {"name": "limit", "type": "integer", "required": False, "default": 10,
                     "description": "Maximum results to return."},
                ],
            })
    if not actions and row:
        actions.append({
            "id": f"{slug}_search-fixture",
            "action_id": f"{slug[:2]}_search",
            "catalog_id": f"{slug}-fixture",
            "name": "Search",
            "description": f"Search {row[1]}.",
            "tags": [slug, "read", "search"],
            "type": "read",
            "mode": "sync",
            "auth_mode": "none",
            "auth_required": False,
            "credits": 1,
            "parameters": [
                {"name": "query", "type": "string", "required": True},
                {"name": "limit", "type": "integer", "required": False, "default": 10},
            ],
        })
    return {
        "catalog": ({"slug": row[0], "name": row[1], "category": row[2],
                     "description": row[4], "action_count": row[3]} if row else {}),
        "actions": actions,
    }


def _slug_for_action(action_id: str) -> str:
    """Which catalog does this action belong to? (fixture-side lookup)"""
    for sc in SCENARIOS.values():
        for row in sc["actions"]:
            if row[1] == action_id:
                return row[0]
    prefix = action_id[:2]
    for c in CATALOG:
        if c[0][:2] == prefix:
            return c[0]
    return ""


def _wire_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Mirror the live POST /v1/wire-run shape: {job_id, status, data}."""
    action_id = str(payload.get("action_id", ""))
    params = payload.get("params") or {}
    query = str(params.get("query", ""))
    # Match on the QUERY only. Folding the action_id in here used to leak whole
    # scenarios: `cp_search_software` contains "software" and `ta_search_hotels`
    # contains "hotel", so a goal about earbuds was served G2 and Goa content.
    # The action_id is already used, separately and correctly, by
    # `_slug_for_action` to decide whether a site is a discussion site.
    sc = _matched_scenario(query)
    slug = _slug_for_action(action_id)

    if sc is None:
        return {
            "job_id": f"job_{_stable_int(action_id, query, mod=10**9)}",
            "status": "completed",
            "data": {"query": query, "count": 0, "items": [], "note": _NO_SCENARIO_NOTE},
            "trial": {"message": "Keyless free tier.", "remaining_credits": 297,
                      "signup_url": "https://anakin.io/signup?source=keyless"},
        }

    # Discussion/review sites return threads and articles, not purchasable items.
    # Getting this wrong would let the community leg masquerade as vendor listings.
    if slug in DISCUSSION_CATALOGS:
        items = [
            {"title": t, "url": u, "text": s,
             "points": int(score * 1200), "num_comments": int(score * 340)}
            for t, u, s, score in sc["reviews"]
        ]
        return {
            "job_id": f"job_{_stable_int(action_id, query, mod=10**9)}",
            "status": "completed",
            "data": {"query": params.get("query"), "count": len(items), "hits": items},
            "trial": {"message": "Keyless free tier.",
                      "remaining_credits": 297,
                      "signup_url": "https://anakin.io/signup?source=keyless"},
        }

    listings = [
        item for item in sc["listings"]
        if action_id.startswith(item["source"][:2])
        or slug in item["source"]
        or slug[:2] == item["source"][:2]
    ]
    if not listings:
        listings = sc["listings"]
    # Price jitter so repeated runs look like live data, not a frozen file.
    jittered = []
    for item in listings:
        j = (_stable_int(action_id, item["title"], mod=7) - 3) / 100.0
        copy = dict(item)
        copy["price"] = round(item["price"] * (1 + j), 2)
        copy["specs"] = dict(item["specs"])
        jittered.append(copy)
    return {
        "job_id": f"job_{_stable_int(action_id, query, mod=10**9)}",
        "status": "completed",
        "data": {
            "query": params.get("query"),
            "count": len(jittered),
            "items": jittered,
        },
        "trial": {
            "message": "Keyless free tier — sign up free for 300 credits and your own key.",
            "remaining_credits": 297,
            "signup_url": "https://anakin.io/signup?source=keyless",
        },
    }


def _scrape_payload(payload: dict[str, Any]) -> dict[str, Any]:
    url = str(payload.get("url", ""))
    sc = _scenario_for(url)
    md = "\n".join(
        [
            f"# {url}",
            "",
            "## Overview",
            f"Source document used by ARGUS to ground its recommendation ({sc['specs_source']}).",
            "",
            "## Key measured facts",
            *[f"- {t}: {s}" for t, u, s, _ in sc["reviews"][:3]],
            "",
            "## Notes",
            "Values captured from the live page at fetch time. ARGUS records the retrieval",
            "timestamp and the exact URL so every claim in the final decision is auditable.",
        ]
    )
    return {
        "id": f"job_{_stable_int(url, mod=10**9)}",
        "status": "completed",
        "url": url,
        "jobType": "url_scraper",
        "country": "us",
        "markdown": md,
        "html": f"<html><body>{md}</body></html>",
        "cleanedHtml": f"<main>{md}</main>",
        "generatedJson": None,
        "cached": False,
        "error": None,
        "durationMs": 900 + _stable_int(url, mod=2500),
    }


def _search_payload(payload: dict[str, Any]) -> dict[str, Any]:
    q = str(payload.get("query", ""))
    sc = _matched_scenario(q)
    if sc is None:
        return {"query": q, "answer": _NO_SCENARIO_NOTE, "results": [], "citations": []}
    results = [
        {"title": t, "url": u, "snippet": s, "score": score}
        for t, u, s, score in sc["reviews"]
    ]
    return {
        "query": q,
        "answer": (
            "Synthesised from the cited sources: value leadership sits with the mid-priced "
            "option, premium pricing buys marginally better processing, and the cheapest "
            "listing carries warranty or condition risk that materially changes total cost."
        ),
        "results": results,
        "citations": [{"url": u, "title": t, "score": s} for t, u, _s, s in sc["reviews"]],
    }


def _agentic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    q = str(payload.get("query", ""))
    sc = _matched_scenario(q)
    if sc is None:
        return {"id": f"as_{_stable_int(q, mod=10**9)}", "status": "completed", "query": q,
                "stages": [], "report": _NO_SCENARIO_NOTE, "citations": []}
    return {
        "id": f"as_{_stable_int(q, mod=10**9)}",
        "status": "completed",
        "query": q,
        "stages": [
            {"stage": "query_refinement", "output": q},
            {"stage": "web_search", "sources_found": len(sc["reviews"])},
            {"stage": "citation_scraping", "urls_scraped": len(sc["reviews"])},
            {"stage": "synthesis", "output": "report"},
        ],
        "report": "\n\n".join(f"## {t}\n{s}\nSource: {u}" for t, u, s, _ in sc["reviews"]),
        "citations": [{"url": u, "title": t} for t, u, _s, _ in sc["reviews"]],
    }


def _map_payload(payload: dict[str, Any]) -> dict[str, Any]:
    base = str(payload.get("url", "https://example.com")).rstrip("/")
    return {
        "url": base,
        "links": [f"{base}/", f"{base}/pricing", f"{base}/reviews", f"{base}/specs", f"{base}/support"],
        "count": 5,
    }


def _crawl_payload(payload: dict[str, Any]) -> dict[str, Any]:
    base = str(payload.get("url", "https://example.com")).rstrip("/")
    pages = [f"{base}/", f"{base}/pricing", f"{base}/reviews"][: max(1, int(payload.get("maxPages", 3)))]
    return {
        "url": base,
        "pages": [{"url": p, "markdown": f"# {p}\n\nFixture page content."} for p in pages],
        "count": len(pages),
    }


def _job_payload(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "id": f"job_{_stable_int(str(payload), mod=10**9)}",
        "status": "completed",
        "jobType": kind,
        "result": {"ok": True, "note": "fixture job completed"},
    }


_DISPATCH = {
    "catalog_detail": _catalog_detail_payload,
    "catalog": lambda p: _catalog_payload(),
    "resolve": _resolve_payload,
    "wire_run": _wire_run_payload,
    "scrape": _scrape_payload,
    "scrape_async": lambda p: _job_payload(p, "url_scraper"),
    "scrape_job": lambda p: _scrape_payload({"url": "https://example.com"}),
    "batch": lambda p: {"id": "batch_fixture", "status": "completed", "results": []},
    "search": _search_payload,
    "agentic": _agentic_payload,
    "map": _map_payload,
    "crawl": _crawl_payload,
    "wire_task": lambda p: _job_payload(p, "wire_task"),
    "wire_job": lambda p: _job_payload(p, "wire_task"),
}


def lookup(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve a fixture payload for a given logical endpoint key."""
    fn = _DISPATCH.get(key)
    if fn is None:
        for k, f in _DISPATCH.items():
            if k in key:
                fn = f
                break
    if fn is None:
        return {"status": "completed", "note": "no fixture", "key": key}
    return fn(payload)
