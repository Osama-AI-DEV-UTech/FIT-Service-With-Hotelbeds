"""Free-text country name/alias → ISO 3166-1 alpha-2."""
import re
from functools import lru_cache
from typing import Optional

import structlog

logger = structlog.get_logger("countries")

_ISO2 = re.compile(r"^[A-Za-z]{2}$")
_ISO3 = re.compile(r"^[A-Za-z]{3}$")

_ALIASES = {
    "UAE": "AE", "U.A.E": "AE", "U.A.E.": "AE", "EMIRATES": "AE", "USA": "US", "U.S.A.": "US", "U.S.": "US",
    "AMERICA": "US", "UNITED STATES": "US", "UK": "GB", "U.K.": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB",
    "SCOTLAND": "GB", "WALES": "GB", "BRITAIN": "GB", "SOUTH KOREA": "KR", "KOREA": "KR", "NORTH KOREA": "KP",
    "RUSSIA": "RU", "VIETNAM": "VN", "LAOS": "LA", "IVORY COAST": "CI", "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ",
    "BURMA": "MM", "HOLLAND": "NL", "TURKEY": "TR", "TURKIYE": "TR", "TÜRKIYE": "TR", "IRAN": "IR",
    "SYRIA": "SY", "TANZANIA": "TZ", "BOLIVIA": "BO", "VENEZUELA": "VE", "MOLDOVA": "MD", "MACEDONIA": "MK",
    "KSA": "SA", "SAUDI": "SA", "SAUDI ARABIA": "SA", "BOSNIA": "BA", "TAIWAN": "TW", "PALESTINE": "PS",
    "MALDIVES": "MV", "BALI": "ID", "HONG KONG": "HK", "MACAU": "MO", "MACAO": "MO",
}


def pycountry_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("pycountry") is not None


@lru_cache(maxsize=2048)
def to_iso2(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    cleaned = country.strip()
    if not cleaned or cleaned.lower() == "string":
        return None
    alias = _ALIASES.get(cleaned.upper())
    if alias:
        return alias
    try:
        import pycountry
    except ImportError:  # pragma: no cover
        logger.warning("pycountry_not_installed")
        return cleaned.upper() if _ISO2.match(cleaned) else None

    if _ISO2.match(cleaned):
        rec = pycountry.countries.get(alpha_2=cleaned.upper())
        return rec.alpha_2 if rec else None
    if _ISO3.match(cleaned):
        rec = pycountry.countries.get(alpha_3=cleaned.upper())
        if rec:
            return rec.alpha_2
    try:
        match = pycountry.countries.search_fuzzy(cleaned)
        if match:
            return match[0].alpha_2
    except LookupError:
        pass
    logger.info("country_not_resolved", country=country)
    return None


def country_name(iso2: Optional[str]) -> Optional[str]:
    if not iso2:
        return None
    try:
        import pycountry
        rec = pycountry.countries.get(alpha_2=iso2.upper())
        return getattr(rec, "common_name", None) or rec.name if rec else None
    except Exception:  # pragma: no cover
        return None
