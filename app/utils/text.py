"""Text normalisation + fuzzy matching used by resolvers and scorers."""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Optional

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "at", "to", "for", "with", "on", "by", "from", "tour", "tours",
    "trip", "visit", "excursion", "experience", "ticket", "tickets", "day", "half", "full", "string", "de", "la",
    "le", "el", "city",
}


def normalize(value: Optional[str]) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def is_placeholder(value: Optional[str]) -> bool:
    """Swagger's default example literally sends "string" — treat as empty."""
    return value is None or normalize(value) in ("", "string", "none", "null", "na", "n a")


def clean(value: Optional[str]) -> Optional[str]:
    return None if is_placeholder(value) else str(value).strip()


def tokens(value: Optional[str], *, drop_stopwords: bool = True) -> set[str]:
    words = normalize(value).split()
    if drop_stopwords:
        words = [w for w in words if w not in _STOPWORDS and len(w) > 1]
    return set(words)


def similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def token_overlap(query: Optional[str], text: Optional[str]) -> float:
    """Share of query tokens found (prefix-tolerant) in text. 0..1."""
    q = tokens(query)
    if not q:
        return 0.0
    t = tokens(text, drop_stopwords=False)
    if not t:
        return 0.0
    hits = 0
    for word in q:
        if word in t or any(tw.startswith(word) or word.startswith(tw) for tw in t if len(tw) > 3 and len(word) > 3):
            hits += 1
    return hits / len(q)


def best_name_match(target: str, candidates: Iterable[tuple[str, str]], threshold: float = 0.84
                    ) -> Optional[tuple[str, str, float]]:
    """candidates: (code, name). Returns (code, name, score) — exact > prefix > contains > fuzzy."""
    nt = normalize(target)
    if not nt:
        return None
    best: Optional[tuple[str, str, float]] = None
    for code, name in candidates:
        nn = normalize(name)
        if not nn:
            continue
        if nn == nt:
            score = 1.0
        elif nn.startswith(nt + " ") or nt.startswith(nn + " "):
            score = 0.95
        elif re.search(rf"\b{re.escape(nt)}\b", nn) or re.search(rf"\b{re.escape(nn)}\b", nt):
            score = 0.9
        else:
            score = SequenceMatcher(None, nt, nn).ratio()
        # Prefer shorter names on ties ("Dubai" over "Dubai Desert")
        if best is None or score > best[2] or (score == best[2] and len(nn) < len(normalize(best[1]))):
            best = (code, name, score)
    if best and best[2] >= threshold:
        return best
    return None
