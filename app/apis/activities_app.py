"""/activities — Hotelbeds Activities Booking + Content + Cache API (docs at /activities/docs)."""
from typing import Literal, Optional

from fastapi import Body, Depends, Path, Query

from app.apis.common import container, create_subapp
from app.container import Container
from app.fit.resolvers import LocationResolver
from app.schemas.activities import (ActivityBookingRequest, ActivityContentMultiRequest, ActivityDetailsRequest,
                                    ActivityReconfirmRequest, ActivitySearchRequest)
from app.core.exceptions import ValidationFailed

DESCRIPTION = """
Direct access to the **Hotelbeds APItude Activities** product (tours, tickets, excursions).

**Booking flow (Hotelbeds best practice)**
1. `POST /api/v1/search` (light) or `POST /api/v1/availability` (complete, needs `paxes`) — max 60-day window,
   100 items per page. Filters inside one group are AND-ed, groups are OR-ed.
2. `POST /api/v1/details` — when the customer picks an activity: returns the **bookable `rateKey`**
   (valid 30 minutes, single use), sessions, languages and the **questions** that must be answered.
3. `PUT /api/v1/bookings` — confirm (or `/bookings/preconfirm` → `/bookings/reconfirm` to lock stock 30 min).
4. Booking detail / list / cancel (`SIMULATION` first to see the fee).

Other products: [/fit/docs](/fit/docs) · [/hotels/docs](/hotels/docs) · [/transfers/docs](/transfers/docs)
"""

TAGS = [{"name": "Availability & Details"}, {"name": "Bookings"}, {"name": "Content"}, {"name": "Cache"},
        {"name": "Helpers"}]

app = create_subapp(title="Hotelbeds Activities API", description=DESCRIPTION, openapi_tags=TAGS)
P = "/api/v1"


# ── Availability & Details ───────────────────────────────────────────
@app.post(f"{P}/search", tags=["Availability & Details"], summary="Search (POST /activities)")
async def search(req: ActivitySearchRequest, c: Container = Depends(container)):
    return await c.activities.search(req.payload())


@app.post(f"{P}/availability", tags=["Availability & Details"],
          summary="Availability (POST /activities/availability)")
async def availability(req: ActivitySearchRequest, c: Container = Depends(container)):
    if not req.paxes:
        raise ValidationFailed("paxes are required for availability (use /search without paxes)")
    return await c.activities.availability(req.payload())


@app.post(f"{P}/details", tags=["Availability & Details"], summary="Details / CheckRate → bookable rateKey")
async def details(req: ActivityDetailsRequest, full: bool = Query(False, description="Use /details/full"),
                  c: Container = Depends(container)):
    return await c.activities.details(req.payload(), full=full)


# ── Bookings ─────────────────────────────────────────────────────────
@app.put(f"{P}/bookings", tags=["Bookings"], summary="Confirm booking (PUT /bookings)")
async def booking_confirm(req: ActivityBookingRequest, c: Container = Depends(container)):
    return await c.activities.booking_confirm(req.payload())


@app.put(f"{P}/bookings/preconfirm", tags=["Bookings"], summary="Pre-confirm (locks stock for 30 min)")
async def booking_preconfirm(req: ActivityBookingRequest, c: Container = Depends(container)):
    return await c.activities.booking_preconfirm(req.payload())


@app.put(f"{P}/bookings/reconfirm", tags=["Bookings"], summary="Re-confirm a pre-confirmed booking")
async def booking_reconfirm(req: ActivityReconfirmRequest, c: Container = Depends(container)):
    return await c.activities.booking_reconfirm(req.payload())


@app.get(f"{P}/bookings", tags=["Bookings"], summary="Booking list")
async def booking_list(
    start: str = Query(..., description="YYYY-MM-DD"), end: str = Query(..., description="YYYY-MM-DD"),
    filterType: Literal["CHECKIN", "CREATION", "CANCELLATION"] = "CHECKIN",
    includedCancelled: bool = False, holder: Optional[str] = None,
    itemsPerPage: int = Query(10, ge=1, le=100), page: int = Query(1, ge=1), language: str = "en",
    c: Container = Depends(container),
):
    params = {"start": start, "end": end, "filterType": filterType,
              "includedCancelled": str(includedCancelled).lower(), "holder": holder,
              "itemsPerPage": itemsPerPage, "page": page}
    return await c.activities.booking_list(language, params)


@app.get(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Booking detail (incl. vouchers)")
async def booking_detail(reference: str = Path(...), language: str = "en", c: Container = Depends(container)):
    return await c.activities.booking_detail(reference, language)


@app.delete(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Cancel booking (or simulate)")
async def booking_cancel(reference: str = Path(...),
                         cancellationFlag: Literal["SIMULATION", "CANCELLATION"] = "SIMULATION",
                         language: str = "en", c: Container = Depends(container)):
    return await c.activities.booking_cancel(reference, language, simulation=cancellationFlag == "SIMULATION")


# ── Content ──────────────────────────────────────────────────────────
@app.get(f"{P}/content/countries", tags=["Content"])
async def content_countries(language: str = "en", c: Container = Depends(container)):
    return await c.activities.content_countries(language)


@app.get(f"{P}/content/destinations/{{countryCode}}", tags=["Content"])
async def content_destinations(countryCode: str = Path(..., min_length=2, max_length=2), language: str = "en",
                               c: Container = Depends(container)):
    return await c.activities.content_destinations(language, countryCode.upper())


@app.get(f"{P}/content/currencies", tags=["Content"])
async def content_currencies(language: str = "en", c: Container = Depends(container)):
    return await c.activities.content_currencies(language)


@app.get(f"{P}/content/languages", tags=["Content"])
async def content_languages(c: Container = Depends(container)):
    return await c.activities.content_languages()


@app.get(f"{P}/content/segments", tags=["Content"])
async def content_segments(language: str = "en", c: Container = Depends(container)):
    return await c.activities.content_segments(language)


@app.get(f"{P}/content/activities/{{activityCode}}", tags=["Content"], summary="Activity factsheet (single)")
async def content_activity(activityCode: str, modalityCode: Optional[str] = None, language: str = "en",
                           c: Container = Depends(container)):
    return await c.activities.content_activity(language, activityCode, modalityCode)


@app.post(f"{P}/content/activities", tags=["Content"], summary="Activity factsheets (multi, max 100)")
async def content_activities_multi(req: ActivityContentMultiRequest, c: Container = Depends(container)):
    return await c.activities.content_activities_multi(req.payload())


# ── Cache ────────────────────────────────────────────────────────────
@app.get(f"{P}/cache/portfolio", tags=["Cache"], summary="Portfolio per destination (paged)")
async def cache_portfolio(destination: str, offset: int = Query(1, ge=1), limit: int = Query(1000, ge=1, le=1000),
                          c: Container = Depends(container)):
    resp = await c.activities.cache_portfolio(destination, offset, limit)
    return {"totalCount": resp.header_int("X-Total-Count"), "offset": offset, "limit": limit, "items": resp.data}


# ── Helpers ──────────────────────────────────────────────────────────
@app.get(f"{P}/resolve/destination", tags=["Helpers"], summary="City name → Activities destination code")
async def resolve_destination(city: str = Query(..., examples=["Dubai"]),
                              country: str = Query(..., examples=["United Arab Emirates"]),
                              c: Container = Depends(container)):
    return await LocationResolver(c).resolve_activity_destination(city, [country])


@app.post(f"{P}/cache/purge", tags=["Helpers"], summary="Purge cached Activities content")
async def purge_cache(prefix: str = Body("activities:", embed=True), c: Container = Depends(container)):
    return {"purged": await c.cache.purge(prefix)}
