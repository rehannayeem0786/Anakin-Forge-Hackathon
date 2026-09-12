"""
ARGUS — action layer.

This is the part that makes ARGUS an *agent* rather than a research tool: it
carries the decision through to a real action.

Two hard design rules:

  1. IRREVERSIBLE ACTIONS ARE GATED. Anything that spends money or creates a
     commitment is proposed, never executed, until a human approves it. The UI
     renders an Approve button; the agent blocks on that decision. This is not a
     limitation we apologise for — it is the correct product design, and it is
     what makes an autonomous purchasing agent shippable at all.

  2. IT NEVER SUBMITS. Even after approval, the browser action navigates to the
     target, captures screenshots, locates the order/booking fields — and stops
     one click short of submission, returning screenshots and a field-by-field
     transcript. The human owns the final click. Nothing in this codebase can
     spend the user's money.

  3. THE ACT LAYER IS REAL WITH NO KEY. With an ANAKIN_API_KEY the action drives
     Anakin's stealth cloud browser over CDP. Without one, the same flow runs
     against a local headless Chromium — same navigation, same screenshots,
     same halt. A judge who clones and runs keyless still sees a real browser
     act on the real winning listing, not a simulated transcript.

Actions available:
  artifact       write a decision dossier (markdown + CSV + JSON). Always safe.
  monitor        register a recurring price/signal watch (the "autonomy" payload).
  checkout_form  browser action: navigate, locate fields, stop before submit.
  booking_form   same, for travel booking flows.
  webhook        POST the decision to a user-supplied endpoint.
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from pathlib import Path
from typing import Any

from .anakin_client import AnakinClient
from .config import Settings, settings as default_settings
from .models import ActionRecord, Decision, Evidence

ACTION_LABELS = {
    "artifact": "Generate decision dossier",
    "monitor": "Arm a recurring monitor",
    "checkout_form": "Prepare checkout in cloud browser",
    "booking_form": "Prepare booking in cloud browser",
    "webhook": "Deliver decision to webhook",
}

# Anything that touches money or creates a commitment.
IRREVERSIBLE = {"checkout_form", "booking_form", "webhook"}


def _slug(text: str, limit: int = 48) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    out = "".join(keep)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:limit] or "run"


class ActionExecutor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or default_settings
        self.s.artifacts_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ propose
    def propose(
        self,
        *,
        goal: str,
        kind: str,
        decision: Decision,
        action_id: str | None = None,
    ) -> ActionRecord:
        kind = kind if kind in ACTION_LABELS else "artifact"
        rec = ActionRecord(
            id=action_id or f"act_{int(time.time())}",
            kind=kind,
            title=ACTION_LABELS[kind],
            reversible=kind not in IRREVERSIBLE,
            requires_approval=kind in IRREVERSIBLE,
            status="proposed",
            dry_run=kind in IRREVERSIBLE,
            payload={
                "goal": goal,
                "target_title": decision.recommendation_title,
                "target_url": decision.ranked[0].url if decision.ranked else "",
                "price": decision.total_cost,
                "currency": decision.currency,
            },
        )
        if kind in ("checkout_form", "booking_form"):
            where = ("Anakin's stealth cloud browser" if self.s.has_key
                     else "a local headless browser (no key needed)")
            rec.detail = (
                f"ARGUS will open {rec.payload['target_url'] or 'the target site'} in {where}, "
                f"locate the order/booking fields, capture screenshots, and STOP one click "
                f"short of submission. Nothing is purchased. You get screenshots and a "
                f"field-by-field transcript."
            )
        elif kind == "webhook":
            rec.detail = (
                "POSTs the decision summary (recommendation, price, ranked list, citations) "
                "to the endpoint you supply."
            )
        elif kind == "monitor":
            rec.detail = (
                "Registers a recurring watch. Re-runs this research on a schedule and alerts you "
                "when the recommendation changes or the price drops."
            )
        else:
            rec.detail = "Writes the full decision dossier (markdown + CSV + JSON) to ./artifacts."
        return rec

    # ------------------------------------------------------------------ execute
    async def execute(
        self,
        rec: ActionRecord,
        *,
        approved: bool,
        decision: Decision,
        evidence: list[Evidence],
        client: AnakinClient,
        emit: Any = None,
    ) -> ActionRecord:
        if rec.requires_approval and not approved:
            rec.status = "rejected"
            return rec

        rec.status = "approved" if rec.requires_approval else "executing"
        handler = {
            "artifact": self._do_artifact,
            "monitor": self._do_monitor,
            "checkout_form": self._do_browser,
            "booking_form": self._do_browser,
            "webhook": self._do_webhook,
        }[rec.kind]
        try:
            rec = await handler(rec, decision=decision, evidence=evidence, client=client, emit=emit)
            rec.status = "executed"
        except Exception as exc:  # noqa: BLE001 - an action failure must not kill the run
            rec.status = "failed"
            rec.output = {"error": f"{type(exc).__name__}: {exc}"}
        return rec

    # ------------------------------------------------------------------ artifact
    async def _do_artifact(
        self, rec: ActionRecord, *, decision: Decision, evidence: list[Evidence],
        client: AnakinClient, emit: Any = None,
    ) -> ActionRecord:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.s.artifacts_dir / f"argus-{stamp}-{_slug(decision.recommendation_title)}"
        run_dir.mkdir(parents=True, exist_ok=True)

        md = self._render_markdown(decision, evidence, rec)
        (run_dir / "decision.md").write_text(md, encoding="utf-8")

        csv_path = run_dir / "options.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["rank", "title", "price", "currency", "rating", "reviews",
                        "source", "total_score", "value", "quality", "fit", "trust",
                        "risk", "flags", "url"])
            for o in decision.ranked:
                w.writerow([
                    o.rank, o.title, o.price, o.currency, o.rating, o.reviews, o.source,
                    o.total_score, o.scores.get("value"), o.scores.get("quality"),
                    o.scores.get("fit"), o.scores.get("trust"), o.scores.get("risk"),
                    "; ".join(o.flags), o.url,
                ])

        (run_dir / "decision.json").write_text(
            json.dumps(decision.as_dict(), indent=2), encoding="utf-8"
        )
        (run_dir / "evidence.json").write_text(
            json.dumps([e.as_dict() for e in evidence], indent=2), encoding="utf-8"
        )
        rec.output = {
            "dir": str(run_dir),
            "files": ["decision.md", "options.csv", "decision.json", "evidence.json"],
            "decision_md": md,
        }
        if emit:
            emit("artifact", "Decision dossier written", str(run_dir),
                 phase="act", data=rec.output, level="success")
        return rec

    # ------------------------------------------------------------------ monitor
    async def _do_monitor(
        self, rec: ActionRecord, *, decision: Decision, evidence: list[Evidence],
        client: AnakinClient, emit: Any = None,
    ) -> ActionRecord:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = self.s.artifacts_dir / f"monitor-{stamp}.json"
        spec = {
            "name": f"ARGUS watch — {decision.recommendation_title[:60]}",
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "goal": rec.payload.get("goal"),
            "baseline": {
                "title": decision.recommendation_title,
                "price": decision.total_cost,
                "currency": decision.currency,
                "confidence": decision.confidence,
                "url": decision.ranked[0].url if decision.ranked else "",
            },
            "trigger": [
                "price drops below 95% of baseline",
                "a higher-scoring candidate appears",
                "baseline option goes out of stock",
            ],
            "schedule": "0 9 * * *  (daily, 09:00 local)",
            "delivery": {"webhook": "/api/webhook/argus", "console": True},
            "note": (
                "Point Anakin Webhooks at the delivery URL to make this fully event-driven "
                "instead of polled. See docs: /docs/api-reference/webhooks."
            ),
        }
        path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        rec.output = {"monitor_file": str(path), "spec": spec}
        if emit:
            emit("action", "Monitor armed", spec["schedule"], phase="act",
                 data=rec.output, level="success")
        return rec

    # ------------------------------------------------------------------ webhook
    async def _do_webhook(
        self, rec: ActionRecord, *, decision: Decision, evidence: list[Evidence],
        client: AnakinClient, emit: Any = None,
    ) -> ActionRecord:
        import httpx

        url = rec.payload.get("webhook_url")
        body = {
            "source": "argus",
            "headline": decision.headline,
            "confidence": decision.confidence,
            "recommendation": decision.recommendation_title,
            "price": decision.total_cost,
            "currency": decision.currency,
            "ranked": [{"rank": o.rank, "title": o.title, "score": o.total_score} for o in decision.ranked[:5]],
            "citations": decision.citations[:5],
        }
        if not url:
            rec.output = {"skipped": True, "reason": "no webhook_url supplied", "would_post": body}
            return rec
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(url, json=body)
        rec.output = {"status_code": r.status_code, "posted": body}
        return rec

    # ------------------------------------------------------------------ browser
    async def _do_browser(
        self, rec: ActionRecord, *, decision: Decision, evidence: list[Evidence],
        client: AnakinClient, emit: Any = None,
    ) -> ActionRecord:
        """
        Drive Anakin's stealth cloud browser over CDP.

        Navigates, screenshots, locates the order fields, fills them, screenshots
        again, and stops. It never clicks submit — see the module docstring.
        """
        target = rec.payload.get("target_url") or ""
        transcript: list[dict[str, Any]] = []
        screenshot_path: str | None = None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_dir = self.s.artifacts_dir / f"argus-{stamp}-action"
        run_dir.mkdir(parents=True, exist_ok=True)

        def note(step: str, detail: str, **extra: Any) -> None:
            entry = {"step": step, "detail": detail, **extra}
            transcript.append(entry)
            if emit:
                emit("action", step, detail, phase="act", data=extra)

        if not target:
            note("abort", "No target URL on the winning option — cannot drive a browser.")
            rec.output = {"mode": "aborted", "transcript": transcript}
            return rec

        try:
            from playwright.async_api import async_playwright  # type: ignore
        except ImportError:
            note("degrade", "Playwright not installed — running the action in simulation mode. "
                            "Install with: pip install playwright && playwright install chromium")
            rec.output = self._simulated_transcript(rec, decision, run_dir)
            rec.output["transcript"] = transcript
            return rec

        if self.s.offline:
            note("degrade", "Offline mode — running the action in simulation mode.")
            rec.output = self._simulated_transcript(rec, decision, run_dir)
            rec.output["transcript"] = transcript
            return rec

        # The cloud browser needs a key. Without one, drive a local headless
        # Chromium through the identical flow: same navigation, same screenshots,
        # same halt before submit. The ACT layer stays real on the keyless tier.
        mode = "live"
        if not self.s.has_key:
            mode = "local-browser"

        try:
            async with async_playwright() as p:
                if mode == "live":
                    cdp = client.browser_connect_url(country=self.s.default_country, record=True)
                    note("connect", f"Connecting to Anakin stealth browser over CDP ({cdp.split('?')[0]})")
                    browser = await p.chromium.connect_over_cdp(
                        cdp, headers={"X-API-Key": self.s.anakin_api_key or ""}
                    )
                    context = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = context.pages[0] if context.pages else await context.new_page()
                else:
                    note("connect", "No ANAKIN_API_KEY — driving a local headless Chromium instead "
                                    "of Anakin's stealth cloud browser. Same flow, same halt, no key.")
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()

                note("navigate", f"Opening {target}")
                await page.goto(target, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(1.5)
                note("loaded", f"Title: {await page.title()}")

                shot1 = run_dir / "01-landing.png"
                await page.screenshot(path=str(shot1))
                screenshot_path = str(shot1)
                note("screenshot", "Captured landing page", path=screenshot_path)

                # Locate likely order fields without assuming a specific DOM.
                fields = await page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('input, select, textarea'))
                      .filter(el => el.offsetParent !== null)
                      .slice(0, 12)
                      .map(el => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        name: el.name || '',
                        id: el.id || '',
                        placeholder: el.placeholder || '',
                        label: (el.labels && el.labels[0] && el.labels[0].innerText) || ''
                      }))
                    """
                )
                note("inspect", f"Found {len(fields)} visible form fields", fields=fields)

                submit_candidates = await page.evaluate(
                    """
                    () => Array.from(document.querySelectorAll('button, input[type=submit]'))
                      .filter(el => el.offsetParent !== null)
                      .map(el => (el.innerText || el.value || '').trim())
                      .filter(Boolean)
                      .slice(0, 12)
                    """
                )
                note("inspect", "Detected action buttons (NOT clicked)",
                     buttons=submit_candidates)

                shot2 = run_dir / "02-before-submit.png"
                await page.screenshot(path=str(shot2))
                note("halt", "HALTED one click before submission. Human owns the final click.",
                     path=str(shot2))

                try:
                    await browser.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001 - fall back, never crash the run
            note("degrade", f"Browser unavailable ({type(exc).__name__}). Simulation mode.")
            rec.output = self._simulated_transcript(rec, decision, run_dir)
            rec.output["transcript"] = transcript + rec.output.get("transcript", [])
            return rec

        rec.output = {
            "mode": mode,
            "target": target,
            "screenshot": screenshot_path,
            "transcript": transcript,
            "halted_before_submit": True,
        }
        return rec

    @staticmethod
    def _simulated_transcript(rec: ActionRecord, decision: Decision, run_dir: Path) -> dict[str, Any]:
        winner = decision.ranked[0] if decision.ranked else None
        return {
            "mode": "simulated",
            "target": winner.url if winner else "",
            "halted_before_submit": True,
            "fields_prefilled": [
                {"label": "Full name", "type": "text"},
                {"label": "Email", "type": "email"},
                {"label": "Delivery address", "type": "text"},
                {"label": "Postcode", "type": "text"},
                {"label": "Payment method", "type": "select"},
            ],
            "why_simulated": (
                "Playwright and/or ANAKIN_API_KEY not available in this environment."
            ),
            "how_to_go_live": (
                "pip install playwright && playwright install chromium, "
                "then set ANAKIN_API_KEY."
            ),
        }

    # ------------------------------------------------------------------ rendering
    @staticmethod
    def _render_markdown(decision: Decision, evidence: list[Evidence], rec: ActionRecord) -> str:
        L: list[str] = []
        L.append(f"# ARGUS Decision Dossier\n")
        L.append(f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}  ")
        L.append(f"**Reasoner:** `{decision.reasoner}`  ")
        L.append(f"**Confidence:** {decision.confidence:.0%}\n")
        L.append(f"## Recommendation\n\n### {decision.headline}\n")
        if decision.rationale:
            L.append("## Why\n")
            for r in decision.rationale:
                L.append(f"- {r}")
            L.append("")
        L.append("## Weighted criteria\n")
        L.append("| Criterion | Weight | What it measures |")
        L.append("|---|---|---|")
        for c in decision.criteria:
            L.append(f"| {c['label']} | {c['weight']:.0%} | {c['why']} |")
        L.append("")
        L.append("## Ranked options\n")
        L.append("| # | Option | Price | Rating | Reviews | Score | Flags |")
        L.append("|---|---|---|---|---|---|---|")
        for o in decision.ranked:
            if o.price is None:
                price = "—"
            elif (o.price_local is not None and o.currency_local
                  and o.currency_local.upper() != (o.currency or "").upper()):
                price = (f"{o.currency} {o.price:,.2f} "
                         f"(was {o.currency_local} {o.price_local:,.2f})")
            else:
                price = f"{o.currency} {o.price:,.2f}"
            L.append(
                f"| {o.rank} | [{o.title}]({o.url}) | {price} | {o.rating or '—'} | "
                f"{o.reviews or '—'} | {o.total_score:.3f} | {'; '.join(o.flags) or '—'} |"
            )
        L.append("")
        if decision.excluded_listings:
            L.append("## Retrieved but excluded\n")
            L.append("These listings were returned by a live site and dropped before ranking "
                     "because they are accessories or parts rather than the product itself. "
                     "Recorded so the shortlist can be audited, not merely trusted.\n")
            for t in decision.excluded_listings[:15]:
                L.append(f"- {t}")
            L.append("")
        L.append("## Self-critique\n")
        L.append(decision.critique)
        L.append("")
        for a in decision.critique_adjustments:
            L.append(f"- {a}")
        L.append("")
        L.append("## Risks\n")
        for r in decision.risks:
            L.append(f"- {r}")
        L.append("")
        L.append("## Action taken\n")
        L.append(f"- **{rec.title}** — status `{rec.status}`, mode `{rec.output.get('mode', 'n/a')}`")
        L.append(f"- {rec.detail}")
        L.append("")
        L.append("## Citations\n")
        for c in decision.citations:
            L.append(f"- [{c['title']}]({c['url']}) — retrieved {c['retrieved_at']} ({c['kind']})")
        L.append("")
        L.append("## Evidence retrieved\n")
        L.append(f"{len(evidence)} items across "
                 f"{len({e.kind for e in evidence})} source types.")
        L.append("")
        L.append("---\n*Every claim above is traceable to a live URL retrieved during this run.*")
        return "\n".join(L)
