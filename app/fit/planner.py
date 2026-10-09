"""
Turns the raw FIT form into a TripPlan: per-destination stay dates,
room occupancy groups, arrival/departure terminal legs, pax breakdowns
and Hotelbeds defaults (sourceMarket, currency, languages).

Everything the request did not specify but Hotelbeds needs is recorded
in `fields_added`, and everything inconsistent becomes an advisory —
nothing is silently guessed.
"""
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from app.config import settings
from app.schemas.fit import Accommodation, Destination, ExcursionRequest, FITRequest, TransportDetails
from app.fit.resolvers import MODE_FLIGHT, MODE_ROAD, normalize_mode
from app.utils.countries import to_iso2
from app.utils.text import clean, normalize

_LANG_ALIASES = {
    "english": "en", "arabic": "ar", "urdu": "ur", "hindi": "hi", "french": "fr", "spanish": "es",
    "german": "de", "italian": "it", "turkish": "tr", "russian": "ru", "chinese": "zh", "mandarin": "zh",
    "japanese": "ja", "portuguese": "pt", "korean": "ko", "dutch": "nl", "persian": "fa", "farsi": "fa",
    "malay": "ms", "indonesian": "id", "thai": "th", "greek": "el", "polish": "pl",
}


def language_code(value: Optional[str]) -> Optional[str]:
    v = normalize(value)
    if not v:
        return None
    if len(v) == 2:
        return v
    if v in _LANG_ALIASES:
        return _LANG_ALIASES[v]
    try:
        import pycountry
        rec = pycountry.languages.lookup(v)
        return getattr(rec, "alpha_2", None)
    except Exception:
        return None


@dataclass
class Advisory:
    severity: str
    category: str
    message: str
    destinationIndex: Optional[int] = None
    actionRequired: bool = False

    def dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class OccupancyGroup:
    rooms: int
    adults: int            # per room
    children: int          # per room
    child_ages: list[int]  # per room

    def hotelbeds(self) -> dict:
        occ = {"rooms": self.rooms, "adults": self.adults, "children": self.children}
        if self.children:
            occ["paxes"] = [{"type": "CH", "age": a} for a in self.child_ages]
        return occ


@dataclass
class TerminalLeg:
    direction: str                     # ARRIVAL | DEPARTURE
    required: bool
    mode: Optional[str]                # FLIGHT | TRAIN | SEA | ROAD | None
    terminal_text: Optional[str]
    city: Optional[str]
    when: Optional[datetime]
    when_assumed: bool = False
    type_pref: Optional[str] = None    # PRIVATE | SHARED | LUXURY


@dataclass
class DestinationPlan:
    index: int
    name: str
    countries: list[str]
    check_in: date
    check_out: date
    nights: int
    accommodation: Optional[Accommodation]
    occupancies: list[OccupancyGroup]
    excursions: list[ExcursionRequest]
    arrival: TerminalLeg
    departure: TerminalLeg
    transport: TransportDetails
    source: Destination

    @property
    def stay_days(self) -> list[date]:
        return [self.check_in + timedelta(days=i) for i in range(self.nights + 1)]


@dataclass
class TripPlan:
    request: FITRequest
    adults: int
    child_ages: list[int]
    source_market: str
    currency: str
    budget_total: Optional[float]
    planning_style: str
    guide_language: Optional[str]
    destinations: list[DestinationPlan]
    advisories: list[Advisory] = field(default_factory=list)
    fields_added: list[dict] = field(default_factory=list)

    @property
    def transfer_pax(self) -> tuple[int, int, int]:
        infants = sum(1 for a in self.child_ages if a <= 2)
        return self.adults, len(self.child_ages) - infants, infants

    @property
    def total_nights(self) -> int:
        return sum(d.nights for d in self.destinations)

    def add(self, severity: str, category: str, message: str, dest: Optional[int] = None,
            action: bool = False) -> None:
        if any(a.message == message and a.destinationIndex == dest for a in self.advisories):
            return
        self.advisories.append(Advisory(severity, category, message, dest, action))


def build_occupancies(adults: int, child_ages: list[int], rooms: Optional[int]) -> list[OccupancyGroup]:
    """Spread the party over rooms. Rooms with children are kept as single
    occupancy entries (each carries its own child ages); identical
    adult-only rooms are grouped (rooms=N)."""
    total = adults + len(child_ages)
    if not rooms:
        # Default: max 2 adults per room, max 4 persons per room; a 2+2 family shares one room.
        rooms = max(math.ceil(adults / 2), math.ceil(total / 4), 1)
    rooms = max(1, min(rooms, adults))  # every room needs at least one adult
    per_room_adults = [adults // rooms + (1 if i < adults % rooms else 0) for i in range(rooms)]
    per_room_children: list[list[int]] = [[] for _ in range(rooms)]
    # Give children to rooms with the fewest people first.
    for age in sorted(child_ages, reverse=True):
        idx = min(range(rooms), key=lambda i: per_room_adults[i] + len(per_room_children[i]))
        per_room_children[idx].append(age)

    # Identical rooms are merged into one occupancy (rooms=N) — Hotelbeds then
    # returns ONE rate line for all N rooms, which is booked with roomId 1..N.
    counts: dict[tuple[int, tuple[int, ...]], int] = {}
    for a, kids in zip(per_room_adults, per_room_children):
        key = (a, tuple(sorted(kids)))
        counts[key] = counts.get(key, 0) + 1
    return [OccupancyGroup(rooms=n, adults=a, children=len(ages), child_ages=list(ages))
            for (a, ages), n in sorted(counts.items(), key=lambda kv: (-kv[0][0], kv[0][1]))]


def _transfer_type(value: Optional[str]) -> Optional[str]:
    v = normalize(value)
    if not v:
        return None
    if any(k in v for k in ("lux", "vip", "premium", "limo")):
        return "LUXURY"
    if "shar" in v or "shuttle" in v or "group" in v:
        return "SHARED"
    if "priv" in v:
        return "PRIVATE"
    return None


def _style(value: Optional[str]) -> str:
    v = normalize(value).replace(" ", "_")
    if "ultra" in v:
        return "ULTRA_LUXURY"
    if "lux" in v or "premium" in v:
        return "LUXURY"
    if "budget" in v or "economy" in v or "cheap" in v:
        return "BUDGET"
    return "MID_RANGE"


class Planner:
    def __init__(self, today: Optional[date] = None):
        self.today = today or date.today()

    def build(self, req: FITRequest) -> TripPlan:
        trip, trav = req.tripInfo, req.travelerInfo
        fields_added: list[dict] = []

        market = to_iso2(trav.nationality)
        if not market:
            market = settings.DEFAULT_SOURCE_MARKET
            fields_added.append({"field": "travelerInfo.nationality", "usedValue": market,
                                 "reason": "Hotelbeds sourceMarket — prices are valid only for this market"})
        currency = (clean(req.budget.currency) or settings.DEFAULT_CURRENCY).upper()
        if not clean(req.budget.currency) and req.budget.range:
            fields_added.append({"field": "budget.currency", "usedValue": currency,
                                 "reason": "Budget compared against Hotelbeds prices in this currency"})
        guide_lang = language_code(req.foodGuideEtc.guideLanguage)

        plan = TripPlan(request=req, adults=trav.adults, child_ages=trav.child_age_ints, source_market=market,
                        currency=currency, budget_total=req.budget.range or None,
                        planning_style=_style(req.budget.planningStyle), guide_language=guide_lang,
                        destinations=[], fields_added=fields_added)

        trip_countries = [c for c in trip.tripCountries if clean(c)]
        dests = req.destinations
        prev_checkout: Optional[date] = None
        for i, d in enumerate(dests):
            seg = d.travelSegment
            tp = d.transportDetails or TransportDetails()
            arrival_dt = tp.arprtOrStatnTrsfrDt or (seg.travelDate if seg else None) or \
                (trip.startDestinationDate if i == 0 else None)
            if arrival_dt is not None:
                check_in = arrival_dt.date()
            elif prev_checkout is not None:
                check_in = prev_checkout
            else:
                check_in = trip.startDestinationDate.date()

            acc = d.accommodation
            nights = acc.numberNights if acc and acc.numberNights else None
            if nights is None:
                nxt = self._next_arrival(dests, i)
                end = nxt.date() if nxt else trip.endDestinationDate.date()
                nights = max((end - check_in).days, 1)
                fields_added.append({"field": f"destinations[{i}].accommodation.numberNights", "usedValue": str(nights),
                                     "reason": "Derived from the next destination / trip end date"})
            check_out = check_in + timedelta(days=nights)

            rooms = acc.rooms if acc else None
            if acc is not None and not rooms:
                fields_added.append({"field": f"destinations[{i}].accommodation.rooms",
                                     "usedValue": "auto", "reason": "Rooms auto-calculated (max 2 adults/room)"})
            occupancies = build_occupancies(trav.adults, plan.child_ages, rooms)

            countries = list(trip_countries)
            arrival = self._arrival_leg(req, i, d, tp, arrival_dt)
            departure = self._departure_leg(req, i, d, tp, check_out)

            excursions = [e for e in d.excursions if clean(e.excursion) or clean(e.excursionType)]
            dp = DestinationPlan(index=d.destinationIndex if d.destinationIndex is not None else i, name=d.destinationName,
                                 countries=countries, check_in=check_in, check_out=check_out, nights=nights,
                                 accommodation=acc, occupancies=occupancies, excursions=excursions, arrival=arrival,
                                 departure=departure, transport=tp, source=d)
            plan.destinations.append(dp)
            self._validate_destination(plan, dp, arrival_dt, prev_checkout)
            prev_checkout = check_out

        self._validate_trip(plan)
        return plan

    # ── legs ─────────────────────────────────────────────────────────
    @staticmethod
    def _next_arrival(dests: list[Destination], i: int) -> Optional[datetime]:
        if i + 1 < len(dests):
            nd = dests[i + 1]
            ntp = nd.transportDetails
            return (ntp.arprtOrStatnTrsfrDt if ntp else None) or (nd.travelSegment.travelDate if nd.travelSegment else None)
        return None

    def _arrival_leg(self, req: FITRequest, i: int, d: Destination, tp: TransportDetails,
                     arrival_dt: Optional[datetime]) -> TerminalLeg:
        seg = d.travelSegment
        mode = normalize_mode(seg.modeOfTravel if seg else None)
        text = clean(seg.arrivalTerminalCode) if seg else None
        if not text and i == 0:
            text = clean(req.tripInfo.startDestinationLocation)
        if mode is None and i == 0 and text:
            mode = MODE_FLIGHT
        return TerminalLeg(direction="ARRIVAL", required=tp.arprtOrStatnTrsfrReqd, mode=mode, terminal_text=text,
                           city=d.destinationName, when=tp.arprtOrStatnTrsfrDt or arrival_dt,
                           when_assumed=tp.arprtOrStatnTrsfrDt is None,
                           type_pref=_transfer_type(tp.trsfrType))

    def _departure_leg(self, req: FITRequest, i: int, d: Destination, tp: TransportDetails,
                       check_out: date) -> TerminalLeg:
        dests = req.destinations
        if i + 1 < len(dests):
            nseg = dests[i + 1].travelSegment
            mode = normalize_mode(nseg.modeOfTravel if nseg else None)
            text = None  # departure terminal is in THIS city — resolved from the city + mode
        else:
            text = clean(req.tripInfo.endDestinationLocation)
            mode = MODE_FLIGHT if text else None
        when = tp.hotelTrsfrDt
        assumed = False
        if when is None:
            if i + 1 == len(dests):
                when = req.tripInfo.endDestinationDate
            else:
                when = datetime.combine(check_out, datetime.min.time()).replace(hour=12)
                assumed = True
        return TerminalLeg(direction="DEPARTURE", required=tp.hotelTrsfrReqd, mode=mode, terminal_text=text,
                           city=d.destinationName, when=when, when_assumed=assumed,
                           type_pref=_transfer_type(tp.hotelTrsfrType) or _transfer_type(tp.trsfrType))

    # ── validation ───────────────────────────────────────────────────
    def _validate_destination(self, plan: TripPlan, dp: DestinationPlan, arrival_dt: Optional[datetime],
                              prev_checkout: Optional[date]) -> None:
        if dp.check_in < self.today:
            plan.add("CRITICAL", "DATES", f"{dp.name}: check-in {dp.check_in} is in the past — Hotelbeds cannot "
                     "price past dates.", dp.index, True)
        if prev_checkout and dp.check_in > prev_checkout:
            gap = (dp.check_in - prev_checkout).days
            plan.add("WARNING", "DATES", f"{gap} night(s) without accommodation before arriving in {dp.name} "
                     f"({prev_checkout} → {dp.check_in}).", dp.index, True)
        if prev_checkout and dp.check_in < prev_checkout:
            plan.add("WARNING", "DATES", f"{dp.name} check-in {dp.check_in} overlaps the previous stay "
                     f"(check-out {prev_checkout}).", dp.index, True)
        if arrival_dt and arrival_dt.hour < 6 and dp.accommodation:
            plan.add("INFO", "DATES", f"Early-morning arrival in {dp.name} ({arrival_dt:%H:%M}) — consider booking "
                     "the previous night for immediate check-in.", dp.index)
        if dp.arrival.required:
            if dp.arrival.mode == MODE_ROAD:
                plan.add("WARNING", "TRANSFER", f"{dp.name}: arrival is by road — Hotelbeds only sells airport, "
                         "port and station transfers; arrange the intercity drive manually.", dp.index, True)
            elif dp.transport.arprtOrStatnTrsfrDt is None:
                plan.add("WARNING", "TRANSFER", f"{dp.name}: arrival transfer requested without "
                         "arprtOrStatnTrsfrDt — using the travel date/time.", dp.index)
        if dp.departure.required:
            if dp.departure.mode == MODE_ROAD:
                plan.add("WARNING", "TRANSFER", f"{dp.name}: onward travel is by road — hotel-to-hotel transfers "
                         "are not available in Hotelbeds; arrange manually.", dp.index, True)
            if dp.departure.when_assumed:
                plan.add("WARNING", "TRANSFER", f"{dp.name}: departure transfer requested without hotelTrsfrDt — "
                         "assumed 12:00 on check-out day; confirm the real flight/train time.", dp.index, True)
        tp = dp.transport
        if tp.withinCityTransportReqd and tp.withinCityTransports:
            kinds = ", ".join(sorted({clean(w.withinCityTransportType) or "UNSPECIFIED" for w in tp.withinCityTransports}))
            plan.add("WARNING", "TRANSFER", f"{dp.name}: within-city transport ({kinds}) is not a Hotelbeds product "
                     "— arrange with a local supplier.", dp.index, True)
        if dp.source.travelSegment and dp.source.travelSegment.needStorage:
            plan.add("INFO", "SERVICE", f"{dp.name}: luggage storage requested "
                     f"({clean(dp.source.travelSegment.storageComment) or 'no details'}) — arrange manually.",
                     dp.index, True)
        for ex in dp.excursions:
            if ex.excursionDate and not (dp.check_in <= ex.excursionDate <= dp.check_out):
                plan.add("WARNING", "ACTIVITY", f"{dp.name}: excursion '{clean(ex.excursion) or ex.excursionType}' "
                         f"date {ex.excursionDate} is outside the stay ({dp.check_in} → {dp.check_out}).", dp.index)

    def _validate_trip(self, plan: TripPlan) -> None:
        req = plan.request
        last = plan.destinations[-1]
        end = req.tripInfo.endDestinationDate.date()
        if last.check_out != end:
            plan.add("WARNING", "DATES", f"Last check-out ({last.check_out}) differs from trip end date ({end}).",
                     last.index)
        f = req.foodGuideEtc
        for flag, label in ((f.travelInsuranceReqd, "Travel insurance"), (f.esimReqd, "eSIM"),
                            (f.evisaReqd, "e-Visa")):
            if flag:
                plan.add("INFO", "ADD_ON", f"{label} requested — not a Hotelbeds product; add from the platform's "
                         "add-on catalogue.", None, True)
        meal = clean(f.mealPref)
        if meal and normalize(meal) not in ("no preference", "none", "any"):
            plan.add("INFO", "MEAL", f"Meal preference '{meal}' — Hotelbeds has no dietary filter; verify with the "
                     "hotel and activity suppliers.", None)
        if req.travelerInfo.isImmobile:
            plan.add("WARNING", "ACCESSIBILITY", "Traveller with reduced mobility — verify hotel accessibility and "
                     "request adapted vehicles; physically demanding activities were de-prioritised.", None, True)
        if f.guideLanguage and not plan.guide_language:
            plan.add("INFO", "ACTIVITY", f"Guide language '{f.guideLanguage}' not recognised — language matching "
                     "skipped.", None)
