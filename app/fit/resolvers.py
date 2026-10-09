"""
Location resolution for Hotelbeds:

* city  → Hotels destination code (+ zone from the preferred area)
* city  → Activities destination code
* text  → Transfers terminal (IATA via the offline airport DB, PORT /
          STATION via the cached Transfers terminals list)

All lookups run against CACHED content (Hotelbeds requires content to be
stored locally), so a FIT request normally costs zero content calls.
"""
from typing import Optional

import structlog

from app.container import Container
from app.core.exceptions import ProviderNotConfigured
from app.utils import airports
from app.utils.countries import to_iso2
from app.utils.text import best_name_match, clean, normalize

logger = structlog.get_logger("resolvers")

MODE_FLIGHT, MODE_TRAIN, MODE_SEA, MODE_ROAD = "FLIGHT", "TRAIN", "SEA", "ROAD"


def normalize_mode(mode: Optional[str]) -> Optional[str]:
    m = normalize(mode)
    if not m:
        return None
    if any(k in m for k in ("flight", "air", "plane", "fly")):
        return MODE_FLIGHT
    if any(k in m for k in ("train", "rail")):
        return MODE_TRAIN
    if any(k in m for k in ("ferry", "cruise", "ship", "boat", "sea", "port")):
        return MODE_SEA
    if any(k in m for k in ("car", "bus", "road", "drive", "coach", "taxi", "self")):
        return MODE_ROAD
    return None


def _city_part(city: str) -> str:
    """'Dubai, UAE' → 'Dubai'."""
    return city.split(",")[0].strip()


class LocationResolver:
    def __init__(self, c: Container):
        self.c = c
        self.hotel_lang = c.settings.HOTELBEDS_HOTEL_LANGUAGE
        self.activity_lang = c.settings.HOTELBEDS_ACTIVITY_LANGUAGE
        self.transfer_lang = c.settings.HOTELBEDS_TRANSFER_LANGUAGE

    # ── countries ────────────────────────────────────────────────────
    @staticmethod
    def country_candidates(city: Optional[str], countries: list[Optional[str]]) -> list[str]:
        out: list[str] = []
        for country in countries:
            iso = to_iso2(country)
            if iso and iso not in out:
                out.append(iso)
        if city:
            # Airport DB knows which country a city is in — great tie-breaker for multi-country trips.
            rec = airports.resolve_airport(_city_part(city))
            if rec and rec["country"]:
                if rec["country"] in out:
                    out.remove(rec["country"])
                out.insert(0, rec["country"])
        return out

    # ── hotels destinations ──────────────────────────────────────────
    async def hotel_destinations(self, country: str) -> list[dict]:
        key = f"hotels:destinations:{country}:{self.hotel_lang}"

        async def load():
            items: list[dict] = []
            start = 1
            while True:
                data = await self.c.hotels.content_destinations({
                    "countryCodes": country, "fields": "all", "language": self.hotel_lang,
                    "from": start, "to": start + 999, "useSecondaryLanguage": "false"})
                for d in data.get("destinations") or []:
                    name = d.get("name")
                    name = name.get("content") if isinstance(name, dict) else name
                    zones = [{"code": z.get("zoneCode"), "name": z.get("name") or
                              (z.get("description") or {}).get("content")} for z in d.get("zones") or []]
                    items.append({"code": d.get("code"), "name": name, "countryCode": d.get("countryCode"),
                                  "zones": zones})
                total = data.get("total") or 0
                start += 1000
                if start > total or not data.get("destinations"):
                    break
            return items

        return await self.c.cache.get_or_load(key, load, product="hotels")

    async def resolve_hotel_destination(self, city: str, countries: list[Optional[str]],
                                        area: Optional[str] = None) -> Optional[dict]:
        city_name = _city_part(city)
        for cc in self.country_candidates(city_name, countries):
            dests = await self.hotel_destinations(cc)
            match = best_name_match(city_name, [(d["code"], d["name"]) for d in dests if d.get("name")])
            if not match:
                continue
            dest = next(d for d in dests if d["code"] == match[0])
            zone = None
            area_clean = clean(area)
            if area_clean and dest.get("zones"):
                zm = best_name_match(area_clean, [(str(z["code"]), z["name"]) for z in dest["zones"] if z.get("name")],
                                     threshold=0.8)
                if zm:
                    zone = {"code": int(zm[0]) if str(zm[0]).isdigit() else zm[0], "name": zm[1],
                            "matchScore": round(zm[2], 2)}
            logger.info("hotel_destination_resolved", city=city, code=dest["code"], country=cc, zone=zone)
            return {"code": dest["code"], "name": dest["name"], "countryCode": cc, "zone": zone,
                    "matchScore": round(match[2], 2), "source": "hotelbeds_content"}
        logger.warning("hotel_destination_not_resolved", city=city, countries=countries)
        return None

    # ── activities destinations ─────────────────────────────────────
    async def activity_destinations(self, country: str) -> list[dict]:
        key = f"activities:destinations:{country}:{self.activity_lang}"

        async def load():
            data = await self.c.activities.content_destinations(self.activity_lang, country)
            country_obj = data.get("country") or {}
            items = country_obj.get("destinations") or data.get("destinations") or []
            return [{"code": d.get("code"), "name": d.get("name")} for d in items if d.get("code")]

        return await self.c.cache.get_or_load(key, load, product="activities")

    async def resolve_activity_destination(self, city: str, countries: list[Optional[str]]) -> Optional[dict]:
        city_name = _city_part(city)
        for cc in self.country_candidates(city_name, countries):
            try:
                dests = await self.activity_destinations(cc)
            except ProviderNotConfigured:
                raise
            match = best_name_match(city_name, [(d["code"], d["name"]) for d in dests if d.get("name")])
            if match:
                return {"code": match[0], "name": match[1], "countryCode": cc, "matchScore": round(match[2], 2),
                        "source": "hotelbeds_content"}
        return None

    # ── transfer terminals ───────────────────────────────────────────
    async def transfer_terminals(self, country: str) -> list[dict]:
        key = f"transfers:terminals:{country}:{self.transfer_lang}"

        async def load():
            resp = await self.c.transfers.cache("locations/terminals", {
                "fields": "ALL", "language": self.transfer_lang, "countryCodes": country, "limit": 1000})
            items = resp.data if isinstance(resp.data, list) else (resp.data or {}).get("terminals") or []
            out = []
            for t in items:
                content = t.get("content") or {}
                coords = t.get("coordinates") or {}
                out.append({"code": t.get("code"), "type": content.get("type") or t.get("type"),
                            "description": content.get("description") or t.get("name"),
                            "countryCode": t.get("countryCode"), "latitude": coords.get("latitude"),
                            "longitude": coords.get("longitude")})
            return out

        return await self.c.cache.get_or_load(key, load, product="transfers")

    async def resolve_terminal(self, text: Optional[str], *, mode: Optional[str],
                               countries: list[Optional[str]], city: Optional[str] = None) -> Optional[dict]:
        """Returns {"type": IATA|PORT|STATION, "code", "name", "latitude", "longitude", "source"} or None."""
        raw = clean(text)
        norm_mode = normalize_mode(mode) or (MODE_FLIGHT if mode in (None, "") else None)
        if norm_mode == MODE_ROAD:
            return None

        # Explicit "PORT:XYZ" / "STATION:XYZ" / "IATA:XYZ"
        if raw and ":" in raw:
            t, code = raw.split(":", 1)
            t = t.strip().upper()
            if t in ("IATA", "PORT", "STATION") and code.strip():
                return {"type": t, "code": code.strip().upper() if t == "IATA" else code.strip(), "name": raw,
                        "source": "explicit"}

        cc_list = self.country_candidates(city or raw, countries)

        if norm_mode in (None, MODE_FLIGHT):
            for candidate in (raw, city):
                if not candidate:
                    continue
                rec = airports.resolve_airport(candidate, cc_list[0] if cc_list else None)
                if rec:
                    return {"type": "IATA", "code": rec["iata"], "name": rec["name"], "latitude": rec["latitude"],
                            "longitude": rec["longitude"], "country": rec["country"], "source": "airport_db"}
            return None

        want = "STATION" if norm_mode == MODE_TRAIN else "PORT"
        keywords = ("station", "train", "rail", "estacio", "gare", "bahnhof") if want == "STATION" else \
            ("port", "harbour", "harbor", "cruise", "pier", "marina", "terminal", "puerto")
        for cc in cc_list:
            try:
                terminals = await self.transfer_terminals(cc)
            except ProviderNotConfigured:
                return None
            pool = [t for t in terminals if str(t.get("type") or "").upper() not in ("A", "IATA")
                    and any(k in normalize(t.get("description")) for k in keywords)]
            if not pool:
                continue
            for candidate in (raw, city):
                if not candidate:
                    continue
                m = best_name_match(candidate, [(t["code"], t["description"]) for t in pool], threshold=0.55)
                if not m:
                    # "Istanbul" vs "Istanbul, Sirkeci Train Station": token containment
                    ncand = normalize(candidate)
                    hits = [t for t in pool if ncand and ncand in normalize(t["description"])]
                    if hits:
                        t = sorted(hits, key=lambda x: len(x["description"] or ""))[0]
                        m = (t["code"], t["description"], 0.7)
                if m:
                    t = next(x for x in pool if x["code"] == m[0])
                    return {"type": want, "code": t["code"], "name": t["description"], "latitude": t.get("latitude"),
                            "longitude": t.get("longitude"), "country": cc, "source": "hotelbeds_cache"}
        return None
