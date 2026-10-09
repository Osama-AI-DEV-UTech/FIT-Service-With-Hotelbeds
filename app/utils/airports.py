"""
Offline airport lookup from the bundled world_airports.csv (7.8k IATA
records) — zero Hotelbeds calls for the most common transfer terminal
type (IATA).
"""
import csv
import os
from functools import lru_cache
from typing import Optional

import structlog

from app.utils.text import normalize

logger = structlog.get_logger("airports")

# Main commercial airport per city where the CSV has several candidates.
_CITY_OVERRIDES = {
    "DUBAI": "DXB", "PARIS": "CDG", "LONDON": "LHR", "NEW YORK": "JFK", "LOS ANGELES": "LAX", "TOKYO": "HND",
    "BANGKOK": "BKK", "SINGAPORE": "SIN", "SYDNEY": "SYD", "ISTANBUL": "IST", "BARCELONA": "BCN",
    "AMSTERDAM": "AMS", "FRANKFURT": "FRA", "ROME": "FCO", "MILAN": "MXP", "MADRID": "MAD", "HONG KONG": "HKG",
    "SEOUL": "ICN", "MUMBAI": "BOM", "DELHI": "DEL", "NEW DELHI": "DEL", "BEIJING": "PEK", "SHANGHAI": "PVG",
    "CAIRO": "CAI", "JOHANNESBURG": "JNB", "NAIROBI": "NBO", "KARACHI": "KHI", "LAHORE": "LHE",
    "ISLAMABAD": "ISB", "TORONTO": "YYZ", "VANCOUVER": "YVR", "MONTREAL": "YUL", "CHICAGO": "ORD",
    "MIAMI": "MIA", "SAN FRANCISCO": "SFO", "HOUSTON": "IAH", "DALLAS": "DFW", "WASHINGTON": "IAD",
    "ATLANTA": "ATL", "ORLANDO": "MCO", "DOHA": "DOH", "ABU DHABI": "AUH", "RIYADH": "RUH", "JEDDAH": "JED",
    "MAKKAH": "JED", "MECCA": "JED", "MADINAH": "MED", "MEDINA": "MED", "MUSCAT": "MCT", "KUWAIT CITY": "KWI",
    "BEIRUT": "BEY", "AMMAN": "AMM", "ATHENS": "ATH", "LISBON": "LIS", "VIENNA": "VIE", "ZURICH": "ZRH",
    "BRUSSELS": "BRU", "MOSCOW": "SVO", "MUNICH": "MUC", "KUALA LUMPUR": "KUL", "JAKARTA": "CGK",
    "MANILA": "MNL", "TAIPEI": "TPE", "COLOMBO": "CMB", "KATHMANDU": "KTM", "DHAKA": "DAC",
    "MEXICO CITY": "MEX", "SAO PAULO": "GRU", "BUENOS AIRES": "EZE", "LIMA": "LIM", "BOGOTA": "BOG",
    "CASABLANCA": "CMN", "MARRAKECH": "RAK", "MARRAKESH": "RAK", "PHUKET": "HKT", "BALI": "DPS",
    "DENPASAR": "DPS", "MALE": "MLE", "MALDIVES": "MLE", "ANTALYA": "AYT", "CAPPADOCIA": "NAV",
    "BAKU": "GYD", "TBILISI": "TBS", "PALMA": "PMI", "PALMA DE MALLORCA": "PMI", "MALLORCA": "PMI",
    "MAJORCA": "PMI", "VENICE": "VCE", "FLORENCE": "FLR", "NAPLES": "NAP", "NICE": "NCE", "PRAGUE": "PRG",
    "BUDAPEST": "BUD", "BERLIN": "BER", "COPENHAGEN": "CPH", "OSLO": "OSL", "STOCKHOLM": "ARN",
    "HELSINKI": "HEL", "DUBLIN": "DUB", "EDINBURGH": "EDI", "MANCHESTER": "MAN", "GENEVA": "GVA",
    "SHARM EL SHEIKH": "SSH", "HURGHADA": "HRG", "LUXOR": "LXR", "OSAKA": "KIX", "KYOTO": "KIX",
    "HANOI": "HAN", "HO CHI MINH CITY": "SGN", "SEYCHELLES": "SEZ", "MAURITIUS": "MRU", "ZANZIBAR": "ZNZ",
    "CAPE TOWN": "CPT", "AUCKLAND": "AKL", "MELBOURNE": "MEL", "LAS VEGAS": "LAS", "CANCUN": "CUN",
}

_BY_IATA: dict[str, dict] = {}
_BY_CITY: dict[str, list[dict]] = {}


def _load() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "world_airports.csv")
    if not os.path.exists(path):  # pragma: no cover
        logger.error("world_airports_csv_missing", path=path)
        return
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            iata = (row.get("IATA_CODE") or "").strip().upper()
            if len(iata) != 3:
                continue
            try:
                rec = {
                    "iata": iata, "name": (row.get("AIRPORT") or "").strip(), "city": (row.get("CITY") or "").strip(),
                    "country": (row.get("COUNTRY") or "").strip().upper(),
                    "latitude": float(row.get("LATITUDE") or 0), "longitude": float(row.get("LONGITUDE") or 0),
                }
            except ValueError:
                continue
            _BY_IATA[iata] = rec
            _BY_CITY.setdefault(normalize(rec["city"]), []).append(rec)


_load()


def by_iata(code: Optional[str]) -> Optional[dict]:
    return _BY_IATA.get((code or "").strip().upper())


def _rank(cands: list[dict], country: Optional[str]) -> Optional[dict]:
    if not cands:
        return None
    if country:
        same = [c for c in cands if c["country"] == country.upper()]
        cands = same or cands
    # "International" airports first, then shortest name.
    return sorted(cands, key=lambda c: ("international" not in c["name"].lower(), len(c["name"])))[0]


@lru_cache(maxsize=4096)
def resolve_airport(text: Optional[str], country: Optional[str] = None) -> Optional[dict]:
    """Accepts an IATA code, an airport name ("Dubai International Airport")
    or a city name ("Dubai"). Returns the airport record or None."""
    if not text:
        return None
    raw = text.strip()
    if len(raw) == 3 and raw.isalpha():
        rec = by_iata(raw)
        if rec:
            return rec
    # "Dubai International Airport (DXB)" style
    for token in raw.replace("(", " ").replace(")", " ").replace(",", " ").split():
        if len(token) == 3 and token.isupper() and by_iata(token):
            return by_iata(token)
    norm = normalize(raw)
    override = _CITY_OVERRIDES.get(norm.upper())
    if override and by_iata(override):
        return by_iata(override)
    # airport name match
    name_hits = [r for r in _BY_IATA.values() if normalize(r["name"]) == norm]
    if name_hits:
        return _rank(name_hits, country)
    city = _BY_CITY.get(norm)
    if city:
        return _rank(city, country)
    # "Dubai Airport" / "Paris CDG"
    stripped = norm.replace(" international", "").replace(" airport", "").replace(" intl", "").strip()
    override = _CITY_OVERRIDES.get(stripped.upper())
    if override and by_iata(override):
        return by_iata(override)
    city = _BY_CITY.get(stripped)
    if city:
        return _rank(city, country)
    contains = [r for r in _BY_IATA.values() if stripped and stripped in normalize(r["name"])]
    if contains and len(contains) <= 5:
        return _rank(contains, country)
    return None


def city_coordinates(city: Optional[str], country: Optional[str] = None) -> Optional[tuple[float, float]]:
    rec = resolve_airport(city, country)
    if rec and rec["latitude"] and rec["longitude"]:
        return rec["latitude"], rec["longitude"]
    return None
