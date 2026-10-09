"""/hotels — Hotelbeds Hotels Booking + Content API (docs at /hotels/docs)."""
from typing import Literal, Optional

from fastapi import Body, Depends, Path, Query

from app.apis.common import container, create_subapp
from app.container import Container
from app.fit.resolvers import LocationResolver
from app.hotelbeds.hotels import CONTENT_TYPES, image_url
from app.schemas.hotels import (CheckRatesRequest, HotelAvailabilityRequest, HotelBookingModifyRequest,
                                HotelBookingRequest)

DESCRIPTION = """
Direct access to the **Hotelbeds APItude Hotels** product.

**Booking flow (Hotelbeds best practice)**
1. `POST /api/v1/availability` — one search (destination code, hotel codes or geolocation).
2. `POST /api/v1/checkrates` — mandatory when the chosen rate has `rateType = RECHECK`
   (also returns rate comments + final cancellation policy).
3. `POST /api/v1/bookings` — confirm with the `rateKey` (never modify a rateKey).
4. `GET/PUT/DELETE /api/v1/bookings/{reference}` — detail, modify, cancel (`SIMULATION` first).

**Content** (`/api/v1/content/...`) is static data — Hotelbeds asks you to cache it, not call it per search.
`/api/v1/resolve/destination` turns a city name into a Hotelbeds destination code using the cached content.

Other products: [/fit/docs](/fit/docs) · [/activities/docs](/activities/docs) · [/transfers/docs](/transfers/docs)
"""

TAGS = [
    {"name": "Availability & CheckRate"},
    {"name": "Bookings"},
    {"name": "Content"},
    {"name": "Helpers"},
]

app = create_subapp(title="Hotelbeds Hotels API", description=DESCRIPTION, openapi_tags=TAGS)
P = "/api/v1"


@app.get(f"{P}/status", tags=["Helpers"], summary="Hotelbeds Hotels API status (GET /hotel-api/1.0/status)")
async def status(c: Container = Depends(container)):
    return await c.hotels.status()


# ── Availability & CheckRate ─────────────────────────────────────────
@app.post(f"{P}/availability", tags=["Availability & CheckRate"], summary="Hotel availability (POST /hotels)")
async def availability(req: HotelAvailabilityRequest, c: Container = Depends(container)):
    payload = req.payload()
    payload.setdefault("language", c.settings.HOTELBEDS_HOTEL_LANGUAGE)
    return await c.hotels.availability(payload)


@app.post(f"{P}/checkrates", tags=["Availability & CheckRate"], summary="Re-price rateKeys (POST /checkrates)")
async def checkrates(req: CheckRatesRequest, c: Container = Depends(container)):
    return await c.hotels.checkrates(req.payload())


# ── Bookings ─────────────────────────────────────────────────────────
@app.post(f"{P}/bookings", tags=["Bookings"], summary="Confirm booking (POST /bookings)")
async def booking_confirm(req: HotelBookingRequest, use_v12: bool = Query(False, description="Use /hotel-api/1.2"),
                          c: Container = Depends(container)):
    return await c.hotels.booking_confirm(req.payload(), use_v12=use_v12)


@app.get(f"{P}/bookings", tags=["Bookings"], summary="Booking list (GET /bookings)")
async def booking_list(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
    filterType: Literal["CREATION", "CHECKIN"] = Query("CREATION"),
    status: Optional[str] = Query(None, description="e.g. CONFIRMED, CANCELLED, ALL"),
    from_: int = Query(1, alias="from", ge=1),
    to: int = Query(25, ge=1),
    clientReference: Optional[str] = None,
    creationUser: Optional[str] = None,
    country: Optional[str] = None,
    destination: Optional[str] = None,
    hotel: Optional[str] = Query(None, description="comma-separated hotel codes"),
    c: Container = Depends(container),
):
    params = {"start": start, "end": end, "filterType": filterType, "status": status, "from": from_, "to": to,
              "clientReference": clientReference, "creationUser": creationUser, "country": country,
              "destination": destination, "hotel": hotel}
    return await c.hotels.booking_list(params)


@app.get(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Booking detail")
async def booking_detail(reference: str = Path(...), language: Optional[str] = None,
                         c: Container = Depends(container)):
    return await c.hotels.booking_detail(reference, language)


@app.put(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Booking modification (SIMULATION/UPDATE)")
async def booking_modify(req: HotelBookingModifyRequest, reference: str = Path(...),
                         c: Container = Depends(container)):
    return await c.hotels.booking_modify(reference, req.payload())


@app.delete(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Cancel booking (or simulate)")
async def booking_cancel(reference: str = Path(...),
                         cancellationFlag: Literal["SIMULATION", "CANCELLATION"] = Query("SIMULATION"),
                         language: Optional[str] = None, c: Container = Depends(container)):
    return await c.hotels.booking_cancel(reference, simulation=cancellationFlag == "SIMULATION", language=language)


# ── Content ──────────────────────────────────────────────────────────
def _content_params(language, from_, to, fields, use_secondary, last_update) -> dict:
    return {"language": language, "from": from_, "to": to, "fields": fields,
            "useSecondaryLanguage": use_secondary, "lastUpdateTime": last_update}


@app.get(f"{P}/content/hotels", tags=["Content"], summary="Hotel portfolio (GET /hotel-content-api/1.0/hotels)")
async def content_hotels(
    destinationCode: Optional[str] = None, countryCode: Optional[str] = None,
    codes: Optional[str] = Query(None, description="comma-separated hotel codes"),
    language: str = "ENG", fields: str = "all", from_: int = Query(1, alias="from", ge=1),
    to: int = Query(100, ge=1, le=1000), useSecondaryLanguage: Optional[bool] = None,
    lastUpdateTime: Optional[str] = None, c: Container = Depends(container),
):
    params = _content_params(language, from_, to, fields, useSecondaryLanguage, lastUpdateTime)
    params.update({"destinationCode": destinationCode, "countryCode": countryCode, "codes": codes})
    return await c.hotels.content_hotels(params)


@app.get(f"{P}/content/hotels/{{codes}}/details", tags=["Content"], summary="Hotel details (full content)")
async def content_hotel_details(codes: str, language: str = "ENG", useSecondaryLanguage: Optional[bool] = None,
                                c: Container = Depends(container)):
    return await c.hotels.content_hotel_details(codes, {"language": language,
                                                        "useSecondaryLanguage": useSecondaryLanguage})


@app.get(f"{P}/content/locations/countries", tags=["Content"], summary="Countries")
async def content_countries(language: str = "ENG", fields: str = "all", codes: Optional[str] = None,
                            from_: int = Query(1, alias="from", ge=1), to: int = Query(1000, ge=1, le=1000),
                            c: Container = Depends(container)):
    return await c.hotels.content_countries({"language": language, "fields": fields, "codes": codes,
                                             "from": from_, "to": to})


@app.get(f"{P}/content/locations/destinations", tags=["Content"], summary="Destinations (+ zones)")
async def content_destinations(countryCodes: Optional[str] = None, codes: Optional[str] = None,
                               language: str = "ENG", fields: str = "all",
                               from_: int = Query(1, alias="from", ge=1), to: int = Query(1000, ge=1, le=1000),
                               c: Container = Depends(container)):
    return await c.hotels.content_destinations({"countryCodes": countryCodes, "codes": codes, "language": language,
                                                "fields": fields, "from": from_, "to": to})


@app.get(f"{P}/content/types/{{type_name}}", tags=["Content"], summary="Master data (boards, categories, ...)")
async def content_type(
    type_name: Literal[CONTENT_TYPES],  # type: ignore[valid-type]
    language: str = "ENG", fields: str = "all", codes: Optional[str] = None,
    from_: int = Query(1, alias="from", ge=1), to: int = Query(1000, ge=1, le=1000),
    code: Optional[str] = Query(None, description="ratecommentdetails only: rateCommentsId"),
    date: Optional[str] = Query(None, description="ratecommentdetails only: check-in YYYY-MM-DD"),
    c: Container = Depends(container),
):
    return await c.hotels.content_type(type_name, {"language": language, "fields": fields, "codes": codes,
                                                   "from": from_, "to": to, "code": code, "date": date})


# ── Helpers ──────────────────────────────────────────────────────────
@app.get(f"{P}/resolve/destination", tags=["Helpers"],
         summary="City name → Hotelbeds destination code (+ zone), cached")
async def resolve_destination(city: str = Query(..., examples=["Dubai"]),
                              country: str = Query(..., examples=["United Arab Emirates"]),
                              area: Optional[str] = Query(None, description="Neighbourhood → zone code"),
                              c: Container = Depends(container)):
    resolver = LocationResolver(c)
    return await resolver.resolve_hotel_destination(city, [country], area=area)


@app.get(f"{P}/images/url", tags=["Helpers"], summary="Build a Hotelbeds image URL from a content `path`")
async def build_image_url(path: str, size: Literal["standard", "small", "medium", "bigger", "xl", "xxl",
                                                   "original"] = "bigger"):
    return {"url": image_url(path, size)}


@app.post(f"{P}/cache/purge", tags=["Helpers"], summary="Purge cached Hotels content")
async def purge_cache(prefix: str = Body("hotels:", embed=True), c: Container = Depends(container)):
    return {"purged": await c.cache.purge(prefix)}
