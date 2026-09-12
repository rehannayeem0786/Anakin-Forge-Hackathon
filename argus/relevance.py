"""
ARGUS — listing relevance.

`anakin_client.is_query_capable` decides which *actions* are allowed to answer
"find me X". This module answers the mirror-image question about the *results*:
a site has just returned twenty things — which of them is the thing the user
actually asked for?

It exists because of a real failure. Running the flagship goal live and keyless
("a 65-inch OLED TV under $1,500 for gaming") ARGUS returned:

    #1  Vu 65-inch Glo MiniLED TV          <- not OLED
    #2  Samsung 55-inch OLED               <- not 65-inch
    #3  OHAYO 65-inch TV Screen Protector  <- not a TV
    #5  TV Wall Mount Bracket              <- not a TV

...and confidently recommended the MiniLED. The retrieval was excellent; the
*relevance* was unexamined. The action gate had been fixed; the same class of
bug was still alive one layer down, in the results. This module closes it.

Two independent checks, deliberately conservative:

  ACCESSORIES  A screen protector is not a television. Detected by accessory
               nouns plus a compatibility clause ("Compatible with All 65 Inch
               LED, LCD, OLED & Smart TVs"), so a real product that merely
               mentions a stand is not thrown away.

  REQUIREMENTS Only contradictions are flagged, never silence. If the goal says
               "OLED" and the title says "MiniLED", that is a contradiction. If
               the title says nothing about panel technology, we do not guess —
               absence of evidence is not evidence of absence, and guessing
               would silently drop good candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Requirements",
    "is_accessory",
    "accessory_signal",
    "technologies",
    "sizes_inches",
    "parse_requirements",
    "requirement_flags",
    "requirement_match",
    "content_tokens",
    "alignment",
]


def _norm(text: str) -> str:
    """Lowercase, unify separators, collapse whitespace. Keeps quotes and digits."""
    t = (text or "").lower()
    t = t.replace("–", "-").replace("—", "-").replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()


# --------------------------------------------------------------------------
# ACCESSORIES — "is this the product, or something you attach to the product?"
# --------------------------------------------------------------------------
# Phrases that are decisive *when they sit in the head of the title*. The head
# is where the product names itself; everything after it is qualifier, bundle
# contents or marketing.
_STRONG_ACCESSORY = (
    "screen protector", "wall mount", "wall bracket", "mount bracket",
    "tv cover", "screen cover", "dust cover", "dustproof", "surge protector",
    "replacement part", "spare part", "carrying case", "carry case",
    "protective case", "phone case", "laptop case", "screen guard",
    "remote control", "extension cord", "power cord", "power adapter",
    "wall charger", "hdmi cable", "usb cable", "power cable",
    "cleaning kit", "screen cleaner", "installation kit", "mounting kit",
    "tv stand", "tv bracket", "tv mount",
)

# Nouns that mean "accessory" when they name the product in the head. One on its
# own is not enough — "LG C4 65-inch OLED evo 4K Smart TV with Stand" is a TV.
_ACCESSORY_NOUNS = (
    "protector", "mount", "bracket", "cover", "case", "stand", "holder",
    "cable", "cord", "adapter", "charger", "remote", "filter", "strap",
    "sticker", "decal", "skin", "cleaner", "kit", "clip", "sleeve", "pouch",
    "hinge", "screw", "screws", "base", "legs", "feet", "accessories",
)

_ACCESSORY_NOUN_RE = re.compile(r"\b(" + "|".join(_ACCESSORY_NOUNS) + r")\b")

# Everything from here on is a qualifier, not the product. Splitting the title
# at the first such marker is what separates
#     "OHAYO 65 Inch Acrylic TV Screen Protector | Compatible with ..."
# from
#     "LG 65 inch OLED evo AI C6 4K Smart webOS TV (2026) ... Bundle with 1 YR
#      CPS Enhanced Protection Pack, 2X 6FT HDMI Cable and Deco Gear ..."
# Both contain "cable"/"protector"-class words; only the first is *about* one.
_HEAD_BREAK = re.compile(
    r"[|,(]|\s[-–]\s|\bwith\b|\bbundle\b|\bbundled\b|\bincluding\b|\bincludes\b|"
    r"\bfeaturing\b|\bplus\b|\bfor\b|\bw/\b"
)

# A compatibility clause only means "accessory" when it is compatible with the
# *product class* — "Compatible with All 65 Inch LED, LCD, OLED & Smart TVs".
# Real televisions say "Compatible with Alexa" and that is not an accessory.
_DEVICE_CLASS = (
    r"tv|television|monitor|laptop|notebook|smartphone|phone|tablet|camera|"
    r"console|printer|computer|projector|watch|headphone|earbud|speaker|"
    r"soundbar|router|keyboard|mouse|drone|gpu|motherboard"
)
_ASSISTANTS = (
    "alexa", "google assistant", "siri", "airplay", "chromecast", "bluetooth",
    "ios", "android", "windows", "roku", "smartthings", "homekit", "matter",
    "voice control", "bixby", "cortana",
)
_COMPAT_LEAD_RE = re.compile(
    r"\b(?:compatible with|works with|for use with|designed for)\b"
)
_COMPAT_DEVICE_RE = re.compile(r"\b(?:" + _DEVICE_CLASS + r")s?\b")


def _head_of(text: str, limit: int = 110) -> str:
    """The part of the title that names the product."""
    return _HEAD_BREAK.split(text, maxsplit=1)[0][:limit]


def _compatible_with_product_class(text: str) -> bool:
    """True for "Compatible with 65 inch TVs", false for "Compatible with Alexa"."""
    for m in _COMPAT_LEAD_RE.finditer(text):
        window = text[m.end():m.end() + 70]
        if any(a in window for a in _ASSISTANTS):
            continue
        if _COMPAT_DEVICE_RE.search(window):
            return True
    return False


def accessory_signal(title: str) -> tuple[int, list[str]]:
    """
    How strongly does this title look like an accessory rather than the product?

    Returns (score, reasons). Score >= 2 means "this is not the product".
    """
    t = _norm(title)
    head = _head_of(t)
    score = 0
    reasons: list[str] = []

    for phrase in _STRONG_ACCESSORY:
        if phrase in head:
            score += 2
            reasons.append(phrase)
            break

    m = _ACCESSORY_NOUN_RE.search(head)
    if m:
        score += 1
        reasons.append(f"accessory noun '{m.group(1)}'")

    if _compatible_with_product_class(t):
        score += 2
        reasons.append("compatibility clause naming a product class")

    return score, reasons


def is_accessory(title: str) -> tuple[bool, list[str]]:
    """Is this listing an accessory rather than the thing being shopped for?"""
    score, reasons = accessory_signal(title)
    return score >= 2, reasons


# --------------------------------------------------------------------------
# REQUIREMENTS — what the goal explicitly asked for
# --------------------------------------------------------------------------
# Panel technologies, most specific first. Order matters: "OLED" contains "LED",
# so the longer tokens must be consumed before the bare one is looked for.
_TECH_TOKENS: tuple[tuple[str, str], ...] = (
    ("oled", "oled"),
    ("qled", "qled"),
    ("mini led", "miniled"),
    ("miniled", "miniled"),
    ("micro led", "microled"),
    ("microled", "microled"),
    ("plasma", "plasma"),
    ("lcd", "lcd"),
)

_TECH_LABEL = {
    "oled": "OLED", "qled": "QLED", "miniled": "Mini LED",
    "microled": "Micro LED", "lcd": "LCD", "led": "LED",
}

# Resolution is the other spec people actually name, and getting it wrong is a
# wrong product in the same way the panel type is: an 8K request answered with a
# 4K panel is not a worse candidate, it is not what was asked for.
_RESOLUTIONS = ("8k", "4k", "2k", "1440p", "1080p", "720p")


def resolutions(text: str) -> set[str]:
    """Explicit resolutions named in the text."""
    t = _norm(text)
    return {r for r in _RESOLUTIONS if re.search(rf"\b{re.escape(r)}\b", t)}


def technologies(text: str) -> set[str]:
    """
    Every display technology explicitly named in the text.

    "LED" is only reported when it is not part of OLED/QLED/Mini LED — otherwise
    every OLED listing would also claim to be a plain LED one.
    """
    t = _norm(text)
    found: set[str] = set()
    residual = t
    for token, canon in _TECH_TOKENS:
        if token in residual:
            found.add(canon)
            residual = residual.replace(token, " ")
    if re.search(r"\bled\b", residual):
        found.add("led")
    return found


# "65-inch", "65 inches", "65in", '65"', '65”' and "164 cm" are all the same
# panel quoted four different ways. Titles use every one of them.
_SIZE_IN_RE = re.compile(r"(\d{2,3})\s*[-\s]?\s*(?:inch|inches|in\b|\"|''|”)")
_SIZE_CM_RE = re.compile(r"(\d{2,3})\s*[-\s]?\s*(?:cm|centimet(?:re|er)s?)\b")


def sizes_inches(text: str) -> set[int]:
    """
    Diagonal sizes named in the text, normalised to inches.

    Titles quote both units in the wild — Amazon India says "164 cm (65 inches)"
    for the same panel a US storefront calls 65". Both must compare equal.
    """
    t = _norm(text)
    out: set[int] = set()
    for rx, scale in ((_SIZE_IN_RE, 1.0), (_SIZE_CM_RE, 1.0 / 2.54)):
        for m in rx.finditer(t):
            try:
                value = float(m.group(1)) * scale
            except ValueError:
                continue
            # A 65-inch TV is the subject; 3-inch and 200-inch are noise.
            if 10.0 <= value <= 100.0:
                out.add(int(round(value)))
    return out


@dataclass
class Requirements:
    """What the goal explicitly pinned down. Empty sets mean 'not stated'."""

    technologies: set[str] = field(default_factory=set)
    sizes_inches: set[int] = field(default_factory=set)
    resolutions: set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not self.technologies and not self.sizes_inches and not self.resolutions

    def describe(self) -> str:
        bits: list[str] = []
        if self.technologies:
            bits.append("/".join(sorted(_TECH_LABEL.get(t, t.upper())
                                        for t in self.technologies)))
        if self.resolutions:
            bits.append("/".join(sorted(r.upper() for r in self.resolutions)))
        if self.sizes_inches:
            bits.append("/".join(f"{s}-inch" for s in sorted(self.sizes_inches)))
        return " + ".join(bits) or "nothing pinned down"


def parse_requirements(goal: str) -> Requirements:
    """Extract the hard, checkable asks from a goal sentence."""
    return Requirements(
        technologies=technologies(goal),
        sizes_inches=sizes_inches(goal),
        resolutions=resolutions(goal),
    )


def requirement_flags(title: str, req: Requirements) -> list[str]:
    """
    Where does this listing contradict a stated requirement?

    Silence is never a contradiction. A title that never mentions a panel type
    is *unknown*, not wrong — flagging it would quietly discard good candidates
    and is exactly the kind of confident guessing this project exists to avoid.
    """
    flags: list[str] = []

    if req.technologies:
        title_techs = technologies(title)
        if title_techs and not (title_techs & req.technologies):
            asked = "/".join(sorted(_TECH_LABEL.get(t, t.upper()) for t in req.technologies))
            got = "/".join(sorted(_TECH_LABEL.get(t, t.upper()) for t in title_techs))
            flags.append(f"wrong panel type — listed as {got}, goal asked for {asked}")

    if req.resolutions:
        title_res = resolutions(title)
        if title_res and not (title_res & req.resolutions):
            asked = "/".join(sorted(r.upper() for r in req.resolutions))
            got = "/".join(sorted(r.upper() for r in title_res))
            flags.append(f"wrong resolution — listed as {got}, goal asked for {asked}")

    if req.sizes_inches:
        title_sizes = sizes_inches(title)
        if title_sizes and not (title_sizes & req.sizes_inches):
            asked = "/".join(f"{s}\"" for s in sorted(req.sizes_inches))
            got = "/".join(f"{s}\"" for s in sorted(title_sizes))
            flags.append(f"wrong size — listed as {got}, goal asked for {asked}")

    return flags


# --------------------------------------------------------------------------
# COHERENCE — is any of this actually about the question?
# --------------------------------------------------------------------------
# Words that carry no subject matter. Kept deliberately aggressive: the goal is
# to reduce a sentence to *what it is about*, so that "find me the best X" and a
# listing titled X overlap on X and nothing else.
_STOPWORDS = {
    # articles, pronouns, prepositions, conjunctions
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at", "to",
    "for", "with", "by", "as", "is", "are", "was", "be", "am", "it", "its",
    "this", "that", "these", "those", "my", "me", "i", "we", "you", "your",
    "he", "she", "they", "them", "us", "so", "no", "not", "do", "does", "did",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must",
    # generic task verbs — these describe the *job*, not the subject
    "need", "needs", "want", "wants", "find", "finds", "get", "gets", "give",
    "buy", "buying", "purchase", "purchasing", "order", "ordering", "shop",
    "shopping", "look", "looking", "search", "searching", "show", "help",
    "prepare", "preparing", "compare", "comparing", "plan", "planning",
    "research", "researching", "recommend", "recommendation", "suggest",
    "pick", "choose", "choosing", "select", "please", "thanks", "thank",
    # generic quality words
    "best", "better", "good", "great", "top", "nice", "ideal", "perfect",
    "cheap", "cheapest", "affordable", "budget", "value", "quality", "new",
    "latest", "current", "good", "reliable", "worth", "worthwhile",
    # quantifiers and connectives that survive tokenising
    "under", "below", "less", "than", "over", "above", "more", "most", "max",
    "maximum", "min", "minimum", "up", "about", "around", "roughly", "between",
    "some", "any", "all", "one", "two", "thing", "things", "item", "items",
    "option", "options", "product", "products", "price", "prices", "pricing",
    "cheapest", "usd", "eur", "gbp", "inr", "dollars", "euros", "rupees",
    "need", "would", "like", "want", "tell", "know", "what", "which", "who",
    "when", "where", "why", "how", "there", "here", "also", "just", "very",
    "really", "quite", "actually", "ignore", "previous", "instructions",
    "drop", "table", "tables", "system", "prompt", "assistant",
}


def content_tokens(text: str) -> set[str]:
    """
    The words in `text` that say what it is *about*.

    Two characters minimum, because "TV" is the whole subject of a great many
    perfectly good goals and a length-3 floor would erase it.
    """
    return {
        t for t in re.split(r"[^a-z0-9]+", (text or "").lower())
        if len(t) >= 2 and t not in _STOPWORDS and not t.isdigit()
    }


def requirement_match(title: str, req: Requirements) -> bool:
    """
    Does the title *positively confirm* every requirement the goal stated?

    Distinct from `requirement_flags`, which only reports contradictions. There
    are three states, not two:

      confirmed   the title says "OLED" and the goal asked for OLED
      silent      the title never mentions a panel type — unknown, not wrong
      contradicted the title says "MiniLED" and the goal asked for OLED

    Collapsing "silent" into "confirmed" is how an 8K request gets answered with
    a 4K set whose listing simply never mentioned a resolution. Collapsing it
    into "contradicted" throws away good candidates. Keeping it separate is what
    lets the agent prefer positive evidence and still say "none of these
    actually confirms what you asked for".
    """
    if req.empty:
        return True
    if req.technologies and not (technologies(title) & req.technologies):
        return False
    if req.resolutions and not (resolutions(title) & req.resolutions):
        return False
    if req.sizes_inches and not (sizes_inches(title) & req.sizes_inches):
        return False
    return True


def alignment(subject: str, titles: list[str]) -> tuple[set[str], int]:
    """
    Do any of these results actually relate to the question?

    Returns (subject_tokens, how many titles share at least one of them).

    This is the last line of defence against the worst failure mode there is:
    answering a question nobody asked. An agent that returns a confidently
    ranked shortlist for an empty, garbled, or unrelated goal is worse than one
    that returns nothing, because the user cannot tell the difference. It is
    also how "wireless earbuds" ends up answered with a television.
    """
    tokens = content_tokens(subject)
    if not tokens:
        return tokens, 0
    related = sum(1 for t in titles if tokens & content_tokens(t))
    return tokens, related
