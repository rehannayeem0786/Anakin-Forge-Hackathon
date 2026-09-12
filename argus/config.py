"""
ARGUS — configuration.

Everything is environment driven so the same code runs in three modes:

  1. ZERO TOUCH  (no key)      -> read-only Anakin calls, works instantly, no signup.
  2. KEYED       (ANAKIN_API_KEY set) -> adds crawl, agentic search, write actions,
                                          cloud browser sessions, webhooks.
  3. OFFLINE     (ARGUS_OFFLINE=1)    -> deterministic fixtures. Demo never dies on stage.

Nothing here is required for the app to boot. That is deliberate: a judge should be
able to clone, run one command, and see the agent work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is in requirements.txt
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # ---- Anakin ----------------------------------------------------------
    anakin_api_key: str | None = os.getenv("ANAKIN_API_KEY") or None
    anakin_base_url: str = os.getenv("ANAKIN_BASE_URL", "https://api.anakin.io/v1").rstrip("/")
    anakin_mcp_url: str = os.getenv("ANAKIN_MCP_URL", "https://mcp.anakin.io/mcp")
    # Residential egress country for scraping / browser sessions.
    default_country: str = os.getenv("ARGUS_COUNTRY", "us")
    # How many Wire/scrape calls may be in flight at once.
    max_parallel: int = int(_num("ARGUS_MAX_PARALLEL", 4))
    request_timeout: float = _num("ARGUS_REQUEST_TIMEOUT", 90.0)
    # Total wall-clock budget for a run. Optional work (community search, source
    # grounding) is dropped once this is exceeded, so the agent always returns an
    # answer instead of hanging on a slow third-party site.
    max_run_seconds: float = _num("ARGUS_MAX_RUN_SECONDS", 180.0)
    # Per-scrape cap. Forum and video sites can be very slow; we would rather have
    # a partial evidence base than a demo that never finishes.
    scrape_timeout: float = _num("ARGUS_SCRAPE_TIMEOUT", 35.0)

    # ---- Reasoning -------------------------------------------------------
    # Any OpenAI-compatible endpoint works (OpenAI, OpenRouter, Groq, Together,
    # Fireworks, vLLM, Ollama, LM Studio...). If absent, ARGUS falls back to its
    # built-in deterministic planner so the demo still completes end to end.
    llm_api_key: str | None = os.getenv("ARGUS_LLM_API_KEY") or None
    llm_base_url: str = os.getenv("ARGUS_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_model: str = os.getenv("ARGUS_LLM_MODEL", "gpt-4o-mini")

    # ---- Runtime ---------------------------------------------------------
    offline: bool = _flag("ARGUS_OFFLINE", False)
    host: str = os.getenv("ARGUS_HOST", "127.0.0.1")
    port: int = int(_num("ARGUS_PORT", 8787))
    artifacts_dir: Path = Path(os.getenv("ARGUS_ARTIFACTS_DIR", str(ROOT / "artifacts")))

    @property
    def has_key(self) -> bool:
        return bool(self.anakin_api_key)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def mode(self) -> str:
        if self.offline:
            return "offline"
        return "keyed" if self.has_key else "zero-touch"

    def describe(self) -> dict:
        return {
            "mode": self.mode,
            "has_anakin_key": self.has_key,
            "has_llm_key": self.has_llm,
            "llm_model": self.llm_model if self.has_llm else None,
            "country": self.default_country,
            "max_parallel": self.max_parallel,
            "base_url": self.anakin_base_url,
            "mcp_url": self.anakin_mcp_url,
        }


settings = Settings()
