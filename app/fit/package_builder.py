"""
Stage 4 — PACKAGE. Assembles the three stage results into:

* displayPackage  — what the end user sees (summary, per-destination
                    components, day-by-day itinerary, pricing, cancellation)
* bookingPayload  — everything the booking service needs later (rateKeys,
                    activity code/modality/rate/date, alternatives)
"""
from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.fit.planner import DestinationPlan, TripPlan
from app.fit.pricing import summarize
from app.utils.text import clean

_DAYPART_TIME = {"MORNING": "09:00", "AFTERNOON": "14:00", "EVENING": "18:00", "NIGHT": "20:00",
                 "FULL_DAY": "08:30", "FULLDAY": "08:30"}


def _hotel_view(opt: Optional[dict]) -> Optional[dict]:
    if not opt:
        return None
    content = opt.get("content") or {}
    return {
        "optionId": opt["optionId"],
        "code": opt["hotel"]["code"], "name": opt["hotel"]["name"], "stars": opt["hotel"]["stars"],
        "category": opt["hotel"]["categoryName"], "zone": opt["hotel"]["zoneName"],
        "latitude": opt["hotel"]["latitude"], "longitude": opt["hotel"]["longitude"],
        "address": content.get("address"), "city": content.get("city"), "description": content.get("description"),
        "images": content.get("images") or [], "web": content.get("web"), "phones": content.get("phones"),
        "checkIn": opt["checkIn"], "checkOut": opt["checkOut"], "nights": opt["nights"],
        "rooms": [{"roomName": r["roomName"], "roomCode": r["roomCode"], "board": r["boardName"],
                   "boardCode": r["boardCode"], "rooms": r["rooms"], "adults": r["adults"], "children": r["children"],
                   "childAges": r["childAges"], "rateClass": r["rateClass"],
                   "cancellationPolicies": r["cancellationPolicies"], "offers": r.get("offers"),
                   "promotions": r.get("promotions")} for r in opt["rooms"]],
        "price": opt["price"], "refundable": opt["refundable"], "freeCancellationUntil": opt["freeCancellationUntil"],
        "score": opt["score"], "scoreBreakdown": opt["scoreBreakdown"],
    }


def _transfer_view(leg: Optional[dict]) -> Optional[dict]:
    if not leg:
        return None
    view = {k: leg.get(k) for k in ("itemId", "direction", "status", "reason", "from", "to", "requestedDateTime",
                                    "dateTimeAssumed", "travelMode")}
    sel = leg.get("selected")
    if sel:
        view["selected"] = {k: sel.get(k) for k in ("optionId", "transferType", "vehicle", "category",
                                                     "maxPaxCapacity", "pickup", "price", "cancellationPolicies",
                                                     "remarks", "details", "waitingTime", "images", "extrasAvailable")}
        view["alternatives"] = [{k: a.get(k) for k in ("optionId", "transferType", "vehicle", "category", "price")}
                                for a in leg.get("alternatives") or []]
    return view


def _activity_view(item: dict) -> dict:
    keys = ("itemId", "status", "isSuggested", "requested", "reason", "date", "from", "to", "activity", "modality",
            "rate", "session", "language", "languagesOffered", "pax", "price", "cancellationPolicies", "questions",
            "matchScore", "matchReasons", "alternatives", "dateChanged", "comment")
    return {k: item[k] for k in keys if k in item}


def _safe_date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


class PackageBuilder:
    def __init__(self, plan: TripPlan):
        self.plan = plan

    def build(self, package_id: str, results: list[dict], narrative: Optional[str]) -> tuple[dict, dict]:
        plan = self.plan
        destinations_view, lines, optional_lines = [], [], []
        booking: dict[str, Any] = {"version": 1, "sourceMarket": plan.source_market,
                                   "hotels": [], "activities": [], "transfers": []}

        for dp, res in zip(plan.destinations, results):
            hotel, acts, trf = res["hotel"], res["activities"], res["transfers"]
            sel = hotel.get("selected")
            if sel:
                lines.append({"category": "HOTEL", "destinationIndex": dp.index, "itemId": f"HTL-{dp.index}",
                              "label": f"{sel['hotel']['name']} — {dp.nights} night(s)", "price": sel["price"]})
                booking["hotels"].append(self._hotel_booking(dp, sel, hotel.get("alternatives") or []))
            for item in acts.get("items") or []:
                if item["status"] == "MATCHED":
                    lines.append({"category": "ACTIVITY", "destinationIndex": dp.index, "itemId": item["itemId"],
                                  "label": f"{item['activity']['name']} ({item['date']})", "price": item["price"]})
                    booking["activities"].append(self._activity_booking(dp, item, optional=False))
            for item in acts.get("suggested") or []:
                optional_lines.append({"category": "ACTIVITY", "destinationIndex": dp.index, "itemId": item["itemId"],
                                       "label": f"{item['activity']['name']} ({item['date']}) — suggested",
                                       "price": item["price"]})
                booking["activities"].append(self._activity_booking(dp, item, optional=True))
            for key in ("arrival", "departure"):
                leg = trf.get(key)
                if leg and leg.get("selected"):
                    s = leg["selected"]
                    lines.append({"category": "TRANSFER", "destinationIndex": dp.index, "itemId": leg["itemId"],
                                  "label": f"{key.title()} transfer {leg['from']['code']} → {leg['to']['code']} "
                                           f"({s['transferType'].lower()})", "price": s["price"]})
                    booking["transfers"].append(self._transfer_booking(dp, leg))

            destinations_view.append({
                "index": dp.index, "name": dp.name, "checkIn": dp.check_in.isoformat(),
                "checkOut": dp.check_out.isoformat(), "nights": dp.nights,
                "resolvedDestination": hotel.get("destination") or acts.get("destination"),
                "hotel": {"status": hotel["status"], "selected": _hotel_view(sel),
                          "alternatives": [_hotel_view(a) for a in hotel.get("alternatives") or []],
                          "candidatesFound": hotel.get("candidatesFound")},
                "activities": {"status": acts.get("status"),
                               "items": [_activity_view(i) for i in acts.get("items") or []],
                               "suggested": [_activity_view(i) for i in acts.get("suggested") or []]},
                "transfers": {"status": trf.get("status"), "arrival": _transfer_view(trf.get("arrival")),
                              "departure": _transfer_view(trf.get("departure"))},
            })

        pricing = summarize(lines, plan.budget_total, plan.currency)
        pricing["optionalExtras"] = summarize(optional_lines, None, plan.currency) if optional_lines else None
        display = {
            "summary": self._summary(),
            "narrative": narrative,
            "destinations": destinations_view,
            "itinerary": self._itinerary(results),
            "pricing": pricing,
            "cancellation": self._cancellation(results),
            "addOns": {"travelInsurance": plan.request.foodGuideEtc.travelInsuranceReqd,
                       "esim": plan.request.foodGuideEtc.esimReqd, "evisa": plan.request.foodGuideEtc.evisaReqd,
                       "note": "Add-ons are fulfilled outside Hotelbeds"},
            "preferences": {"mealPref": clean(plan.request.foodGuideEtc.mealPref),
                            "guideLanguage": clean(plan.request.foodGuideEtc.guideLanguage),
                            "comfort": plan.request.comfortSafety.model_dump(exclude_none=True)},
        }
        if pricing.get("budgetCheck") and pricing["budgetCheck"].get("withinBudget") is False:
            plan.add("WARNING", "BUDGET", f"Package total {pricing['budgetCheck']['packageTotal']} "
                     f"{plan.currency} exceeds the budget {pricing['budgetCheck']['budget']} by "
                     f"{-pricing['budgetCheck']['difference']}.", None, True)
        elif pricing.get("budgetCheck") and pricing["budgetCheck"].get("comparable") is False:
            plan.add("INFO", "BUDGET", pricing["budgetCheck"]["note"], None)
        booking["currencies"] = [t["currency"] for t in pricing["totalsByCurrency"]]
        return display, booking

    # ── booking payload pieces ───────────────────────────────────────
    @staticmethod
    def _hotel_option_payload(opt: dict) -> dict:
        return {"optionId": opt["optionId"], "hotelCode": opt["hotel"]["code"], "hotelName": opt["hotel"]["name"],
                "checkIn": opt["checkIn"], "checkOut": opt["checkOut"],
                "rooms": [{"rateKey": r["rateKey"], "rateType": r["rateType"], "rooms": r["rooms"], "adults": r["adults"],
                           "children": r["children"], "childAges": r["childAges"], "roomName": r["roomName"],
                           "boardCode": r["boardCode"], "expectedNet": r["price"]["net"],
                           "expectedSelling": r["price"]["selling"]} for r in opt["rooms"]],
                "expectedNet": opt["price"]["net"], "expectedSelling": opt["price"]["selling"],
                "currency": opt["price"]["currency"]}

    def _hotel_booking(self, dp: DestinationPlan, sel: dict, alternatives: list[dict]) -> dict:
        acc = dp.accommodation
        return {"itemId": f"HTL-{dp.index}", "destinationIndex": dp.index, "destinationName": dp.name,
                **self._hotel_option_payload(sel),
                "remark": clean(acc.comment) if acc else None,
                "alternatives": [self._hotel_option_payload(a) for a in alternatives]}

    @staticmethod
    def _activity_booking(dp: DestinationPlan, item: dict, optional: bool) -> dict:
        return {"itemId": item["itemId"], "destinationIndex": dp.index, "optional": optional,
                "activityCode": item["activity"]["code"], "activityName": item["activity"]["name"],
                "modalityCode": item["modality"]["code"], "rateCode": item["rate"]["rateCode"],
                "rateClass": item["rate"]["rateClass"], "date": item["date"], "from": item["from"], "to": item["to"],
                "session": (item.get("session") or {}).get("code"), "language": item.get("language"),
                "pax": item["pax"], "expectedAmount": item["price"]["net"], "currency": item["price"]["currency"],
                "questions": item.get("questions") or []}

    @staticmethod
    def _transfer_booking(dp: DestinationPlan, leg: dict) -> dict:
        s = leg["selected"]
        return {"itemId": leg["itemId"], "destinationIndex": dp.index, "direction": leg["direction"],
                "rateKey": s["rateKey"], "transferType": s["transferType"], "from": leg["from"], "to": leg["to"],
                "travelMode": leg.get("travelMode"), "requestedDateTime": leg.get("requestedDateTime"),
                "expectedAmount": s["price"]["net"], "currency": s["price"]["currency"],
                "luggageComment": clean(dp.transport.luggageComment)}

    # ── views ────────────────────────────────────────────────────────
    def _summary(self) -> dict:
        p = self.plan
        names = [d.name for d in p.destinations]
        return {"title": f"{p.total_nights}-night trip: {' → '.join(names)}",
                "startDate": p.destinations[0].check_in.isoformat(),
                "endDate": p.destinations[-1].check_out.isoformat(),
                "totalNights": p.total_nights, "destinations": names,
                "travellers": {"adults": p.adults, "children": len(p.child_ages), "childAges": p.child_ages},
                "travelerType": clean(p.request.travelerInfo.travelerType), "planningStyle": p.planning_style,
                "sourceMarket": p.source_market, "budget": p.budget_total, "budgetCurrency": p.currency}

    def _itinerary(self, results: list[dict]) -> list[dict]:
        days: dict[date, list[dict]] = {}
        dest_of_day: dict[date, str] = {}
        for dp, res in zip(self.plan.destinations, results):
            for d in dp.stay_days:
                dest_of_day.setdefault(d, dp.name)
            hotel = (res["hotel"].get("selected") or {}).get("hotel") or {}
            hname = hotel.get("name") or "hotel (to be confirmed)"
            arr = res["transfers"].get("arrival")
            if arr and arr.get("selected"):
                t = (arr.get("requestedDateTime") or "")[11:16] or None
                self._event(days, dp.check_in, t, "TRANSFER", f"Arrival transfer {arr['from']['code']} → {hname}",
                            f"{arr['selected']['transferType'].title()} · {(arr['selected'].get('vehicle') or {}).get('name')}",
                            arr["itemId"])
            elif dp.index == self.plan.destinations[0].index or dp.arrival.when:
                t = dp.arrival.when.strftime("%H:%M") if dp.arrival.when else None
                self._event(days, dp.check_in, t, "ARRIVAL", f"Arrive in {dp.name}", None, None)
            if res["hotel"].get("selected"):
                arrival_time = dp.arrival.when.strftime("%H:%M") if dp.arrival.when and \
                    dp.arrival.when.date() == dp.check_in else None
                checkin = max("14:00", arrival_time or "00:00")
                dep_leg = res["transfers"].get("departure") or {}
                dep_pick = ((dep_leg.get("selected") or {}).get("pickup") or {}).get("time")
                checkout = min("12:00", (dep_pick or "99:99")[:5])
                self._event(days, dp.check_in, checkin, "HOTEL", f"Check-in: {hname}",
                            ", ".join(r["boardName"] or "" for r in res["hotel"]["selected"]["rooms"]), f"HTL-{dp.index}")
                self._event(days, dp.check_out, checkout, "HOTEL", f"Check-out: {hname}", None, f"HTL-{dp.index}")
            for item in (res["activities"].get("items") or []) + (res["activities"].get("suggested") or []):
                if item.get("status") not in ("MATCHED", "SUGGESTED"):
                    continue
                dpart = ((item.get("requested") or {}).get("dayPart") or "").upper().replace(" ", "_")
                t = _DAYPART_TIME.get(dpart) or ((item.get("session") or {}).get("name") or "")[:5] or None
                if t and not (len(t) == 5 and t[2] == ":"):
                    t = None
                title = item["activity"]["name"] + (" (optional suggestion)" if item.get("isSuggested") else "")
                self._event(days, date.fromisoformat(item["date"]), t, "ACTIVITY", title,
                            item["modality"].get("name"), item["itemId"])
            dep = res["transfers"].get("departure")
            if dep and dep.get("selected"):
                pick = dep["selected"].get("pickup") or {}
                t = (pick.get("time") or "")[:5] or None
                d = _safe_date(pick.get("date")) or dp.check_out
                self._event(days, d, t, "TRANSFER", f"Departure transfer {hname} → {dep['to']['code']}",
                            f"{dep['selected']['transferType'].title()} · pickup "
                            f"{t or 'time to be confirmed'}", dep["itemId"])

        if not dest_of_day:
            return []
        start, end = min(dest_of_day), max(dest_of_day)
        out = []
        for n in range((end - start).days + 1):
            d = start + timedelta(days=n)
            events = sorted(days.get(d, []), key=lambda e: e["time"] or "99:99")
            if not events:
                events = [{"time": None, "type": "FREE", "title": "Free day at leisure", "details": None, "itemId": None}]
            out.append({"day": n + 1, "date": d.isoformat(), "weekday": d.strftime("%A"),
                        "destination": dest_of_day.get(d), "events": events})
        return out

    @staticmethod
    def _event(days: dict, d: date, time: Optional[str], kind: str, title: str, details: Optional[str],
               item_id: Optional[str]) -> None:
        days.setdefault(d, []).append({"time": time, "type": kind, "title": title, "details": details or None,
                                       "itemId": item_id})

    @staticmethod
    def _cancellation(results: list[dict]) -> dict:
        deadlines: list[str] = []
        non_refundable: list[str] = []
        for res in results:
            sel = res["hotel"].get("selected")
            if sel:
                if not sel["refundable"]:
                    non_refundable.append(sel["hotel"]["name"])
                if sel.get("freeCancellationUntil"):
                    deadlines.append(sel["freeCancellationUntil"])
            for item in res["activities"].get("items") or []:
                if item.get("status") == "MATCHED":
                    if item["rate"].get("rateClass") == "NRF":
                        non_refundable.append(item["activity"]["name"])
                    deadlines += [p["dateFrom"] for p in item.get("cancellationPolicies") or [] if p.get("dateFrom")]
            for key in ("arrival", "departure"):
                leg = res["transfers"].get(key)
                if leg and leg.get("selected"):
                    deadlines += [p["from"] for p in leg["selected"].get("cancellationPolicies") or [] if p.get("from")]

        def _key(v: str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return datetime.max
        earliest = min(deadlines, key=_key) if deadlines else None
        return {"fullyRefundableNow": not non_refundable, "nonRefundableItems": non_refundable,
                "earliestPenaltyFrom": earliest,
                "note": "Cancellation charges apply from the listed date-times (destination local time)."}
