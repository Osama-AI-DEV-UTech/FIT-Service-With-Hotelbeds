"""
Stage 3 — TRANSFERS (runs after hotels: the hotel's Hotelbeds code is
used directly as an ATLAS location, which is Hotelbeds' recommended,
most precise transfer location type).

Per destination: arrival (terminal → hotel) and/or departure
(hotel → terminal). When both use the same terminal, ONE round-trip
availability call returns both directions.
"""
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import structlog

from app.config import settings
from app.container import Container
from app.core.exceptions import HotelbedsError, ProviderNotConfigured
from app.fit.planner import DestinationPlan, TerminalLeg, TripPlan
from app.fit.pricing import price_line
from app.fit.resolvers import MODE_ROAD, LocationResolver
from app.fit.scoring import money, style_price_score
from app.utils.text import clean, normalize

logger = structlog.get_logger("fit.transfers")

_BIG_LUGGAGE = ("large", "big", "many", "extra", "oversize", "golf", "ski", "stroller", "pram", "wheelchair",
                "excess", "heavy", "bags")


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class TransferStage:
    def __init__(self, c: Container, resolver: LocationResolver, plan: TripPlan):
        self.c = c
        self.resolver = resolver
        self.plan = plan

    async def run(self, dp: DestinationPlan, hotel_result: Optional[dict]) -> dict:
        started = time.perf_counter()
        out: dict[str, Any] = {"arrival": None, "departure": None}
        legs = [leg for leg in (dp.arrival, dp.departure) if leg.required]
        if not legs:
            out["status"] = "NOT_REQUESTED"
            return out
        hotel = ((hotel_result or {}).get("selected") or {}).get("hotel") or {}
        try:
            await self._run(dp, hotel, out)
        except ProviderNotConfigured as exc:
            for leg in legs:
                out[leg.direction.lower()] = self._failed(leg, "PROVIDER_NOT_CONFIGURED", exc.message)
            self.plan.add("CRITICAL", "TRANSFER", f"{dp.name}: {exc.message}", dp.index, True)
        statuses = [v["status"] for v in (out["arrival"], out["departure"]) if v]
        out["status"] = "SELECTED" if statuses and all(s == "SELECTED" for s in statuses) else \
            ("PARTIAL" if any(s == "SELECTED" for s in statuses) else "UNAVAILABLE")
        out["durationMs"] = round((time.perf_counter() - started) * 1000, 1)
        return out

    async def _run(self, dp: DestinationPlan, hotel: dict, out: dict) -> None:
        if not hotel.get("code"):
            for leg in (dp.arrival, dp.departure):
                if leg.required:
                    out[leg.direction.lower()] = self._failed(leg, "NO_HOTEL", "No hotel selected — the hotel is the "
                                                              "transfer pickup/drop-off point")
                    self.plan.add("WARNING", "TRANSFER", f"{dp.name}: {leg.direction.lower()} transfer skipped — no "
                                  "hotel was selected.", dp.index, True)
            return

        atlas = {"type": "ATLAS", "code": str(hotel["code"]), "name": hotel.get("name")}
        resolved: dict[str, Optional[dict]] = {}
        for leg in (dp.arrival, dp.departure):
            if not leg.required:
                continue
            if leg.mode == MODE_ROAD:
                out[leg.direction.lower()] = self._failed(leg, "NOT_SUPPORTED", "Road travel — Hotelbeds sells only "
                                                          "airport / port / station transfers")
                continue
            term = await self.resolver.resolve_terminal(leg.terminal_text, mode=leg.mode, countries=dp.countries,
                                                        city=leg.city)
            if term is None:
                out[leg.direction.lower()] = self._failed(leg, "TERMINAL_NOT_RESOLVED",
                                                          f"Could not resolve the {leg.direction.lower()} "
                                                          f"airport/port/station for {leg.city}")
                self.plan.add("WARNING", "TRANSFER", f"{dp.name}: could not resolve the {leg.direction.lower()} "
                              f"terminal ('{leg.terminal_text or leg.city}', mode {leg.mode or 'FLIGHT'}). Provide "
                              "travelSegment.arrivalTerminalCode / an IATA code.", dp.index, True)
                continue
            resolved[leg.direction] = term

        arr, dep = resolved.get("ARRIVAL"), resolved.get("DEPARTURE")
        if (settings.FIT_TRANSFER_ROUND_TRIP_OPTIMISATION and arr and dep and arr["type"] == dep["type"]
                and arr["code"] == dep["code"] and dp.arrival.when and dp.departure.when):
            services = await self._search(dp, arr, atlas, dp.arrival.when, dp.departure.when)
            if services is not None:
                out["arrival"] = self._pick(dp, dp.arrival, arr, atlas,
                                            [s for s in services if s.get("direction") == "ARRIVAL"])
                out["departure"] = self._pick(dp, dp.departure, atlas, dep,
                                              [s for s in services if s.get("direction") == "DEPARTURE"])
                out["roundTripSearch"] = True
                return
        if arr:
            services = await self._search(dp, arr, atlas, dp.arrival.when, None)
            out["arrival"] = self._pick(dp, dp.arrival, arr, atlas, services or []) if services is not None else \
                self._failed(dp.arrival, "SUPPLIER_ERROR", "Hotelbeds transfer search failed")
        if dep:
            services = await self._search(dp, atlas, dep, dp.departure.when, None)
            out["departure"] = self._pick(dp, dp.departure, atlas, dep, services or []) if services is not None else \
                self._failed(dp.departure, "SUPPLIER_ERROR", "Hotelbeds transfer search failed")

    async def _search(self, dp: DestinationPlan, frm: dict, to: dict, outbound: Optional[datetime],
                      inbound: Optional[datetime]) -> Optional[list[dict]]:
        if outbound is None:
            return []
        adults, children, infants = self.plan.transfer_pax
        try:
            data = await self.c.transfers.availability(
                language=settings.HOTELBEDS_TRANSFER_LANGUAGE, from_type=frm["type"], from_code=frm["code"],
                to_type=to["type"], to_code=to["code"], outbound=fmt_dt(outbound),
                inbound=fmt_dt(inbound) if inbound else None, adults=adults, children=children, infants=infants)
        except HotelbedsError as exc:
            if exc.upstream_status in (204, 404):
                return []
            self.plan.add("WARNING", "TRANSFER", f"{dp.name}: Hotelbeds transfer search {frm['code']}→{to['code']} "
                          f"failed — {exc.message}", dp.index, True)
            return None
        return (data or {}).get("services") or []

    # ── selection ────────────────────────────────────────────────────
    def _pick(self, dp: DestinationPlan, leg: TerminalLeg, frm: dict, to: dict, services: list[dict]) -> dict:
        base = {"itemId": f"TRF-{uuid.uuid4().hex[:8]}", "direction": leg.direction,
                "from": {k: frm.get(k) for k in ("type", "code", "name")},
                "to": {k: to.get(k) for k in ("type", "code", "name")},
                "requestedDateTime": fmt_dt(leg.when) if leg.when else None,
                "dateTimeAssumed": leg.when_assumed, "travelMode": leg.mode or "FLIGHT"}
        if not services:
            self.plan.add("WARNING", "TRANSFER", f"{dp.name}: no Hotelbeds {leg.direction.lower()} transfer available "
                          f"{frm['code']} → {to['code']} at {base['requestedDateTime']}.", dp.index, True)
            return {**base, "status": "NO_AVAILABILITY", "selected": None, "alternatives": []}

        total_pax = sum(self.plan.transfer_pax)
        fitting = [s for s in services if not s.get("maxPaxCapacity") or int(s["maxPaxCapacity"]) >= total_pax] or services
        prices = [self._amount(s) for s in fitting]
        options = sorted((self._option(s, leg, dp, prices) for s in fitting), key=lambda o: o["score"], reverse=True)
        selected = options[0]
        self._post_checks(dp, leg, selected)
        return {**base, "status": "SELECTED", "selected": selected, "alternatives": options[1:3]}

    @staticmethod
    def _amount(s: dict) -> float:
        p = s.get("price") or {}
        return money(p.get("netAmount") if p.get("netAmount") not in (None, 0) else p.get("totalAmount"))

    def _option(self, s: dict, leg: TerminalLeg, dp: DestinationPlan, prices: list[float]) -> dict:
        b: dict[str, float] = {}
        ttype = str(s.get("transferType") or "").upper()
        cat = f"{(s.get('category') or {}).get('code', '')} {(s.get('category') or {}).get('name', '')}".upper()
        veh = f"{(s.get('vehicle') or {}).get('code', '')} {(s.get('vehicle') or {}).get('name', '')}".upper()
        pref = leg.type_pref
        if pref is None:
            style = self.plan.planning_style
            pref = "LUXURY" if style in ("LUXURY", "ULTRA_LUXURY") else ("SHARED" if style == "BUDGET" else "PRIVATE")
        if pref == "PRIVATE" and ttype == "PRIVATE":
            b["type"] = 20
        elif pref == "SHARED" and ttype in ("SHARED", "SHUTTLE"):
            b["type"] = 20
        elif pref == "LUXURY":
            if ttype == "PRIVATE":
                b["type"] = 10
            if any(k in cat for k in ("PRM", "LXR", "PREMIUM", "LUX", "VIP")):
                b["category"] = 15
        cs = self.plan.request.comfortSafety
        if normalize(cs.comfortWtCrowd) in ("low", "none", "avoid") and ttype == "PRIVATE":
            b["comfort"] = 6
        if self.plan.request.travelerInfo.isImmobile and ("DSBLD" in veh or "ADAPT" in veh):
            b["accessibility"] = 25
        if any(k in normalize(dp.transport.luggageComment) for k in _BIG_LUGGAGE) and any(
                k in veh for k in ("MNBS", "MINIBUS", "VAN", "BS ", "BUS")):
            b["luggage"] = 6
        amount = self._amount(s)
        b["price"] = round(style_price_score(amount, prices, self.plan.planning_style, weight=10), 2)

        pickup_info = s.get("pickupInformation") or {}
        pickup = pickup_info.get("pickup") or {}
        content = s.get("content") or {}
        p = s.get("price") or {}
        return {
            "optionId": uuid.uuid4().hex[:10],
            "rateKey": s.get("rateKey"),
            "serviceId": s.get("id"),
            "transferType": ttype, "vehicle": s.get("vehicle"), "category": s.get("category"),
            "minPaxCapacity": s.get("minPaxCapacity"), "maxPaxCapacity": s.get("maxPaxCapacity"),
            "pickup": {"date": pickup_info.get("date"), "time": pickup_info.get("time"),
                       "address": pickup.get("address"), "town": pickup.get("town"),
                       "description": pickup.get("description"),
                       "checkPickup": pickup.get("checkPickup")},
            "price": price_line("TRANSFER", amount, p.get("currencyId")),
            "supplierRetail": money(p.get("totalAmount")) if p.get("netAmount") not in (None, 0) else None,
            "cancellationPolicies": [{"amount": money(c.get("amount")), "from": c.get("from"),
                                      "currency": c.get("currencyId")} for c in s.get("cancellationPolicies") or []],
            "remarks": [{"type": r.get("type"), "description": r.get("description"), "mandatory": r.get("mandatory")}
                        for r in content.get("transferRemarks") or []],
            "details": [{"id": d.get("id"), "name": d.get("name"), "description": d.get("description")}
                        for d in content.get("transferDetailInfo") or []][:8],
            "waitingTime": content.get("supplierTransferTimeInfo"),
            "images": [i.get("url") for i in content.get("images") or [] if i.get("url")][:3],
            "extrasAvailable": s.get("extras") or [],
            "factsheetId": s.get("factsheetId"),
            "score": round(sum(b.values()), 2), "scoreBreakdown": b,
        }

    def _post_checks(self, dp: DestinationPlan, leg: TerminalLeg, sel: dict) -> None:
        check = (sel.get("pickup") or {}).get("checkPickup") or {}
        if check.get("mustCheckPickupTime"):
            self.plan.add("INFO", "TRANSFER", f"{dp.name}: {leg.direction.lower()} pickup time must be checked at "
                          f"{check.get('url')} {check.get('hoursBeforeConsulting')}h before travel — show this to the "
                          "customer.", dp.index)
        if leg.type_pref == "SHARED" and sel["transferType"] != "SHARED":
            self.plan.add("INFO", "TRANSFER", f"{dp.name}: no shared {leg.direction.lower()} transfer — private "
                          "offered.", dp.index)
        if leg.type_pref in ("PRIVATE", "LUXURY") and sel["transferType"] not in ("PRIVATE",):
            self.plan.add("WARNING", "TRANSFER", f"{dp.name}: no private {leg.direction.lower()} transfer — "
                          f"{sel['transferType'].lower()} offered.", dp.index, True)
        if self.plan.request.travelerInfo.isImmobile and "accessibility" not in sel["scoreBreakdown"]:
            self.plan.add("WARNING", "ACCESSIBILITY", f"{dp.name}: no adapted vehicle for the "
                          f"{leg.direction.lower()} transfer — confirm with the supplier.", dp.index, True)
        if clean(dp.transport.luggageComment):
            self.plan.add("INFO", "TRANSFER", f"{dp.name}: luggage note '{dp.transport.luggageComment}' will be sent "
                          "as a transfer remark.", dp.index)

    @staticmethod
    def _failed(leg: TerminalLeg, status: str, reason: str) -> dict:
        return {"itemId": f"TRF-{uuid.uuid4().hex[:8]}", "direction": leg.direction, "status": status,
                "reason": reason, "selected": None, "alternatives": [],
                "requestedDateTime": fmt_dt(leg.when) if leg.when else None}

    # used by the booking service when the customer switches hotel
    async def research_leg(self, dp: DestinationPlan, leg: TerminalLeg, hotel_code: str, hotel_name: str) -> dict:
        atlas = {"type": "ATLAS", "code": str(hotel_code), "name": hotel_name}
        term = await self.resolver.resolve_terminal(leg.terminal_text, mode=leg.mode, countries=dp.countries,
                                                    city=leg.city)
        if term is None:
            return self._failed(leg, "TERMINAL_NOT_RESOLVED", "Terminal could not be resolved")
        frm, to = (term, atlas) if leg.direction == "ARRIVAL" else (atlas, term)
        services = await self._search(dp, frm, to, leg.when, None)
        if services is None:
            return self._failed(leg, "SUPPLIER_ERROR", "Hotelbeds transfer search failed")
        return self._pick(dp, leg, frm, to, services)
