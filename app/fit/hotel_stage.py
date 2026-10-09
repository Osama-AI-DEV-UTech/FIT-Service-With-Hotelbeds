"""
Stage 1 — HOTELS (runs first; its hotel code feeds the transfer stage).

One availability call per destination (+1 relaxed retry when the star
filter returns nothing), one batched content call for the shortlisted
hotels (cached for a week), then deterministic scoring.
"""
import time
import uuid
from typing import Any, Optional

import structlog

from app.config import settings
from app.container import Container
from app.core.exceptions import HotelbedsError, ProviderNotConfigured
from app.fit.planner import DestinationPlan, OccupancyGroup, TripPlan
from app.fit.pricing import price_line
from app.fit.resolvers import LocationResolver
from app.fit.scoring import (BED_KEYWORDS, ROOM_KEYWORDS, board_code, hotel_stars, keyword_match, money,
                             requested_stars, style_price_score)
from app.hotelbeds.hotels import image_url
from app.utils import airports
from app.utils.text import clean, normalize, similarity

logger = structlog.get_logger("fit.hotels")


def _hotel_share(plan: TripPlan) -> float:
    s = {normalize(x) for x in plan.request.budget.splurgeOn}
    o = {normalize(x) for x in plan.request.budget.optamizeCostOn}
    if any(k in x for x in s for k in ("hotel", "accommodation", "stay", "room")):
        return 0.65
    if any(k in x for x in o for k in ("hotel", "accommodation", "stay", "room")):
        return 0.4
    return 0.55


class HotelStage:
    def __init__(self, c: Container, resolver: LocationResolver, plan: TripPlan):
        self.c = c
        self.resolver = resolver
        self.plan = plan

    async def run(self, dp: DestinationPlan) -> dict:
        started = time.perf_counter()
        result: dict[str, Any] = {"status": "UNAVAILABLE", "selected": None, "alternatives": [], "searchedFor": None,
                                  "destination": None, "candidatesFound": 0}
        if dp.accommodation is None:
            result["status"] = "NOT_REQUESTED"
            return result
        try:
            result = await self._run(dp, result)
        except ProviderNotConfigured as exc:
            result["status"] = "PROVIDER_NOT_CONFIGURED"
            self.plan.add("CRITICAL", "HOTEL", f"{dp.name}: {exc.message}", dp.index, True)
        except HotelbedsError as exc:
            result["status"] = "SUPPLIER_ERROR"
            result["error"] = {"code": exc.upstream_code, "message": exc.message, "status": exc.upstream_status}
            self.plan.add("CRITICAL", "HOTEL", f"{dp.name}: Hotelbeds hotel search failed — {exc.message}",
                          dp.index, True)
        result["durationMs"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    # ─────────────────────────────────────────────────────────────────
    async def _run(self, dp: DestinationPlan, result: dict) -> dict:
        acc = dp.accommodation
        dest = await self.resolver.resolve_hotel_destination(dp.name, dp.countries, area=acc.location)
        scope: dict
        if dest:
            scope = {"destination": {"code": dest["code"]}}
            result["destination"] = dest
            if clean(acc.location) and not dest.get("zone"):
                self.plan.add("INFO", "HOTEL", f"{dp.name}: area '{acc.location}' did not match a Hotelbeds zone — "
                              "location scored by name/coordinates only.", dp.index)
        else:
            coords = airports.city_coordinates(dp.name, dp.countries[0] if dp.countries else None)
            if not coords:
                result["status"] = "LOCATION_NOT_RESOLVED"
                self.plan.add("CRITICAL", "HOTEL", f"{dp.name}: could not resolve to a Hotelbeds destination. Check "
                              "destinationName / tripCountries.", dp.index, True)
                return result
            scope = {"geolocation": {"latitude": coords[0], "longitude": coords[1],
                                     "radius": settings.FIT_GEO_FALLBACK_RADIUS_KM, "unit": "km"}}
            result["destination"] = {"code": None, "name": dp.name, "geolocation": scope["geolocation"],
                                     "source": "airport_coordinates_fallback"}
            self.plan.add("WARNING", "HOTEL", f"{dp.name}: destination code not found — searched "
                          f"{settings.FIT_GEO_FALLBACK_RADIUS_KM} km around the city's airport instead.", dp.index)

        min_cat, max_cat = requested_stars(acc.hotelCategory, self.plan.planning_style)
        board = board_code(acc.boardBasis)
        payload = self._payload(dp, scope, min_cat, max_cat, board)
        result["searchedFor"] = {k: v for k, v in payload.items() if k not in ("language",)}

        data = await self.c.hotels.availability(payload)
        hotels = ((data or {}).get("hotels") or {}).get("hotels") or []
        if not hotels and (min_cat or max_cat):
            # Relax the star filter once rather than return nothing.
            payload.pop("filter", None)
            payload["filter"] = {"maxHotels": settings.FIT_MAX_HOTELS_PER_SEARCH}
            if settings.FIT_ALLOW_PACKAGING_RATES:
                payload["filter"]["packaging"] = True
            data = await self.c.hotels.availability(payload)
            hotels = ((data or {}).get("hotels") or {}).get("hotels") or []
            if hotels:
                self.plan.add("WARNING", "HOTEL", f"{dp.name}: no {acc.hotelCategory or 'requested'} hotels available"
                              " — showing other categories.", dp.index, True)
        result["candidatesFound"] = len(hotels)
        if not hotels:
            result["status"] = "NO_AVAILABILITY"
            self.plan.add("CRITICAL", "HOTEL", f"{dp.name}: Hotelbeds returned no availability for "
                          f"{dp.check_in} → {dp.check_out}.", dp.index, True)
            return result

        options = []
        for h in hotels:
            opt = self._build_option(h, dp)
            if opt:
                options.append(opt)
        if not options:
            result["status"] = "NO_MATCHING_ROOMS"
            self.plan.add("CRITICAL", "HOTEL", f"{dp.name}: hotels found but none could fit the requested occupancy "
                          "/ refundability.", dp.index, True)
            return result

        prices = [o["price"]["selling"] for o in options]
        for o in options:
            self._score(o, dp, dest, prices, min_cat, max_cat)
        options.sort(key=lambda o: o["score"], reverse=True)

        shortlist = options[: max(settings.FIT_HOTEL_CONTENT_ENRICH_TOP_N, settings.FIT_HOTEL_ALTERNATIVES + 1)]
        await self._enrich(shortlist, dp)
        for o in shortlist:
            self._content_score(o, dp)
        shortlist.sort(key=lambda o: o["score"], reverse=True)

        selected = shortlist[0]
        result.update({"status": "SELECTED", "selected": selected,
                       "alternatives": shortlist[1: 1 + settings.FIT_HOTEL_ALTERNATIVES]})
        self._post_checks(selected, dp, min_cat)
        return result

    def _payload(self, dp: DestinationPlan, scope: dict, min_cat, max_cat, board) -> dict:
        flt: dict[str, Any] = {"maxHotels": settings.FIT_MAX_HOTELS_PER_SEARCH}
        if min_cat:
            flt["minCategory"] = min_cat
        if max_cat:
            flt["maxCategory"] = max_cat
        if settings.FIT_ALLOW_PACKAGING_RATES:
            flt["packaging"] = True
        payload = {
            "stay": {"checkIn": dp.check_in.isoformat(), "checkOut": dp.check_out.isoformat()},
            "occupancies": [g.hotelbeds() for g in dp.occupancies],
            **scope,
            "filter": flt,
            "sourceMarket": self.plan.source_market,
            "language": settings.HOTELBEDS_HOTEL_LANGUAGE,
        }
        if board:
            payload["boards"] = {"board": [board], "included": True}
        return payload

    # ── option building ──────────────────────────────────────────────
    def _build_option(self, h: dict, dp: DestinationPlan) -> Optional[dict]:
        acc = dp.accommodation
        refundable_only = bool(acc.refundableOnly)
        room_lines = []
        for group in dp.occupancies:
            best = None
            for room in h.get("rooms") or []:
                for rate in room.get("rates") or []:
                    if not self._rate_fits(rate, group):
                        continue
                    if refundable_only and rate.get("rateClass") == "NRF":
                        continue
                    s = self._rate_score(room, rate, acc)
                    if best is None or s > best[0]:
                        best = (s, room, rate)
            if best is None:
                return None
            _, room, rate = best
            room_lines.append(self._room_line(room, rate, group))

        currency = h.get("currency")
        net = sum(rl["price"]["net"] for rl in room_lines)
        selling = sum(rl["price"]["selling"] for rl in room_lines)
        cxl = [p for rl in room_lines for p in rl["cancellationPolicies"]]
        free_until = min((p["from"] for p in cxl if p.get("from")), default=None)
        return {
            "optionId": uuid.uuid4().hex[:10],
            "hotel": {
                "code": h.get("code"), "name": h.get("name"), "categoryCode": h.get("categoryCode"),
                "categoryName": h.get("categoryName"), "stars": hotel_stars(h.get("categoryCode"), h.get("categoryName")),
                "destinationCode": h.get("destinationCode"), "destinationName": h.get("destinationName"),
                "zoneCode": h.get("zoneCode"), "zoneName": h.get("zoneName"),
                "latitude": _f(h.get("latitude")), "longitude": _f(h.get("longitude")),
            },
            "checkIn": dp.check_in.isoformat(), "checkOut": dp.check_out.isoformat(), "nights": dp.nights,
            "rooms": room_lines,
            "price": {"net": money(net), "selling": money(selling), "currency": (currency or "").upper() or None,
                      "perNight": money(selling / dp.nights) if dp.nights else None,
                      "mandatorySellingPrice": any(rl["hotelMandatory"] for rl in room_lines)},
            "refundable": all(rl["rateClass"] != "NRF" for rl in room_lines),
            "freeCancellationUntil": free_until,
            "requiresCheckRate": any(rl["rateType"] == "RECHECK" for rl in room_lines),
            "score": 0.0, "scoreBreakdown": {},
        }

    @staticmethod
    def _rate_fits(rate: dict, g: OccupancyGroup) -> bool:
        try:
            if int(rate.get("rooms", 1)) != g.rooms or int(rate.get("adults", 0)) != g.adults:
                return False
            if int(rate.get("children", 0)) != g.children:
                return False
        except (TypeError, ValueError):
            return False
        if g.children and rate.get("childrenAges"):
            ages = sorted(int(a) for a in str(rate["childrenAges"]).split(",") if a.strip().isdigit())
            if ages and ages != sorted(g.child_ages):
                return False
        return True

    def _rate_score(self, room: dict, rate: dict, acc) -> float:
        s = 0.0
        names = (room.get("name"), room.get("code"))
        rt = keyword_match(acc.roomType, ROOM_KEYWORDS, *names)
        if rt is True:
            s += 30
        elif rt is False and normalize(acc.roomType) in ("standard", ""):
            s += 5
        bed = keyword_match(acc.bedPref, BED_KEYWORDS, *names)
        if bed is True:
            s += 10
        if rate.get("rateClass") != "NRF":
            s += 6
        if rate.get("rateType") == "BOOKABLE":
            s += 2
        # Cheaper is better among otherwise-equal rates of the same hotel.
        s -= money(rate.get("net")) / 10000.0
        return s

    @staticmethod
    def _room_line(room: dict, rate: dict, g: OccupancyGroup) -> dict:
        mandatory = bool(rate.get("hotelMandatory"))
        selling = money(rate.get("sellingRate")) if mandatory and rate.get("sellingRate") else None
        price = price_line("HOTEL", money(rate.get("net")), None, mandatory_selling=selling)
        price.pop("currency", None)
        return {
            "roomCode": room.get("code"), "roomName": room.get("name"),
            "rateKey": rate.get("rateKey"), "rateType": rate.get("rateType"), "rateClass": rate.get("rateClass"),
            "boardCode": rate.get("boardCode"), "boardName": rate.get("boardName"),
            "rooms": g.rooms, "adults": g.adults, "children": g.children, "childAges": g.child_ages,
            "paymentType": rate.get("paymentType"), "packaging": rate.get("packaging"),
            "hotelMandatory": mandatory, "allotment": rate.get("allotment"),
            "rateCommentsId": rate.get("rateCommentsId"),
            "cancellationPolicies": [{"amount": money(p.get("amount")), "from": p.get("from")}
                                     for p in rate.get("cancellationPolicies") or []],
            "taxes": rate.get("taxes"), "offers": rate.get("offers"), "promotions": rate.get("promotions"),
            "price": price,
        }

    # ── scoring ──────────────────────────────────────────────────────
    def _score(self, o: dict, dp: DestinationPlan, dest: Optional[dict], prices: list[float], min_cat, max_cat):
        acc = dp.accommodation
        b: dict[str, float] = {}
        stars = o["hotel"]["stars"]
        if min_cat and stars:
            if min_cat <= stars <= (max_cat or min_cat):
                b["category"] = 25
            elif abs(stars - min_cat) == 1:
                b["category"] = 10
        elif stars:
            b["category"] = min(stars, 5) * 2

        b["price"] = round(style_price_score(o["price"]["selling"], prices, self.plan.planning_style), 2)
        target = self._budget_target(dp, o["price"]["currency"])
        if target:
            if o["price"]["selling"] > target * 1.1:
                b["overBudget"] = -15
            elif o["price"]["selling"] <= target:
                b["withinBudget"] = 5

        first = o["rooms"][0]
        rt = keyword_match(acc.roomType, ROOM_KEYWORDS, first["roomName"], first["roomCode"])
        if rt is True:
            b["roomType"] = 12
        bed = keyword_match(acc.bedPref, BED_KEYWORDS, first["roomName"], first["roomCode"])
        if bed is True:
            b["bed"] = 4

        if dest and dest.get("zone") and o["hotel"]["zoneCode"] == dest["zone"]["code"]:
            b["location"] = 15
        elif clean(acc.location):
            sim = max(similarity(acc.location, o["hotel"]["zoneName"]), similarity(acc.location, o["hotel"]["name"]))
            if sim >= 0.75 or normalize(acc.location) in normalize(o["hotel"]["name"]):
                b["location"] = 10

        brand = clean(acc.preferedBrand)
        if brand and normalize(brand) in normalize(o["hotel"]["name"]):
            b["brand"] = 15

        if o["refundable"]:
            b["refundable"] = 8 if normalize(self.plan.request.budget.decisionTimeline) != "immediate" else 3
        o["scoreBreakdown"] = b
        o["score"] = round(sum(b.values()), 2)

    def _content_score(self, o: dict, dp: DestinationPlan) -> None:
        content = o.get("content") or {}
        b = o["scoreBreakdown"]
        brand = clean(dp.accommodation.preferedBrand)
        if brand and "brand" not in b:
            chain = normalize(content.get("chainName")) + " " + normalize(content.get("chainCode"))
            if normalize(brand) in chain:
                b["brand"] = 15
        loc = clean(dp.accommodation.location)
        if loc and "location" not in b and normalize(loc) in normalize(content.get("address")):
            b["location"] = 8
        if content.get("images"):
            b["content"] = 2
        o["score"] = round(sum(b.values()), 2)

    def _budget_target(self, dp: DestinationPlan, currency: Optional[str]) -> Optional[float]:
        total = self.plan.budget_total
        if not total or not currency or currency.upper() != self.plan.currency:
            return None
        return total * _hotel_share(self.plan) * (dp.nights / max(self.plan.total_nights, 1))

    # ── content enrichment (cached) ──────────────────────────────────
    async def _enrich(self, options: list[dict], dp: DestinationPlan) -> None:
        lang = settings.HOTELBEDS_HOTEL_LANGUAGE
        codes = [o["hotel"]["code"] for o in options if o["hotel"]["code"] is not None]
        if not codes:
            return
        found: dict[Any, dict] = {}
        missing = []
        for code in codes:
            hit = await self.c.cache.get(f"hotels:content:{code}:{lang}", product="hotels")
            if hit is not None:
                found[code] = hit
            else:
                missing.append(code)
        if missing:
            try:
                data = await self.c.hotels.content_hotels({
                    "codes": ",".join(str(c) for c in missing), "fields": "all", "language": lang,
                    "from": 1, "to": len(missing), "useSecondaryLanguage": "false"})
                for h in data.get("hotels") or []:
                    slim = _slim_content(h)
                    found[h.get("code")] = slim
                    await self.c.cache.put(f"hotels:content:{h.get('code')}:{lang}", slim)
            except HotelbedsError as exc:
                logger.warning("hotel_content_enrich_failed", error=exc.message)
                self.plan.add("INFO", "HOTEL", f"{dp.name}: hotel descriptions/images unavailable "
                              f"({exc.message}).", dp.index)
        if clean(dp.accommodation.preferedBrand):
            chains = await self._chains()
            for c in found.values():
                if c.get("chainCode") and not c.get("chainName"):
                    c["chainName"] = chains.get(c["chainCode"])
        for o in options:
            o["content"] = found.get(o["hotel"]["code"])

    async def _chains(self) -> dict:
        lang = settings.HOTELBEDS_HOTEL_LANGUAGE

        async def load():
            out: dict[str, str] = {}
            start = 1
            while True:
                data = await self.c.hotels.content_type("chains", {"fields": "all", "language": lang,
                                                                   "from": start, "to": start + 999})
                for ch in data.get("chains") or []:
                    desc = ch.get("description")
                    out[ch.get("code")] = desc.get("content") if isinstance(desc, dict) else desc
                total = data.get("total") or 0
                start += 1000
                if start > total or not data.get("chains"):
                    break
            return out

        try:
            return await self.c.cache.get_or_load(f"hotels:chains:{lang}", load, product="hotels")
        except HotelbedsError:
            return {}

    def _post_checks(self, sel: dict, dp: DestinationPlan, min_cat) -> None:
        acc = dp.accommodation
        stars = sel["hotel"]["stars"]
        if min_cat and stars and stars < min_cat:
            self.plan.add("WARNING", "HOTEL", f"{dp.name}: selected hotel is {stars}★ (requested {acc.hotelCategory}).",
                          dp.index, True)
        if clean(acc.preferedBrand) and "brand" not in sel["scoreBreakdown"]:
            self.plan.add("INFO", "HOTEL", f"{dp.name}: preferred brand '{acc.preferedBrand}' not available for these "
                          "dates.", dp.index)
        if clean(acc.roomType) and "roomType" not in sel["scoreBreakdown"]:
            self.plan.add("INFO", "HOTEL", f"{dp.name}: no '{acc.roomType}' room available in the selected hotel — "
                          f"offered '{sel['rooms'][0]['roomName']}'.", dp.index)
        if not sel["refundable"]:
            self.plan.add("WARNING", "HOTEL", f"{dp.name}: selected rate is NON-REFUNDABLE.", dp.index)
        if sel["requiresCheckRate"]:
            self.plan.add("INFO", "HOTEL", f"{dp.name}: rate is RECHECK — it will be re-priced at booking.", dp.index)
        if clean(acc.comment):
            self.plan.add("INFO", "HOTEL", f"{dp.name}: hotel comment '{acc.comment}' will be sent as a booking "
                          "remark (not guaranteed by the hotel).", dp.index)


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _txt(v) -> Optional[str]:
    if isinstance(v, dict):
        return v.get("content")
    return v


def _slim_content(h: dict) -> dict:
    images = sorted(h.get("images") or [], key=lambda i: (i.get("visualOrder", 999), i.get("order", 999)))
    return {
        "code": h.get("code"), "name": _txt(h.get("name")), "description": _txt(h.get("description")),
        "address": _txt(h.get("address")), "city": _txt(h.get("city")), "postalCode": h.get("postalCode"),
        "email": h.get("email"), "web": h.get("web"),
        "phones": [p.get("phoneNumber") for p in h.get("phones") or [] if p.get("phoneNumber")][:3],
        "chainCode": h.get("chainCode"), "accommodationType": h.get("accommodationTypeCode"),
        "categoryCode": h.get("categoryCode"), "ranking": h.get("ranking"),
        "coordinates": h.get("coordinates"),
        "facilityCodes": [f"{f.get('facilityGroupCode')}-{f.get('facilityCode')}" for f in h.get("facilities") or []][:80],
        "images": [{"url": image_url(i["path"], "bigger"), "thumb": image_url(i["path"], "medium"),
                    "type": i.get("imageTypeCode"), "roomCode": i.get("roomCode")}
                   for i in images if i.get("path")][:12],
    }
