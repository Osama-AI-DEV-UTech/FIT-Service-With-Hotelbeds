"""
Stage 2 — ACTIVITIES (excursions).

One availability call per distinct pax mix per destination (normally
one), local matching of every requested excursion against the whole
result set, an optional targeted `text` search for anything unmatched,
and optional suggestions for free days.

No rateKey is stored as "final": Hotelbeds activity rateKeys must be
obtained from /activities/details right before booking (valid 30 min,
single use) — the booking service does that using the code / modality /
rate / date chosen here.
"""
import re
import time
import uuid
from datetime import date, timedelta
from typing import Any, Optional

import structlog

from app.config import settings
from app.container import Container
from app.core.exceptions import HotelbedsError, ProviderNotConfigured
from app.fit.planner import DestinationPlan, TripPlan
from app.fit.pricing import price_line
from app.fit.resolvers import LocationResolver
from app.fit.scoring import (ACTIVITY_TYPE_KEYWORDS, DAYPART_KEYWORDS, STRENUOUS, money, style_price_score)
from app.utils.text import clean, normalize, similarity, token_overlap

logger = structlog.get_logger("fit.activities")

_TAG = re.compile(r"<[^>]+>")


def strip_html(text: Optional[str], limit: int = 600) -> Optional[str]:
    if not text:
        return None
    plain = re.sub(r"\s+", " ", _TAG.sub(" ", text)).strip()
    return plain if len(plain) <= limit else plain[: limit - 1].rsplit(" ", 1)[0] + "…"


def duration_hours(duration: Optional[dict]) -> Optional[float]:
    if not duration:
        return None
    try:
        value = float(duration.get("value"))
    except (TypeError, ValueError):
        return None
    metric = str(duration.get("metric") or "").upper()
    if metric.startswith("MIN"):
        return value / 60
    if metric.startswith("DAY"):
        return value * 24
    return value


def _images(activity: dict) -> list[str]:
    content = activity.get("content") or {}
    out = []
    for img in ((content.get("media") or {}).get("images") or [])[:6]:
        urls = img.get("urls") or []
        best = None
        for u in urls:
            w = u.get("width") or 0
            if u.get("resource") and (best is None or (w <= 1280 and w > (best.get("width") or 0))):
                best = u
        if best:
            out.append(best["resource"])
    return out


def _text_blob(activity: dict, modality: dict) -> str:
    content = activity.get("content") or {}
    segs = " ".join(s.get("name", "") for g in content.get("segmentationGroups") or [] for s in g.get("segments") or [])
    parts = [activity.get("name"), modality.get("name"), content.get("name"), strip_html(content.get("description"), 2000),
             content.get("summary"), " ".join(content.get("highligths") or []) if isinstance(content.get("highligths"), list)
             else None, segs, activity.get("type")]
    return " ".join(p for p in parts if p)


def _languages(rd: dict) -> list[str]:
    out = []
    for lang in rd.get("languages") or []:
        code = lang.get("code") if isinstance(lang, dict) else lang
        if code:
            out.append(str(code).lower())
    return out


def _operates_on(rd: dict, day: date) -> Optional[dict]:
    for op in rd.get("operationDates") or []:
        try:
            f, t = date.fromisoformat(str(op.get("from"))[:10]), date.fromisoformat(str(op.get("to"))[:10])
        except ValueError:
            continue
        if f <= day <= t:
            return op
    return None


class ActivityStage:
    def __init__(self, c: Container, resolver: LocationResolver, plan: TripPlan):
        self.c = c
        self.resolver = resolver
        self.plan = plan
        self.today = date.today()

    async def run(self, dp: DestinationPlan, hotel_result: Optional[dict]) -> dict:
        started = time.perf_counter()
        result: dict[str, Any] = {"status": "NOT_REQUESTED", "destination": None, "items": [], "suggested": []}
        wants_suggestions = settings.FIT_ENABLE_SUGGESTED_ACTIVITIES and settings.FIT_MAX_SUGGESTED_ACTIVITIES_PER_DESTINATION > 0
        if not dp.excursions and not wants_suggestions:
            return result
        try:
            await self._run(dp, hotel_result, result)
        except ProviderNotConfigured as exc:
            result["status"] = "PROVIDER_NOT_CONFIGURED"
            if dp.excursions:
                self.plan.add("CRITICAL", "ACTIVITY", f"{dp.name}: {exc.message}", dp.index, True)
        except HotelbedsError as exc:
            result["status"] = "SUPPLIER_ERROR"
            result["error"] = {"code": exc.upstream_code, "message": exc.message, "status": exc.upstream_status}
            sev = "CRITICAL" if dp.excursions else "INFO"
            self.plan.add(sev, "ACTIVITY", f"{dp.name}: Hotelbeds activity search failed — {exc.message}",
                          dp.index, bool(dp.excursions))
        result["durationMs"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    async def _run(self, dp: DestinationPlan, hotel_result: Optional[dict], result: dict) -> None:
        scope = await self._scope(dp, hotel_result)
        if scope is None:
            result["status"] = "LOCATION_NOT_RESOLVED"
            if dp.excursions:
                self.plan.add("CRITICAL", "ACTIVITY", f"{dp.name}: could not resolve an Activities destination.",
                              dp.index, True)
            return
        result["destination"] = scope["info"]

        window_from = max(dp.check_in, self.today + timedelta(days=1))
        window_to = min(dp.check_out, window_from + timedelta(days=60))
        if window_to < window_from:
            result["status"] = "DATES_NOT_BOOKABLE"
            return

        cache: dict[tuple, list[dict]] = {}
        used_codes: set[str] = set()
        busy_days: set[date] = set()

        for ex in dp.excursions:
            adults, ages = self._pax(ex)
            offers = await self._offers(scope, window_from, window_to, adults, ages, cache)
            item = self._match(ex, offers, dp, used_codes, busy_days, adults, ages)
            if item is None and settings.FIT_ACTIVITY_TARGETED_SEARCH and clean(ex.excursion):
                targeted = await self._offers(scope, window_from, window_to, adults, ages, cache,
                                              text=clean(ex.excursion))
                item = self._match(ex, targeted, dp, used_codes, busy_days, adults, ages)
            if item is None:
                label = clean(ex.excursion) or clean(ex.excursionType)
                result["items"].append({"itemId": f"ACT-{uuid.uuid4().hex[:8]}", "status": "NOT_AVAILABLE",
                                        "requested": self._requested(ex), "reason": "No matching Hotelbeds activity"
                                        " available on the requested date"})
                self.plan.add("WARNING", "ACTIVITY", f"{dp.name}: no Hotelbeds activity matched '{label}'"
                              f"{' on ' + str(ex.excursionDate) if ex.excursionDate else ''} — source manually.",
                              dp.index, True)
                continue
            used_codes.add(item["activity"]["code"])
            busy_days.add(date.fromisoformat(item["date"]))
            result["items"].append(item)

        if settings.FIT_ENABLE_SUGGESTED_ACTIVITIES:
            offers = await self._offers(scope, window_from, window_to, self.plan.adults, self.plan.child_ages, cache)
            result["suggested"] = self._suggest(dp, offers, used_codes, busy_days)

        matched = [i for i in result["items"] if i["status"] == "MATCHED"]
        if dp.excursions:
            result["status"] = "MATCHED" if len(matched) == len(dp.excursions) else ("PARTIAL" if matched else "NONE_MATCHED")
        else:
            result["status"] = "SUGGESTIONS_ONLY" if result["suggested"] else "NOT_REQUESTED"

    # ── scope / pax ──────────────────────────────────────────────────
    async def _scope(self, dp: DestinationPlan, hotel_result: Optional[dict]) -> Optional[dict]:
        dest = await self.resolver.resolve_activity_destination(dp.name, dp.countries)
        if dest:
            return {"filter": {"type": "destination", "value": dest["code"]}, "info": dest}
        hdest = (hotel_result or {}).get("destination") or {}
        if hdest.get("code"):
            return {"filter": {"type": "destination", "value": hdest["code"]},
                    "info": {"code": hdest["code"], "name": hdest.get("name"), "source": "hotel_destination_code"}}
        sel = ((hotel_result or {}).get("selected") or {}).get("hotel") or {}
        if sel.get("latitude") and sel.get("longitude"):
            return {"filter": {"type": "gps", "latitude": sel["latitude"], "longitude": sel["longitude"]},
                    "info": {"gps": [sel["latitude"], sel["longitude"]], "source": "hotel_coordinates"}}
        return None

    def _pax(self, ex) -> tuple[int, list[int]]:
        adults = ex.adultCount if ex.adultCount else self.plan.adults
        kids = ex.childCount if ex.childCount is not None else len(self.plan.child_ages)
        ages = list(self.plan.child_ages[:kids])
        if kids > len(ages):
            ages += [8] * (kids - len(ages))
        return max(adults, 1), ages

    def _paxes(self, adults: int, ages: list[int]) -> list[dict]:
        return [{"age": settings.FIT_ACTIVITY_DEFAULT_ADULT_AGE} for _ in range(adults)] + [{"age": a} for a in ages]

    # ── availability ─────────────────────────────────────────────────
    async def _offers(self, scope: dict, d_from: date, d_to: date, adults: int, ages: list[int],
                      cache: dict, text: Optional[str] = None) -> list[dict]:
        key = (adults, tuple(ages), text)
        if key in cache:
            return cache[key]
        items = [dict(scope["filter"])]
        if text:
            items.append({"type": "text", "value": text[:60]})
        offers: list[dict] = []
        for page in range(1, settings.FIT_ACTIVITY_MAX_PAGES + 1):
            payload = {"filters": [{"searchFilterItems": items}], "from": d_from.isoformat(), "to": d_to.isoformat(),
                       "language": settings.HOTELBEDS_ACTIVITY_LANGUAGE, "paxes": self._paxes(adults, ages),
                       "pagination": {"itemsPerPage": 100, "page": page}, "order": "DEFAULT"}
            try:
                data = await self.c.activities.availability(payload)
            except HotelbedsError as exc:
                if text and exc.upstream_status and exc.upstream_status < 500:
                    break  # targeted search is best-effort
                raise
            activities = (data or {}).get("activities") or []
            for a in activities:
                offers.extend(self._flatten(a))
            total = ((data or {}).get("pagination") or {}).get("totalItems") or 0
            if page * 100 >= total or not activities:
                break
        cache[key] = offers
        return offers

    @staticmethod
    def _flatten(a: dict) -> list[dict]:
        out = []
        for m in a.get("modalities") or []:
            for r in m.get("rates") or []:
                for rd in r.get("rateDetails") or []:
                    out.append({"activity": a, "modality": m, "rate": r, "rd": rd,
                                "blob": _text_blob(a, m)})
        return out

    # ── matching ─────────────────────────────────────────────────────
    def _match(self, ex, offers: list[dict], dp: DestinationPlan, used: set, busy: set, adults: int,
               ages: list[int]) -> Optional[dict]:
        if not offers:
            return None
        text = clean(ex.excursion)
        ex_type = normalize(ex.excursionType).upper().replace(" ", "_")
        scored = []
        prices = [money((o["rd"].get("totalAmount") or {}).get("amount")) for o in offers]
        for o in offers:
            code = o["activity"].get("code")
            if code in used:
                continue
            day = self._pick_day(o["rd"], dp, ex.excursionDate, busy)
            if day is None:
                continue
            reasons: dict[str, float] = {}
            if text:
                overlap = token_overlap(text, o["blob"])
                sim = max(similarity(text, o["activity"].get("name")), similarity(text, o["modality"].get("name")))
                if overlap < 0.34 and sim < 0.6:
                    continue
                reasons["text"] = round(overlap * 45 + sim * 20, 2)
            type_kw = ACTIVITY_TYPE_KEYWORDS.get(ex_type) or ACTIVITY_TYPE_KEYWORDS.get(ex_type.split("_")[0])
            if type_kw:
                blob = normalize(o["blob"])
                if any(k in blob for k in type_kw):
                    reasons["type"] = 15
                elif not text:
                    continue
            elif not text:
                continue
            reasons.update(self._common_reasons(o, ex.dayPart))
            price = money((o["rd"].get("totalAmount") or {}).get("amount"))
            reasons["price"] = round(style_price_score(price, prices, self.plan.planning_style, weight=10), 2)
            if ex.excursionDate and day == ex.excursionDate:
                reasons["date"] = 10
            score = round(sum(reasons.values()), 2)
            if score >= settings.FIT_ACTIVITY_MIN_MATCH_SCORE:
                scored.append((score, o, day, reasons))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, o, day, reasons = scored[0]
        item = self._item(o, day, adults, ages, "MATCHED", best_score, reasons, ex.dayPart)
        item["requested"] = self._requested(ex)
        if ex.excursionDate and day != ex.excursionDate:
            item["dateChanged"] = True
            self.plan.add("WARNING", "ACTIVITY", f"{dp.name}: '{item['activity']['name']}' not available on "
                          f"{ex.excursionDate} — scheduled on {day}.", dp.index, True)
        seen, alts = {o["activity"].get("code")}, []
        for s, ao, aday, _ in scored[1:]:
            c = ao["activity"].get("code")
            if c in seen:
                continue
            seen.add(c)
            alts.append({"activityCode": c, "name": ao["activity"].get("name"),
                         "modalityCode": ao["modality"].get("code"), "modalityName": ao["modality"].get("name"),
                         "date": aday.isoformat(), "score": s,
                         "price": {"amount": money((ao["rd"].get("totalAmount") or {}).get("amount")),
                                   "currency": ao["activity"].get("currency") or ao["activity"].get("currencyName")}})
            if len(alts) >= settings.FIT_ACTIVITY_ALTERNATIVES:
                break
        item["alternatives"] = alts
        if clean(ex.comment):
            item["comment"] = clean(ex.comment)
        return item

    def _common_reasons(self, o: dict, day_part: Optional[str]) -> dict:
        reasons: dict[str, float] = {}
        blob = normalize(o["blob"])
        dpart = normalize(day_part).upper().replace(" ", "_")
        if dpart:
            hours = duration_hours(o["modality"].get("duration"))
            sessions = " ".join(normalize(s.get("name") or s.get("code")) for s in o["rd"].get("sessions") or [])
            if dpart in ("FULL_DAY", "FULLDAY", "FULL"):
                if hours and hours >= 6:
                    reasons["dayPart"] = 8
            elif any(k in sessions or f" {k} " in f" {blob} " for k in DAYPART_KEYWORDS.get(dpart, ())):
                reasons["dayPart"] = 8
        if self.plan.guide_language:
            langs = _languages(o["rd"])
            if langs and self.plan.guide_language in langs:
                reasons["language"] = 6
        crowd = normalize(self.plan.request.comfortSafety.comfortWtCrowd)
        if crowd in ("low", "none", "avoid"):
            if "private" in blob:
                reasons["private"] = 6
            elif "shared" in blob or "group" in blob:
                reasons["shared"] = -3
        if self.plan.request.travelerInfo.isImmobile and any(k in blob for k in STRENUOUS):
            reasons["accessibility"] = -25
        if self.plan.child_ages and any(k in blob for k in ACTIVITY_TYPE_KEYWORDS["FAMILY"]):
            reasons["family"] = 4
        return reasons

    def _pick_day(self, rd: dict, dp: DestinationPlan, wanted: Optional[date], busy: set) -> Optional[date]:
        if wanted and _operates_on(rd, wanted):
            return wanted
        days = dp.stay_days
        # Prefer full days in the destination (not arrival / departure day), nearest to the wanted date.
        inner = days[1:-1] or days
        if wanted:
            inner = sorted(inner, key=lambda d: abs((d - wanted).days))
        for d in inner:
            if d not in busy and d >= self.today + timedelta(days=1) and _operates_on(rd, d):
                return d
        return None

    def _item(self, o: dict, day: date, adults: int, ages: list[int], status: str, score: float,
              reasons: dict, day_part: Optional[str]) -> dict:
        a, m, r, rd = o["activity"], o["modality"], o["rate"], o["rd"]
        op = _operates_on(rd, day) or {}
        hours = duration_hours(m.get("duration"))
        to_day = day
        if m.get("duration") and str((m["duration"] or {}).get("metric", "")).upper().startswith("DAY"):
            try:
                to_day = day + timedelta(days=max(int(float(m["duration"]["value"])) - 1, 0))
            except (TypeError, ValueError):
                pass
        sessions = rd.get("sessions") or []
        session = None
        if sessions:
            dpart = normalize(day_part).upper()
            session = next((s for s in sessions if any(k in normalize(s.get("name") or s.get("code"))
                                                       for k in DAYPART_KEYWORDS.get(dpart, ()))), sessions[0])
        langs = _languages(rd)
        language = None
        if langs:
            language = self.plan.guide_language if self.plan.guide_language in langs else ("en" if "en" in langs else langs[0])
        currency = a.get("currency") or a.get("currencyName")
        amount = money((rd.get("totalAmount") or {}).get("amount"))
        content = a.get("content") or {}
        return {
            "itemId": f"ACT-{uuid.uuid4().hex[:8]}",
            "status": status,
            "date": day.isoformat(), "from": day.isoformat(), "to": to_day.isoformat(),
            "activity": {"code": a.get("code"), "name": a.get("name"), "type": a.get("type"),
                         "description": strip_html(content.get("description")), "images": _images(a),
                         "durationHours": round(hours, 1) if hours else None},
            "modality": {"code": m.get("code"), "name": m.get("name"), "duration": m.get("duration"),
                         "amountUnitType": m.get("amountUnitType")},
            "rate": {"rateCode": r.get("rateCode"), "rateClass": r.get("rateClass"),
                     "freeCancellation": r.get("freeCancellation")},
            "session": {"code": session.get("code"), "name": session.get("name")} if session else None,
            "language": language, "languagesOffered": langs,
            "pax": {"adults": adults, "childAges": ages},
            "price": price_line("ACTIVITY", amount, currency),
            "cancellationPolicies": [{"dateFrom": p.get("dateFrom"), "amount": money(p.get("amount"))}
                                     for p in op.get("cancellationPolicies") or []],
            "questions": m.get("questions") or [],
            "matchScore": score, "matchReasons": reasons,
        }

    @staticmethod
    def _requested(ex) -> dict:
        return {"excursion": clean(ex.excursion), "excursionType": clean(ex.excursionType),
                "excursionDate": ex.excursionDate.isoformat() if ex.excursionDate else None,
                "dayPart": clean(ex.dayPart), "adultCount": ex.adultCount, "childCount": ex.childCount}

    # ── suggestions for free days ────────────────────────────────────
    def _suggest(self, dp: DestinationPlan, offers: list[dict], used: set, busy: set) -> list[dict]:
        limit = settings.FIT_MAX_SUGGESTED_ACTIVITIES_PER_DESTINATION
        free_days = [d for d in dp.stay_days[1:-1] if d not in busy and d >= self.today + timedelta(days=1)]
        if not free_days or not offers or limit <= 0:
            return []
        prefs = {normalize(x).upper() for x in self.plan.request.budget.splurgeOn}
        ttype = normalize(self.plan.request.travelerInfo.travelerType).upper()
        prices = [money((o["rd"].get("totalAmount") or {}).get("amount")) for o in offers]
        ranked = []
        seen = set(used)
        for o in offers:
            code = o["activity"].get("code")
            if code in seen:
                continue
            reasons = self._common_reasons(o, None)
            blob = normalize(o["blob"])
            if any(k in blob for k in ACTIVITY_TYPE_KEYWORDS["SIGHTSEEING"]):
                reasons["sightseeing"] = 8
            if ttype == "FAMILY" and any(k in blob for k in ACTIVITY_TYPE_KEYWORDS["FAMILY"]):
                reasons["family"] = 8
            if any("ACTIV" in p or "EXCURSION" in p or "EXPERIENCE" in p for p in prefs):
                reasons["splurge"] = 4
            price = money((o["rd"].get("totalAmount") or {}).get("amount"))
            reasons["price"] = round(style_price_score(price, prices, self.plan.planning_style, weight=10), 2)
            ranked.append((round(sum(reasons.values()), 2), o, reasons))
        ranked.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        taken_days: set[date] = set(busy)
        adults, ages = self.plan.adults, self.plan.child_ages
        for score, o, reasons in ranked:
            code = o["activity"].get("code")
            if code in seen:
                continue
            day = next((d for d in free_days if d not in taken_days and _operates_on(o["rd"], d)), None)
            if day is None:
                continue
            seen.add(code)
            taken_days.add(day)
            item = self._item(o, day, adults, ages, "SUGGESTED", score, reasons, None)
            item["isSuggested"] = True
            out.append(item)
            if len(out) >= limit:
                break
        return out
