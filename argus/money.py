"""
ARGUS — money.

Two problems this solves, both of which silently corrupt a purchasing agent:

  1. Anakin's catalogs span the world. A search on an Indian storefront returns
     rupees while a US storefront returns dollars. Comparing 899 to 150 is
     meaningless; the agent must normalise before it ranks.

  2. Wire payloads often omit a currency field entirely. Falling back to a
     hardcoded "USD" is how an agent ends up recommending a ₹899 pair of earbuds
     as "under budget" on a $150 brief and then reporting 26% confidence because
     its own arithmetic was nonsense.

So: infer the currency from the catalog's domain when the payload is silent, and
convert everything onto one basis before scoring.

Rates are indicative, static, and clearly labelled as such. A production build
would pull a live FX feed; for a hackathon the honest thing is a fixed table plus
a visible note, not a silent guess.
"""

from __future__ import annotations

import re

# Indicative rates: units of USD per 1 unit of the currency.
RATES_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "INR": 0.0120,
    "EUR": 1.087,
    "GBP": 1.266,
    "JPY": 0.00637,
    "CAD": 0.730,
    "AUD": 0.658,
    "NZD": 0.600,
    "SGD": 0.740,
    "HKD": 0.128,
    "CNY": 0.138,
    "TWD": 0.031,
    "KRW": 0.00073,
    "AED": 0.272,
    "SAR": 0.267,
    "BRL": 0.180,
    "MXN": 0.058,
    "PLN": 0.250,
    "SEK": 0.095,
    "NOK": 0.093,
    "DKK": 0.146,
    "CHF": 1.120,
    "ZAR": 0.055,
    "TRY": 0.031,
    "THB": 0.028,
    "MYR": 0.220,
    "IDR": 0.000062,
    "PHP": 0.0175,
    "VND": 0.000039,
    "NGN": 0.00065,
    "EGP": 0.021,
    "PKR": 0.0036,
    "BDT": 0.0085,
    "LKR": 0.0034,
    "NPR": 0.0075,
    "RUB": 0.011,
    "UAH": 0.024,
    "ILS": 0.27,
    "CZK": 0.043,
    "HUF": 0.0028,
    "RON": 0.22,
    "CLP": 0.0011,
    "COP": 0.00025,
    "ARS": 0.0011,
    "PEN": 0.27,
    "KWD": 3.26,
    "QAR": 0.275,
    "OMR": 2.60,
    "BHD": 2.65,
    "JOD": 1.41,
    "MAD": 0.10,
    "KES": 0.0077,
    "GHS": 0.065,
    "TZS": 0.00038,
    "UGX": 0.00027,
    "XOF": 0.0017,
    "XAF": 0.0017,
    "ETB": 0.017,
    "DZD": 0.0074,
    "TND": 0.32,
    "LBP": 0.000011,
    "IQD": 0.00076,
    "ISK": 0.0072,
    "BGN": 0.556,
    "HRK": 0.144,
    "RSD": 0.0093,
    "MKD": 0.0177,
    "GEL": 0.37,
    "AMD": 0.0026,
    "AZN": 0.588,
    "KZT": 0.0021,
    "UZS": 0.000079,
    "MNT": 0.00029,
    "KHR": 0.00025,
    "LAK": 0.000047,
    "MMK": 0.00048,
    "BND": 0.74,
    "MOP": 0.124,
    "FJD": 0.44,
    "PGK": 0.26,
    "JMD": 0.0064,
    "TTD": 0.147,
    "BBD": 0.50,
    "BSD": 1.0,
    "BZD": 0.50,
    "GTQ": 0.13,
    "CRC": 0.0019,
    "PAB": 1.0,
    "DOP": 0.017,
    "CUP": 0.042,
    "HTG": 0.0076,
    "UYU": 0.025,
    "PYG": 0.00013,
    "BOB": 0.145,
    "VES": 0.027,
    "GYD": 0.0048,
    "SRD": 0.028,
}

# Country-code TLD -> currency. Used when a Wire payload omits `currency`.
# Both single-label suffixes (`.in`, `.uk`) and two-label ones (`.co.uk`,
# `.com.br`) live in one table; currency_for_domain() tries the longest match
# first, so `amazon.com.br` resolves to BRL rather than falling through to USD.
_TLD_CURRENCY: dict[str, str] = {
    "in": "INR", "uk": "GBP", "co.uk": "GBP", "de": "EUR", "fr": "EUR",
    "es": "EUR", "it": "EUR", "nl": "EUR", "be": "EUR", "at": "EUR",
    "ie": "EUR", "pt": "EUR", "fi": "EUR", "gr": "EUR", "sk": "EUR",
    "si": "EUR", "lt": "EUR", "lv": "EUR", "ee": "EUR", "cy": "EUR",
    "mt": "EUR", "lu": "EUR", "jp": "JPY", "ca": "CAD", "au": "AUD",
    "nz": "NZD", "sg": "SGD", "hk": "HKD", "cn": "CNY", "tw": "TWD",
    "kr": "KRW", "ae": "AED", "sa": "SAR", "br": "BRL", "mx": "MXN",
    "pl": "PLN", "se": "SEK", "no": "NOK", "dk": "DKK", "ch": "CHF",
    "za": "ZAR", "tr": "TRY", "th": "THB", "my": "MYR", "id": "IDR",
    "ph": "PHP", "vn": "VND", "ng": "NGN", "eg": "EGP", "pk": "PKR",
    "bd": "BDT", "lk": "LKR", "np": "NPR", "ru": "RUB", "ua": "UAH",
    "il": "ILS", "cz": "CZK", "hu": "HUF", "ro": "RON", "cl": "CLP",
    "co": "COP", "ar": "ARS", "pe": "PEN", "kw": "KWD", "qa": "QAR",
    "om": "OMR", "bh": "BHD", "jo": "JOD", "ma": "MAD", "ke": "KES",
    "gh": "GHS", "tz": "TZS", "ug": "UGX", "is": "ISK", "bg": "BGN",
    "rs": "RSD", "ge": "GEL", "kz": "KZT", "uz": "UZS", "kh": "KHR",
    "bn": "BND", "mo": "MOP", "fj": "FJD", "jm": "JMD", "tt": "TTD",
    "bs": "BSD", "bz": "BZD", "gt": "GTQ", "cr": "CRC", "pa": "PAB",
    "do": "DOP", "uy": "UYU", "py": "PYG", "bo": "BOB", "gy": "GYD",
    # Two-label commercial suffixes. Without these, amazon.com.br and
    # amazon.com.mx both resolved to USD and were treated as the US market.
    "com.br": "BRL", "com.mx": "MXN", "com.au": "AUD", "com.ar": "ARS",
    "com.co": "COP", "com.pe": "PEN", "com.tr": "TRY", "com.sg": "SGD",
    "com.my": "MYR", "com.ph": "PHP", "com.tw": "TWD", "com.hk": "HKD",
    "com.cn": "CNY", "com.sa": "SAR", "com.eg": "EGP", "com.ng": "NGN",
    "com.pk": "PKR", "com.bd": "BDT", "com.ua": "UAH", "com.do": "DOP",
    "com.gt": "GTQ", "com.cr": "CRC", "com.pa": "PAB", "com.uy": "UYU",
    "com.py": "PYG", "com.bo": "BOB", "com.jm": "JMD", "com.tt": "TTD",
    "co.in": "INR", "co.jp": "JPY", "co.kr": "KRW", "co.nz": "NZD",
    "co.za": "ZAR", "co.il": "ILS", "co.th": "THB", "co.id": "IDR",
}

# Currencies written with a symbol, for payloads that only give us a string.
SYMBOL_CURRENCY: list[tuple[str, str]] = [
    ("₹", "INR"), ("rs.", "INR"), ("rs ", "INR"), ("inr", "INR"),
    ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"), ("₩", "KRW"),
    ("a$", "AUD"), ("c$", "CAD"), ("r$", "BRL"), ("zł", "PLN"),
    ("₺", "TRY"), ("₽", "RUB"), ("₪", "ILS"), ("₦", "NGN"),
    ("$", "USD"),
]

DEFAULT_CURRENCY = "USD"


def currency_for_domain(domain: str) -> str:
    """
    Best-effort currency for a catalog's domain. Defaults to USD.

    The domain arrives in several shapes in the live catalog — `amazon.in`,
    `amazon.com.br`, `https://www.noon.com/`. All are reduced to a bare host
    first, then the *longest* matching suffix wins, so `amazon.com.br` is BRL
    and not a silent USD (which is what made a Brazilian storefront look like
    the US market).
    """
    d = (domain or "").lower().strip()
    if not d:
        return DEFAULT_CURRENCY
    d = re.sub(r"^[a-z]+://", "", d)          # strip scheme
    d = d.split("/")[0].split("?")[0].split(":")[0]   # strip path / port
    parts = [p for p in d.split(".") if p]
    if len(parts) < 2:
        return DEFAULT_CURRENCY
    # Need at least one label before the suffix, so "com.br" alone is not a host.
    for n in (2, 1):
        if len(parts) >= n + 1:
            suffix = ".".join(parts[-n:])
            if suffix in _TLD_CURRENCY:
                return _TLD_CURRENCY[suffix]
    return DEFAULT_CURRENCY


def currency_from_text(text: str) -> str | None:
    low = (text or "").lower()
    for symbol, code in SYMBOL_CURRENCY:
        if symbol in low:
            return code
    return None


def to_usd(amount: float, currency: str) -> float:
    rate = RATES_TO_USD.get((currency or DEFAULT_CURRENCY).upper(), 1.0)
    return amount * rate


def convert(amount: float, frm: str, to: str) -> float:
    """Convert between two currencies via the USD basis."""
    if not amount:
        return amount
    frm, to = (frm or DEFAULT_CURRENCY).upper(), (to or DEFAULT_CURRENCY).upper()
    if frm == to:
        return amount
    return to_usd(amount, frm) / RATES_TO_USD.get(to, 1.0)


def format_money(amount: float | None, currency: str) -> str:
    if amount is None:
        return "—"
    cur = (currency or DEFAULT_CURRENCY).upper()
    symbols = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£", "JPY": "¥"}
    sym = symbols.get(cur, "")
    if cur in {"INR", "JPY", "KRW", "IDR", "VND"}:
        return f"{sym}{amount:,.0f} {cur}".strip()
    return f"{sym}{amount:,.2f} {cur}".strip()
