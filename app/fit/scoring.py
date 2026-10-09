"""Shared scoring helpers (rule-based, deterministic, explainable)."""
import re
from typing import Optional

from app.utils.text import normalize

CATEGORY_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def requested_stars(category: Optional[str], style: str) -> tuple[Optional[int], Optional[int]]:
    """FIT hotelCategory → (minCategory, maxCategory) for Hotelbeds' filter."""
    c = normalize(category)
    if c:
        m = re.search(r"([1-5])", c)
        if m:
            s = int(m.group(1))
            return s, s
        for w, s in CATEGORY_WORDS.items():
            if w in c:
                return s, s
        if "lux" in c:
            return 5, 5
        if "boutique" in c:
            return 3, 5
        if "budget" in c or "economy" in c:
            return 2, 3
    return {"BUDGET": (2, 3), "MID_RANGE": (3, 4), "LUXURY": (4, 5), "ULTRA_LUXURY": (5, 5)}.get(style, (None, None))


def hotel_stars(category_code: Optional[str], category_name: Optional[str] = None) -> Optional[int]:
    for text in (category_name, category_code):
        if not text:
            continue
        m = re.search(r"([1-5])", str(text))
        if m:
            return int(m.group(1))
    return None


ROOM_KEYWORDS = {
    "SUITE": ("suite",),
    "DELUXE": ("deluxe", "dlx", "premium", "executive", "club"),
    "SUPERIOR": ("superior", "sup"),
    "FAMILY": ("family", "connecting", "interconnecting"),
    "STANDARD": ("standard", "classic", "std"),
    "EXECUTIVE": ("executive", "club"),
    "STUDIO": ("studio",),
    "APARTMENT": ("apartment", "apt"),
    "VILLA": ("villa",),
}

BED_KEYWORDS = {
    "TWIN": ("twin", "two single", "2 single", "twn"),
    "DOUBLE": ("double", "dbl", "queen"),
    "KING": ("king",),
    "SINGLE": ("single", "sgl"),
}

BOARD_CODES = {
    "RO": "RO", "ROOM ONLY": "RO", "ROOMONLY": "RO", "BB": "BB", "BED AND BREAKFAST": "BB", "BREAKFAST": "BB",
    "HB": "HB", "HALF BOARD": "HB", "FB": "FB", "FULL BOARD": "FB", "AI": "AI", "ALL INCLUSIVE": "AI",
    "SC": "SC", "SELF CATERING": "SC",
}


def board_code(value: Optional[str]) -> Optional[str]:
    v = normalize(value).upper()
    if not v:
        return None
    return BOARD_CODES.get(v) or BOARD_CODES.get(v.replace("_", " "))


def keyword_match(requested: Optional[str], mapping: dict, *texts: Optional[str]) -> Optional[bool]:
    """None = no preference, True/False = matched or not."""
    r = normalize(requested).upper().replace(" ", "_")
    if not r:
        return None
    keys = mapping.get(r) or mapping.get(r.split("_")[0]) or (normalize(requested),)
    hay = " ".join(normalize(t) for t in texts if t)
    return any(k in hay for k in keys)


ACTIVITY_TYPE_KEYWORDS = {
    "CULTURAL": ("museum", "history", "historic", "heritage", "mosque", "palace", "cultural", "culture", "old town",
                 "souk", "bazaar", "temple", "church", "cathedral", "monument", "fort", "art", "gallery", "walking"),
    "ADVENTURE": ("safari", "desert", "dune", "diving", "dive", "zip", "rafting", "quad", "atv", "skydiv",
                  "paraglid", "climb", "jet ski", "parasail", "buggy", "kayak", "adventure", "balloon"),
    "NATURE": ("park", "garden", "island", "mountain", "beach", "nature", "wildlife", "lake", "waterfall",
               "national park", "snorkel", "whale", "dolphin", "boat", "cruise"),
    "CULINARY": ("food", "dinner", "lunch", "cooking", "tasting", "wine", "culinary", "gastronom", "dhow dinner"),
    "SHOPPING": ("shopping", "mall", "market", "outlet", "souk"),
    "RELAXATION": ("spa", "hammam", "massage", "wellness", "beach club"),
    "ENTERTAINMENT": ("show", "theme park", "waterpark", "water park", "aquarium", "observation", "concert",
                      "ticket", "attraction", "zoo"),
    "SIGHTSEEING": ("city tour", "sightseeing", "panoramic", "hop on", "observation deck", "skyline", "landmark"),
    "FAMILY": ("kids", "family", "aquarium", "zoo", "theme park", "waterpark", "water park"),
    "RELIGIOUS": ("mosque", "church", "temple", "ziyarat", "shrine", "pilgrim"),
}

STRENUOUS = ("hike", "hiking", "trek", "climb", "quad", "atv", "camel", "dune bash", "dune bashing", "bike",
             "cycling", "walking tour", "kayak", "rafting", "skydiv", "zip")

DAYPART_KEYWORDS = {
    "MORNING": ("morning", "sunrise", "am"),
    "AFTERNOON": ("afternoon", "pm"),
    "EVENING": ("evening", "night", "sunset", "dinner"),
    "NIGHT": ("night", "evening"),
}


def style_price_score(price: float, prices: list[float], style: str, weight: float = 20.0) -> float:
    """Relative price fit within the candidate pool."""
    if not prices or price <= 0:
        return 0.0
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        return weight * 0.6
    pos = (price - lo) / (hi - lo)  # 0 cheapest … 1 priciest
    if style == "BUDGET":
        return weight * (1 - pos)
    if style in ("LUXURY", "ULTRA_LUXURY"):
        return weight * (0.4 + 0.6 * pos) if pos >= 0.3 else weight * 0.3
    return weight * (1 - abs(pos - 0.35) * 1.2)  # mid-range: slightly below the middle


def money(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0
