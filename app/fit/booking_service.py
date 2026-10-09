"""
Package booking lifecycle: prebook (validate & re-price) → confirm → status → cancel.

Order of confirmation: HOTELS → ACTIVITIES → TRANSFERS (the hotel is the
anchor of everything else). In ALL_OR_NOTHING mode a failure rolls back
every component already confirmed in this run.

Hotelbeds rules applied:
  * hotels: CheckRate before booking (mandatory for RECHECK, done always
    to get final price, rate comments and paymentDataRequired); never
    modify rateKeys; booking calls are not retried — timeouts are
    reconciled through the booking list by clientReference.
  * activities: a fresh rateKey from /activities/details (valid 30 min,
    single use) right before confirming; every question answered.
  * transfers: transferDetails (flight/train/cruise number) mandatory.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import structlog

from app.config import settings
from app.container import Container
from app.core.exceptions import Conflict, HotelbedsError, NotFound, ValidationFailed
from app.fit.planner import Planner
from app.fit.resolvers import MODE_SEA, MODE_TRAIN, LocationResolver, normalize_mode
from app.fit.scoring import money
from app.fit.transfer_stage import TransferStage
from app.schemas.fit import (BookingTraveller, CancelPackageRequest, ComponentSelection, ConfirmPackageRequest,
                             FITRequest, PrebookRequest)
from app.utils.countries import to_iso2
from app.utils.text import normalize

logger = structlog.get_logger("fit.booking")

CONFIRMED_STATES = {"CONFIRMED", "PRECONFIRMED"}


def _pct_diff(new: float, old: float) -> float:
    return 0.0 if not old else round((new - old) / old * 100, 2)


class BookingService:
    def __init__(self, c: Container):
        self.c = c

    # ═════════════════════════════════════════════════════════════════
    # helpers
    # ═════════════════════════════════════════════════════════════════
    async def _load(self, package_id: str) -> dict:
        rec = await self.c.store.get_package(package_id)
        if rec is None:
            raise NotFound(f"Package {package_id} not found", code="PACKAGE_NOT_FOUND")
        return rec

    @staticmethod
    def _expired(rec: dict) -> bool:
        try:
            return datetime.fromisoformat(rec["expiresAt"]) < datetime.now(timezone.utc)
        except (KeyError, ValueError):
            return False

    @staticmethod
    def _client_ref(package_id: str, suffix: str) -> str:
        # Hotelbeds: max 20 chars.
        core = package_id.split("-")[-1]
        return f"{settings.BOOKING_CLIENT_REFERENCE_PREFIX}{core}{suffix}"[:20]

    @staticmethod
    def _apply_selection(payload: dict, sel: ComponentSelection) -> dict:
        """Returns a working copy of the booking payload with selections applied."""
        hotels = []
        for h in payload.get("hotels") or []:
            if h["itemId"] in sel.excludeItemIds:
                continue
            chosen = sel.hotelOptionIds.get(str(h["destinationIndex"]))
            if chosen and chosen != h["optionId"]:
                alt = next((a for a in h.get("alternatives") or [] if a["optionId"] == chosen), None)
                if alt is None:
                    raise ValidationFailed(f"Hotel optionId '{chosen}' is not an alternative of destination "
                                           f"{h['destinationIndex']}", code="INVALID_SELECTION")
                h = {**h, **alt, "swapped": True}
            hotels.append(h)
        activities = [a for a in payload.get("activities") or []
                      if a["itemId"] not in sel.excludeItemIds
                      and (not a.get("optional") or a["itemId"] in sel.includeSuggestedItemIds)]
        transfers = [t for t in payload.get("transfers") or [] if t["itemId"] not in sel.excludeItemIds]
        return {"hotels": hotels, "activities": activities, "transfers": transfers}

    @staticmethod
    def _validate_travellers(req_snapshot: dict, travellers: list[BookingTraveller]) -> tuple[list, list]:
        trav = req_snapshot["travelerInfo"]
        adults = [t for t in travellers if t.type == "ADULT"]
        children = [t for t in travellers if t.type == "CHILD"]
        if len(adults) != trav["adults"] or len(children) != (trav.get("children") or 0):
            raise ValidationFailed(
                f"Package was priced for {trav['adults']} adult(s) and {trav.get('children') or 0} child(ren); "
                f"received {len(adults)} adult(s) and {len(children)} child(ren)", code="TRAVELLERS_MISMATCH")
        quoted = sorted(int(float(a)) for a in trav.get("childAges") or [])
        given = sorted(c.age for c in children)
        if quoted != given:
            raise ValidationFailed(f"Child ages {given} differ from the quoted ages {quoted} — regenerate the package",
                                   code="CHILD_AGES_MISMATCH")
        return adults, children

    @staticmethod
    def _take_children(pool: list[BookingTraveller], ages: list[int]) -> list[BookingTraveller]:
        out = []
        for age in ages:
            idx = next((i for i, c in enumerate(pool) if c.age == age), None)
            if idx is None:
                idx = min(range(len(pool)), key=lambda i: abs((pool[i].age or 0) - age)) if pool else None
            if idx is not None:
                out.append(pool.pop(idx))
        return out

    # ═════════════════════════════════════════════════════════════════
    # pre-booking validation (re-price)
    # ═════════════════════════════════════════════════════════════════
    async def _checkrate_hotel(self, h: dict) -> dict:
        lang = settings.HOTELBEDS_HOTEL_LANGUAGE
        data = await self.c.hotels.checkrates({"rooms": [{"rateKey": r["rateKey"]} for r in h["rooms"]],
                                              "language": lang})
        hotel = (data or {}).get("hotel") or {}
        rates = [rate for room in hotel.get("rooms") or [] for rate in room.get("rates") or []]
        new_rooms = []
        for i, line in enumerate(h["rooms"]):
            rate = rates[i] if i < len(rates) else {}
            mandatory = bool(rate.get("hotelMandatory"))
            new_rooms.append({**line, "rateKey": rate.get("rateKey") or line["rateKey"],
                              "net": money(rate.get("net")) if rate else line["expectedNet"],
                              "sellingRate": money(rate.get("sellingRate")) if mandatory else None,
                              "rateComments": rate.get("rateComments"),
                              "cancellationPolicies": rate.get("cancellationPolicies")})
        total_net = money(hotel.get("totalNet")) or sum(r["net"] for r in new_rooms)
        diff = _pct_diff(total_net, h["expectedNet"])
        return {"itemId": h["itemId"], "type": "HOTEL", "hotelCode": h["hotelCode"], "hotelName": h["hotelName"],
                "rooms": new_rooms, "expectedNet": h["expectedNet"], "net": total_net,
                "currency": hotel.get("currency") or h.get("currency"), "priceChangePct": diff,
                "priceChanged": abs(diff) > settings.BOOKING_PRICE_TOLERANCE_PCT,
                "paymentDataRequired": bool(hotel.get("paymentDataRequired")),
                "modificationPolicies": hotel.get("modificationPolicies"),
                "rateComments": [r["rateComments"] for r in new_rooms if r.get("rateComments")]}

    async def _detail_activity(self, a: dict) -> dict:
        paxes = [{"age": settings.FIT_ACTIVITY_DEFAULT_ADULT_AGE}] * a["pax"]["adults"] + \
            [{"age": age} for age in a["pax"]["childAges"]]
        data = await self.c.activities.details({"code": a["activityCode"], "from": a["from"], "to": a["to"],
                                                "language": settings.HOTELBEDS_ACTIVITY_LANGUAGE, "paxes": paxes,
                                                "modalityCode": a["modalityCode"]})
        act = (data or {}).get("activity") or ((data or {}).get("activities") or [None])[0] or {}
        target_day = date.fromisoformat(a["date"])
        chosen = None
        for m in act.get("modalities") or []:
            if m.get("code") != a["modalityCode"]:
                continue
            for r in m.get("rates") or []:
                if a.get("rateCode") and r.get("rateCode") != a["rateCode"]:
                    continue
                for rd in r.get("rateDetails") or []:
                    for op in rd.get("operationDates") or []:
                        try:
                            ok = date.fromisoformat(str(op.get("from"))[:10]) <= target_day <= \
                                date.fromisoformat(str(op.get("to"))[:10])
                        except ValueError:
                            ok = False
                        if ok and rd.get("rateKey"):
                            chosen = (m, r, rd, op)
                            break
                    if chosen:
                        break
                if chosen:
                    break
            if chosen:
                break
        if chosen is None:
            return {"itemId": a["itemId"], "type": "ACTIVITY", "available": False,
                    "reason": "Activity/modality no longer available on this date"}
        m, r, rd, op = chosen
        amount = money((rd.get("totalAmount") or {}).get("amount"))
        diff = _pct_diff(amount, a["expectedAmount"])
        return {"itemId": a["itemId"], "type": "ACTIVITY", "available": True, "rateKey": rd["rateKey"],
                "activityName": act.get("name") or a.get("activityName"), "net": amount,
                "currency": act.get("currency") or a.get("currency"), "expectedNet": a["expectedAmount"],
                "priceChangePct": diff, "priceChanged": abs(diff) > settings.BOOKING_PRICE_TOLERANCE_PCT,
                "questions": m.get("questions") or [], "sessions": rd.get("sessions") or [],
                "cancellationPolicies": op.get("cancellationPolicies") or [],
                "rateKeyObtainedAt": datetime.now(timezone.utc).isoformat()}

    async def _prebook_items(self, work: dict) -> list[dict]:
        tasks = []
        for h in work["hotels"]:
            tasks.append(self._safe(self._checkrate_hotel(h), h["itemId"], "HOTEL"))
        for a in work["activities"]:
            tasks.append(self._safe(self._detail_activity(a), a["itemId"], "ACTIVITY"))
        results = list(await asyncio.gather(*tasks))
        for t in work["transfers"]:
            results.append({"itemId": t["itemId"], "type": "TRANSFER", "available": True, "net": t["expectedAmount"],
                            "currency": t["currency"], "priceChanged": False,
                            "note": "Transfers have no re-price step; the availability rateKey is booked directly"})
        return results

    @staticmethod
    async def _safe(coro, item_id: str, kind: str) -> dict:
        try:
            return await coro
        except HotelbedsError as exc:
            return {"itemId": item_id, "type": kind, "available": False, "error": exc.message,
                    "upstreamCode": exc.upstream_code, "upstreamStatus": exc.upstream_status}

    async def prebook(self, package_id: str, req: PrebookRequest) -> dict:
        rec = await self._load(package_id)
        if self._expired(rec):
            raise Conflict("Package rates have expired — regenerate the package", code="PACKAGE_EXPIRED")
        work = self._apply_selection(rec["package"]["bookingPayload"], req.selection)
        items = await self._prebook_items(work)
        return {"packageId": package_id, "checkedAt": datetime.now(timezone.utc).isoformat(), "items": items,
                "allAvailable": all(i.get("available", True) and not i.get("error") for i in items),
                "anyPriceChanged": any(i.get("priceChanged") for i in items),
                "paymentDataRequired": any(i.get("paymentDataRequired") for i in items),
                "activityQuestions": {i["itemId"]: i.get("questions") for i in items
                                      if i["type"] == "ACTIVITY" and i.get("questions")},
                "note": "Activity rateKeys are valid for 30 minutes — confirm promptly."}

    # ═════════════════════════════════════════════════════════════════
    # confirm
    # ═════════════════════════════════════════════════════════════════
    async def confirm(self, package_id: str, req: ConfirmPackageRequest) -> dict:
        rec = await self._load(package_id)
        if rec["status"] in ("CONFIRMED", "PARTIALLY_CONFIRMED", "BOOKING_IN_PROGRESS"):
            raise Conflict(f"Package is already {rec['status']}", code="PACKAGE_ALREADY_BOOKED")
        if rec["status"] == "FAILED":
            raise Conflict("Package generation failed — nothing to book", code="PACKAGE_NOT_BOOKABLE")
        if self._expired(rec):
            raise Conflict("Package rates have expired — regenerate the package", code="PACKAGE_EXPIRED")

        snapshot = rec["request"]
        adults, children = self._validate_travellers(snapshot, req.travellers)
        work = self._apply_selection(rec["package"]["bookingPayload"], req.selection)
        if not (work["hotels"] or work["activities"] or work["transfers"]):
            raise ValidationFailed("Nothing to book after applying the selection", code="EMPTY_SELECTION")

        # Hotel swapped → its transfers must be re-searched for the new ATLAS code.
        await self._refresh_transfers_for_swaps(snapshot, work)
        codes = self._transport_codes(work, req)

        pre = await self._prebook_items(work)
        problems = [p for p in pre if p.get("error") or p.get("available") is False]
        if problems and req.bookingMode == "ALL_OR_NOTHING":
            raise Conflict("Some components are no longer available — nothing was booked", code="NOT_AVAILABLE",
                           details={"items": problems})
        changed = [p for p in pre if p.get("priceChanged")]
        if changed and not req.acceptPriceChanges:
            raise Conflict("Prices changed beyond tolerance — review and resend with acceptPriceChanges=true",
                           code="PRICE_CHANGED", details={"items": changed})
        pay = [p for p in pre if p.get("paymentDataRequired")]
        if pay:
            raise Conflict("A hotel rate requires card payment data (paymentDataRequired) — choose an alternative "
                           "hotel option", code="PAYMENT_DATA_REQUIRED", details={"items": pay})
        pre_by_id = {p["itemId"]: p for p in pre}
        answers = self._answers(work, pre_by_id, req, rec)

        await self.c.store.update_package(package_id, status="BOOKING_IN_PROGRESS")
        results: list[dict] = []
        failed = False
        try:
            for h in work["hotels"]:
                if pre_by_id.get(h["itemId"], {}).get("available") is False or pre_by_id.get(h["itemId"], {}).get("error"):
                    results.append({"itemId": h["itemId"], "type": "HOTEL", "status": "SKIPPED_UNAVAILABLE"})
                    continue
                r = await self._book_hotel(package_id, h, pre_by_id[h["itemId"]], req, adults, children)
                results.append(r)
                if r["status"] not in CONFIRMED_STATES:
                    failed = True
                    if req.bookingMode == "ALL_OR_NOTHING":
                        break
            if not (failed and req.bookingMode == "ALL_OR_NOTHING"):
                for a in work["activities"]:
                    p = pre_by_id.get(a["itemId"], {})
                    if p.get("available") is False or p.get("error"):
                        results.append({"itemId": a["itemId"], "type": "ACTIVITY", "status": "SKIPPED_UNAVAILABLE"})
                        continue
                    r = await self._book_activity(package_id, a, p, req, adults, children, answers.get(a["itemId"], []))
                    results.append(r)
                    if r["status"] not in CONFIRMED_STATES:
                        failed = True
                        if req.bookingMode == "ALL_OR_NOTHING":
                            break
            if not (failed and req.bookingMode == "ALL_OR_NOTHING"):
                for t in work["transfers"]:
                    r = await self._book_transfer(package_id, t, req, codes[t["itemId"]])
                    results.append(r)
                    if r["status"] not in CONFIRMED_STATES:
                        failed = True
                        if req.bookingMode == "ALL_OR_NOTHING":
                            break
        except Exception:
            logger.exception("booking_run_crashed", package_id=package_id)
            failed = True

        rollback = []
        if failed and req.bookingMode == "ALL_OR_NOTHING":
            rollback = await self._rollback(results)

        confirmed = [r for r in results if r["status"] in CONFIRMED_STATES and not r.get("rolledBack")]
        unknown = [r for r in results if r["status"] == "UNKNOWN"]
        if failed and req.bookingMode == "ALL_OR_NOTHING":
            status = "BOOKING_FAILED_ROLLED_BACK" if not unknown else "BOOKING_NEEDS_ATTENTION"
        elif failed or unknown:
            status = "PARTIALLY_CONFIRMED" if confirmed else "BOOKING_FAILED"
        else:
            status = "CONFIRMED"

        booking_state = {"status": status, "bookedAt": datetime.now(timezone.utc).isoformat(),
                         "mode": req.bookingMode, "holder": req.holder.model_dump(mode="json"),
                         "items": results, "rollback": rollback, "prebook": pre}
        # A failed/rolled-back package stays re-bookable (status back to GENERATED-like state).
        new_pkg_status = status if status in ("CONFIRMED", "PARTIALLY_CONFIRMED", "BOOKING_NEEDS_ATTENTION") \
            else rec["status"]
        await self.c.store.update_package(package_id, status=new_pkg_status, booking=booking_state)
        logger.info("package_booking_finished", package_id=package_id, status=status,
                    confirmed=len(confirmed), total=len(results))
        totals: dict[str, float] = {}
        for r in confirmed:
            if r.get("currency"):
                totals[r["currency"]] = round(totals.get(r["currency"], 0) + (r.get("net") or 0), 2)
        return {"packageId": package_id, "status": status, "items": results, "rollback": rollback,
                "confirmedNetTotals": totals}

    # ── confirm helpers ──────────────────────────────────────────────
    async def _refresh_transfers_for_swaps(self, snapshot: dict, work: dict) -> None:
        swapped = {h["destinationIndex"]: h for h in work["hotels"] if h.get("swapped")}
        if not swapped or not work["transfers"]:
            return
        plan = Planner().build(FITRequest.model_validate(snapshot))
        stage = TransferStage(self.c, LocationResolver(self.c), plan)
        by_index = {dp.index: dp for dp in plan.destinations}
        new_list = []
        for t in work["transfers"]:
            h = swapped.get(t["destinationIndex"])
            if not h:
                new_list.append(t)
                continue
            dp = by_index[t["destinationIndex"]]
            leg = dp.arrival if t["direction"] == "ARRIVAL" else dp.departure
            res = await stage.research_leg(dp, leg, h["hotelCode"], h["hotelName"])
            if not res.get("selected"):
                raise Conflict(f"No {t['direction'].lower()} transfer available for the newly selected hotel "
                               f"{h['hotelName']}", code="TRANSFER_UNAVAILABLE_FOR_SWAPPED_HOTEL",
                               details={"itemId": t["itemId"], "result": res})
            s = res["selected"]
            new_list.append({**t, "rateKey": s["rateKey"], "transferType": s["transferType"], "from": res["from"],
                             "to": res["to"], "expectedAmount": s["price"]["net"], "currency": s["price"]["currency"]})
        work["transfers"] = new_list

    @staticmethod
    def _transport_codes(work: dict, req: ConfirmPackageRequest) -> dict:
        lookup = {(c.destinationIndex, c.direction): c for c in req.transportCodes}
        out, missing = {}, []
        for t in work["transfers"]:
            code = lookup.get((t["destinationIndex"], t["direction"]))
            if code is None:
                missing.append({"itemId": t["itemId"], "destinationIndex": t["destinationIndex"],
                                "direction": t["direction"], "travelMode": t.get("travelMode")})
            else:
                out[t["itemId"]] = code
        if missing:
            raise ValidationFailed("Flight/train/cruise number is mandatory for every Hotelbeds transfer",
                                   code="TRANSPORT_CODE_REQUIRED", details={"missing": missing})
        return out

    def _answers(self, work: dict, pre: dict, req: ConfirmPackageRequest, rec: dict) -> dict[str, list]:
        given: dict[tuple, str] = {(a.itemId, a.questionCode): a.answer for a in req.activityAnswers}
        hotel_by_dest = {h["destinationIndex"]: h["hotelName"] for h in work["hotels"]}
        holder = req.holder
        out, missing = {}, []
        for a in work["activities"]:
            p = pre.get(a["itemId"], {})
            answers = []
            for q in p.get("questions") or []:
                code = q.get("code")
                text = normalize(q.get("text"))
                ans = given.get((a["itemId"], code))
                if ans is None:
                    blob = f"{normalize(code)} {text}"
                    if "hotel" in blob and hotel_by_dest.get(a["destinationIndex"]):
                        ans = hotel_by_dest[a["destinationIndex"]]
                    elif "phone" in blob or "mobile" in blob or "telephone" in blob:
                        ans = holder.phone
                    elif "email" in blob or "mail" in blob:
                        ans = str(holder.email)
                    elif "name" in blob:
                        ans = f"{holder.firstName} {holder.lastName}"
                if ans is None:
                    if q.get("required"):
                        missing.append({"itemId": a["itemId"], "questionCode": code, "text": q.get("text")})
                    continue
                answers.append({"question": {"code": code, "text": q.get("text"), "required": q.get("required")},
                                "answer": ans})
            out[a["itemId"]] = answers
        if missing:
            raise ValidationFailed("Activity questions must be answered (activityAnswers)",
                                   code="ACTIVITY_ANSWERS_REQUIRED", details={"missing": missing})
        return out

    async def _book_hotel(self, package_id: str, h: dict, pre: dict, req: ConfirmPackageRequest,
                          adults: list, children: list) -> dict:
        adult_pool, child_pool = list(adults), list(children)
        lead = adults[0]
        rooms_payload = []
        for line in pre["rooms"]:
            paxes = []
            for room_id in range(1, line["rooms"] + 1):
                for _ in range(line["adults"]):
                    t = adult_pool.pop(0) if adult_pool else lead
                    paxes.append({"roomId": room_id, "type": "AD", "name": t.firstName, "surname": t.lastName})
                for ch in self._take_children(child_pool, line["childAges"][: line["children"]]):
                    paxes.append({"roomId": room_id, "type": "CH", "age": ch.age, "name": ch.firstName,
                                  "surname": ch.lastName})
            rooms_payload.append({"rateKey": line["rateKey"], "paxes": paxes})
        remark = " | ".join(x for x in (h.get("remark"), req.remark) if x)
        ref = self._client_ref(package_id, f"H{h['destinationIndex']}")
        payload = {"holder": {"name": req.holder.firstName, "surname": req.holder.lastName}, "rooms": rooms_payload,
                   "clientReference": ref, "tolerance": settings.BOOKING_PRICE_TOLERANCE_PCT,
                   "language": settings.HOTELBEDS_HOTEL_LANGUAGE}
        if remark:
            payload["remark"] = remark[:2000]
        base = {"itemId": h["itemId"], "type": "HOTEL", "clientReference": ref, "hotelName": h["hotelName"]}
        try:
            data = await self.c.hotels.booking_confirm(payload)
        except HotelbedsError as exc:
            if exc.is_timeout_or_network:
                found = await self._reconcile_hotel(ref)
                if found:
                    return {**base, **found, "reconciled": True}
                return {**base, "status": "UNKNOWN", "error": exc.message}
            return {**base, "status": "FAILED", "error": exc.message, "upstreamCode": exc.upstream_code}
        b = (data or {}).get("booking") or {}
        return {**base, "status": b.get("status") or "CONFIRMED", "reference": b.get("reference"),
                "net": money(b.get("totalNet")), "currency": b.get("currency"),
                "pendingAmount": b.get("pendingAmount"), "supplier": (b.get("hotel") or {}).get("supplier"),
                "invoiceCompany": b.get("invoiceCompany"),
                "modificationPolicies": b.get("modificationPolicies")}

    async def _reconcile_hotel(self, client_ref: str) -> Optional[dict]:
        today = date.today()
        try:
            data = await self.c.hotels.booking_list({"start": (today - timedelta(days=1)).isoformat(),
                                                     "end": (today + timedelta(days=1)).isoformat(),
                                                     "filterType": "CREATION", "clientReference": client_ref,
                                                     "from": 1, "to": 10})
        except HotelbedsError:
            return None
        lst = ((data or {}).get("bookings") or {})
        items = lst.get("bookings") if isinstance(lst, dict) else lst
        for b in items or []:
            if b.get("clientReference") == client_ref:
                return {"status": b.get("status") or "CONFIRMED", "reference": b.get("reference"),
                        "net": money(b.get("totalNet")), "currency": b.get("currency")}
        return None

    async def _book_activity(self, package_id: str, a: dict, pre: dict, req: ConfirmPackageRequest,
                             adults: list, children: list, answers: list) -> dict:
        pax_adults = [adults[i % len(adults)] for i in range(a["pax"]["adults"])]
        pax_children = self._take_children(list(children), a["pax"]["childAges"])
        paxes = [{"age": t.age or settings.FIT_ACTIVITY_DEFAULT_ADULT_AGE, "name": t.firstName, "surname": t.lastName,
                  "type": "ADULT"} for t in pax_adults] + \
                [{"age": c.age, "name": c.firstName, "surname": c.lastName, "type": "CHILD"} for c in pax_children]
        holder = req.holder
        country = to_iso2(holder.country) if holder.country else None
        h = {"name": holder.firstName, "surname": holder.lastName, "title": holder.title, "email": str(holder.email),
             "telephones": [holder.phone], "mailing": False}
        if country:
            h["country"] = country
        if holder.address:
            h["address"] = holder.address
        if holder.zipCode:
            h["zipCode"] = holder.zipCode
        activity = {"rateKey": pre["rateKey"], "from": a["from"], "to": a["to"], "paxes": paxes}
        if a.get("session") and any((s.get("code") == a["session"]) for s in pre.get("sessions") or []):
            activity["session"] = a["session"]
        elif pre.get("sessions"):
            activity["session"] = pre["sessions"][0].get("code")
        if a.get("language"):
            activity["preferedLanguage"] = a["language"]
            activity["serviceLanguage"] = a["language"]
        if answers:
            activity["answers"] = answers
        ref = self._client_ref(package_id, f"A{a['itemId'][-4:]}")
        payload = {"language": settings.HOTELBEDS_ACTIVITY_LANGUAGE, "clientReference": ref, "holder": h,
                   "activities": [activity]}
        base = {"itemId": a["itemId"], "type": "ACTIVITY", "clientReference": ref, "activityName": a["activityName"],
                "date": a["date"]}
        try:
            data = await self.c.activities.booking_confirm(payload)
        except HotelbedsError as exc:
            if exc.is_timeout_or_network:
                found = await self._reconcile_activity(ref, holder.lastName)
                if found:
                    return {**base, **found, "reconciled": True}
                return {**base, "status": "UNKNOWN", "error": exc.message}
            return {**base, "status": "FAILED", "error": exc.message, "upstreamCode": exc.upstream_code}
        b = (data or {}).get("booking") or {}
        acts = b.get("activities") or []
        return {**base, "status": b.get("status") or "CONFIRMED", "reference": b.get("reference"),
                "net": money(b.get("totalNet") or b.get("total")), "currency": b.get("currency"),
                "vouchers": [v for x in acts for v in (x.get("vouchers") or [])],
                "contractRemarks": [c.get("text") for x in acts for c in (x.get("comments") or [])
                                    if c.get("type") == "CONTRACT_REMARKS"],
                "supplier": (acts[0].get("supplier") if acts else None)}

    async def _reconcile_activity(self, client_ref: str, surname: str) -> Optional[dict]:
        today = date.today()
        try:
            data = await self.c.activities.booking_list(settings.HOTELBEDS_ACTIVITY_LANGUAGE, {
                "start": (today - timedelta(days=1)).isoformat(), "end": (today + timedelta(days=1)).isoformat(),
                "filterType": "CREATION", "includedCancelled": "false", "holder": surname, "itemsPerPage": 50})
        except HotelbedsError:
            return None
        for b in (data or {}).get("bookings") or []:
            if b.get("clientReference") == client_ref:
                return {"status": b.get("status") or "CONFIRMED", "reference": b.get("reference"),
                        "net": money(b.get("totalNet") or b.get("total")), "currency": b.get("currency")}
        return None

    async def _book_transfer(self, package_id: str, t: dict, req: ConfirmPackageRequest, code) -> dict:
        mode = normalize_mode(t.get("travelMode"))
        kind = "TRAIN" if mode == MODE_TRAIN else ("CRUISE" if mode == MODE_SEA else "FLIGHT")
        detail = {"type": kind, "direction": t["direction"], "code": code.code}
        if code.companyName:
            detail["companyName"] = code.companyName
        holder = req.holder
        remark = " | ".join(x for x in (t.get("luggageComment") and f"Luggage: {t['luggageComment']}", req.remark) if x)
        ref = self._client_ref(package_id, f"T{t['itemId'][-4:]}")
        payload = {"language": settings.HOTELBEDS_TRANSFER_LANGUAGE,
                   "holder": {"name": holder.firstName, "surname": holder.lastName, "email": str(holder.email),
                              "phone": holder.phone},
                   "transfers": [{"rateKey": t["rateKey"], "transferDetails": [detail]}],
                   "clientReference": ref,
                   "welcomeMessage": f"Welcome {holder.title or ''} {holder.lastName}".replace("  ", " ").strip()}
        if remark:
            payload["remark"] = remark[:2000]
        base = {"itemId": t["itemId"], "type": "TRANSFER", "clientReference": ref, "direction": t["direction"]}
        try:
            data = await self.c.transfers.booking_confirm(payload)
        except HotelbedsError as exc:
            if exc.is_timeout_or_network:
                return {**base, "status": "UNKNOWN", "error": exc.message}
            return {**base, "status": "FAILED", "error": exc.message, "upstreamCode": exc.upstream_code}
        b = ((data or {}).get("bookings") or [{}])[0]
        trs = b.get("transfers") or []
        return {**base, "status": b.get("status") or "CONFIRMED", "reference": b.get("reference"),
                "net": money(b.get("totalNetAmount") or b.get("totalAmount")), "currency": b.get("currency"),
                "pickup": (trs[0].get("pickupInformation") if trs else None),
                "vouchers": [v for x in trs for v in (x.get("vouchers") or [])],
                "serviceIds": [x.get("id") for x in trs]}

    async def _rollback(self, results: list[dict]) -> list[dict]:
        out = []
        for r in reversed(results):
            if r["status"] not in CONFIRMED_STATES or not r.get("reference"):
                continue
            try:
                await self._cancel_one(r, simulate=False)
                r["rolledBack"] = True
                out.append({"itemId": r["itemId"], "reference": r["reference"], "status": "CANCELLED"})
            except HotelbedsError as exc:
                out.append({"itemId": r["itemId"], "reference": r["reference"], "status": "ROLLBACK_FAILED",
                            "error": exc.message})
                logger.error("rollback_failed", item=r["itemId"], reference=r["reference"], error=exc.message)
        return out

    # ═════════════════════════════════════════════════════════════════
    # cancel / status
    # ═════════════════════════════════════════════════════════════════
    async def _cancel_one(self, r: dict, *, simulate: bool) -> dict:
        ref = r["reference"]
        if r["type"] == "HOTEL":
            data = await self.c.hotels.booking_cancel(ref, simulation=simulate,
                                                      language=settings.HOTELBEDS_HOTEL_LANGUAGE)
            b = (data or {}).get("booking") or {}
            return {"status": b.get("status"), "cancellationReference": b.get("cancellationReference"),
                    "fee": money((b.get("hotel") or {}).get("cancellationAmount")), "currency": b.get("currency")}
        if r["type"] == "ACTIVITY":
            data = await self.c.activities.booking_cancel(ref, settings.HOTELBEDS_ACTIVITY_LANGUAGE,
                                                          simulation=simulate)
            b = (data or {}).get("booking") or {}
            return {"status": b.get("status"), "fee": money(b.get("total") or b.get("totalNet")),
                    "currency": b.get("currency")}
        data = await self.c.transfers.booking_cancel(ref, settings.HOTELBEDS_TRANSFER_LANGUAGE, simulation=simulate)
        b = ((data or {}).get("bookings") or [{}])[0] if isinstance(data, dict) else {}
        return {"status": b.get("status"), "fee": money(b.get("totalAmount")), "currency": b.get("currency")}

    async def cancel(self, package_id: str, req: CancelPackageRequest) -> dict:
        rec = await self._load(package_id)
        booking = rec.get("booking") or {}
        items = [r for r in booking.get("items") or [] if r.get("reference") and r["status"] in CONFIRMED_STATES
                 and not r.get("rolledBack")]
        if req.itemIds:
            items = [r for r in items if r["itemId"] in req.itemIds]
        if not items:
            raise Conflict("No confirmed bookings to cancel for this package", code="NOTHING_TO_CANCEL")
        out = []
        for r in items:
            try:
                res = await self._cancel_one(r, simulate=req.simulate)
                out.append({"itemId": r["itemId"], "type": r["type"], "reference": r["reference"], **res,
                            "ok": True})
                if not req.simulate:
                    r["status"] = "CANCELLED"
                    r["cancelledAt"] = datetime.now(timezone.utc).isoformat()
            except HotelbedsError as exc:
                out.append({"itemId": r["itemId"], "type": r["type"], "reference": r["reference"], "ok": False,
                            "error": exc.message, "upstreamCode": exc.upstream_code})
        fees: dict[str, float] = {}
        for o in out:
            if o.get("ok") and o.get("currency"):
                fees[o["currency"]] = round(fees.get(o["currency"], 0) + (o.get("fee") or 0), 2)
        if not req.simulate:
            remaining = [r for r in booking.get("items") or [] if r["status"] in CONFIRMED_STATES
                         and not r.get("rolledBack")]
            status = "CANCELLED" if not remaining else "PARTIALLY_CANCELLED"
            booking.setdefault("cancellations", []).append({"at": datetime.now(timezone.utc).isoformat(),
                                                           "reason": req.reason, "results": out})
            await self.c.store.update_package(package_id, status=status, booking=booking)
        else:
            status = rec["status"]
        return {"packageId": package_id, "simulation": req.simulate, "status": status, "items": out,
                "feesByCurrency": fees}

    async def live_status(self, package_id: str) -> dict:
        rec = await self._load(package_id)
        booking = rec.get("booking")
        if not booking:
            return {"packageId": package_id, "status": rec["status"], "items": [],
                    "note": "Package has not been booked yet"}

        async def one(r: dict) -> dict:
            if not r.get("reference"):
                return {**r, "live": None}
            try:
                if r["type"] == "HOTEL":
                    d = await self.c.hotels.booking_detail(r["reference"], settings.HOTELBEDS_HOTEL_LANGUAGE)
                    b = (d or {}).get("booking") or {}
                    live = {"status": b.get("status"), "pendingAmount": b.get("pendingAmount"),
                            "totalNet": b.get("totalNet"), "currency": b.get("currency")}
                elif r["type"] == "ACTIVITY":
                    d = await self.c.activities.booking_detail(r["reference"], settings.HOTELBEDS_ACTIVITY_LANGUAGE)
                    b = (d or {}).get("booking") or {}
                    live = {"status": b.get("status"), "vouchers": [v for x in b.get("activities") or []
                                                                    for v in x.get("vouchers") or []]}
                else:
                    d = await self.c.transfers.booking_detail(r["reference"], settings.HOTELBEDS_TRANSFER_LANGUAGE)
                    b = ((d or {}).get("bookings") or [{}])[0]
                    live = {"status": b.get("status"), "vouchers": [v for x in b.get("transfers") or []
                                                                    for v in x.get("vouchers") or []]}
            except HotelbedsError as exc:
                live = {"error": exc.message}
            return {"itemId": r["itemId"], "type": r["type"], "reference": r["reference"], "stored": r["status"],
                    "live": live}

        items = await asyncio.gather(*(one(r) for r in booking.get("items") or []))
        return {"packageId": package_id, "status": rec["status"], "items": items}
