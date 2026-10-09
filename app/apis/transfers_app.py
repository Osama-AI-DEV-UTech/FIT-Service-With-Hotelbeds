"""/transfers — Hotelbeds Transfers Booking + Cache API (docs at /transfers/docs)."""
from typing import List, Literal, Optional

from fastapi import Body, Depends, Path, Query

from app.apis.common import container, create_subapp
from app.container import Container
from app.fit.resolvers import LocationResolver
from app.schemas.transfers import LocationType, TransferAmendRequest, TransferBookingRequest, TransferRoute

DESCRIPTION = """
Direct access to the **Hotelbeds APItude Transfers** product.

**Location types:** `IATA` (airport, e.g. DXB) · `ATLAS` (Hotelbeds hotel code) · `GPS` (`lat,lng`) ·
`PORT` · `STATION`. Not supported: IATA→IATA, PORT→PORT, STATION→STATION, hotel/GPS→hotel/GPS.

**What the date-time means:** from an airport/station = flight/train **arrival** time; to an airport/station =
flight/train **departure** time (Hotelbeds calculates the pickup time); to/from a port = desired pickup / vessel
arrival time. Format `YYYY-MM-DDThh:mm:ss`, local time.

**Booking flow:** availability → `POST /api/v1/bookings` with the `rateKey` and `transferDetails`
(flight/train/cruise number is **mandatory**) → detail / list / cancel (simulate first) / amend.

Other products: [/fit/docs](/fit/docs) · [/hotels/docs](/hotels/docs) · [/activities/docs](/activities/docs)
"""

TAGS = [{"name": "Availability"}, {"name": "Bookings"}, {"name": "Cache / Content"}, {"name": "Helpers"}]

app = create_subapp(title="Hotelbeds Transfers API", description=DESCRIPTION, openapi_tags=TAGS)
P = "/api/v1"


# ── Availability ─────────────────────────────────────────────────────
@app.get(f"{P}/availability", tags=["Availability"], summary="Availability one-way or round trip")
async def availability(
    fromType: LocationType = Query(..., examples=["IATA"]),
    fromCode: str = Query(..., examples=["DXB"]),
    toType: LocationType = Query(..., examples=["ATLAS"]),
    toCode: str = Query(..., examples=["87376"]),
    outbound: str = Query(..., description="YYYY-MM-DDThh:mm:ss", examples=["2026-12-10T14:30:00"]),
    inbound: Optional[str] = Query(None, description="Round trip return date-time (YYYY-MM-DDThh:mm:ss)"),
    adults: int = Query(..., ge=1, le=50),
    children: int = Query(0, ge=0, le=50),
    infants: int = Query(0, ge=0, le=50),
    language: str = "en",
    c: Container = Depends(container),
):
    return await c.transfers.availability(language=language, from_type=fromType, from_code=fromCode,
                                          to_type=toType, to_code=toCode, outbound=outbound, inbound=inbound,
                                          adults=adults, children=children, infants=infants)


@app.post(f"{P}/availability/routes", tags=["Availability"], summary="Availability for several routes (max 20)")
async def availability_routes(
    routes: List[TransferRoute] = Body(..., min_length=1, max_length=20),
    adults: int = Query(..., ge=1), children: int = Query(0, ge=0), infants: int = Query(0, ge=0),
    language: str = "en", allowPartialResults: Optional[bool] = None, vehicle: Optional[str] = None,
    type: Optional[str] = Query(None, description="Transfer type code: PRVT, SHRD, SHTL"),
    category: Optional[str] = None, c: Container = Depends(container),
):
    params = {"allowPartialResults": None if allowPartialResults is None else str(allowPartialResults).lower(),
              "vehicle": vehicle, "type": type, "category": category}
    return await c.transfers.availability_routes(language=language, adults=adults, children=children,
                                                 infants=infants, routes=[r.payload() for r in routes],
                                                 params=params)


# ── Bookings ─────────────────────────────────────────────────────────
@app.post(f"{P}/bookings", tags=["Bookings"], summary="Confirm booking")
async def booking_confirm(req: TransferBookingRequest, c: Container = Depends(container)):
    return await c.transfers.booking_confirm(req.payload())


@app.get(f"{P}/bookings", tags=["Bookings"], summary="Booking list (max 31-day range)")
async def booking_list(fromDate: str = Query(..., description="YYYY-MM-DD"),
                       toDate: str = Query(..., description="YYYY-MM-DD"),
                       dateType: Literal["FROM_DATE", "CREATION_DATE"] = "FROM_DATE",
                       offset: Optional[int] = Query(None, ge=0), limit: Optional[int] = Query(None, ge=1),
                       language: str = "en", c: Container = Depends(container)):
    return await c.transfers.booking_list(language, {"fromDate": fromDate, "toDate": toDate, "dateType": dateType,
                                                     "offset": offset, "limit": limit})


@app.get(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Booking detail (incl. vouchers)")
async def booking_detail(reference: str = Path(...), language: str = "en", c: Container = Depends(container)):
    return await c.transfers.booking_detail(reference, language)


@app.delete(f"{P}/bookings/{{reference}}", tags=["Bookings"], summary="Cancel booking (simulation=true first)")
async def booking_cancel(reference: str = Path(...), simulation: bool = True, language: str = "en",
                         c: Container = Depends(container)):
    return await c.transfers.booking_cancel(reference, language, simulation=simulation)


@app.delete(f"{P}/bookings/{{reference}}/services/{{serviceId}}", tags=["Bookings"],
            summary="Cancel ONE service of a booking (partial cancellation)")
async def booking_cancel_service(reference: str, serviceId: str, simulation: bool = True, language: str = "en",
                                 c: Container = Depends(container)):
    return await c.transfers.booking_cancel(reference, language, simulation=simulation, service_id=serviceId)


@app.put(f"{P}/bookings/amend", tags=["Bookings"], summary="Amend holder / flight details / pickup / location")
async def booking_amend(req: TransferAmendRequest, c: Container = Depends(container)):
    return await c.transfers.booking_amend(req.payload())


# ── Cache / Content ──────────────────────────────────────────────────
def _paged(resp) -> dict:
    return {"totalCount": resp.header_int("X-Total-Count"), "items": resp.data}


@app.get(f"{P}/cache/locations/countries", tags=["Cache / Content"])
async def countries(language: str = "en", codes: Optional[str] = None, offset: Optional[int] = None,
                    limit: Optional[int] = None, c: Container = Depends(container)):
    return _paged(await c.transfers.cache("locations/countries", {"fields": "ALL", "language": language,
                                                                   "codes": codes, "offset": offset, "limit": limit}))


@app.get(f"{P}/cache/locations/destinations", tags=["Cache / Content"])
async def destinations(countryCodes: str, language: str = "en", codes: Optional[str] = None,
                       offset: Optional[int] = None, limit: Optional[int] = None, c: Container = Depends(container)):
    return _paged(await c.transfers.cache("locations/destinations", {
        "fields": "ALL", "language": language, "countryCodes": countryCodes, "codes": codes,
        "offset": offset, "limit": limit}))


@app.get(f"{P}/cache/locations/terminals", tags=["Cache / Content"])
async def terminals(language: str = "en", countryCodes: Optional[str] = None, codes: Optional[str] = None,
                    offset: Optional[int] = None, limit: Optional[int] = None, c: Container = Depends(container)):
    return _paged(await c.transfers.cache("locations/terminals", {
        "fields": "ALL", "language": language, "countryCodes": countryCodes, "codes": codes,
        "offset": offset, "limit": limit}))


@app.get(f"{P}/cache/hotels", tags=["Cache / Content"])
async def hotels(language: str = "en", countryCodes: Optional[str] = None, destinationCodes: Optional[str] = None,
                 codes: Optional[str] = None, giataCodes: Optional[str] = None, offset: Optional[int] = None,
                 limit: Optional[int] = None, c: Container = Depends(container)):
    return _paged(await c.transfers.cache("hotels", {
        "fields": "ALL", "language": language, "countryCodes": countryCodes, "destinationCodes": destinationCodes,
        "codes": codes, "giataCodes": giataCodes, "offset": offset, "limit": limit}))


@app.get(f"{P}/cache/routes", tags=["Cache / Content"])
async def routes(destinationCode: str, offset: int = 0, limit: int = Query(1000, ge=1, le=1000),
                 c: Container = Depends(container)):
    return _paged(await c.transfers.cache("routes", {"fields": "ALL", "destinationCode": destinationCode,
                                                     "offset": offset, "limit": limit}))


@app.get(f"{P}/cache/masters/{{master}}", tags=["Cache / Content"])
async def masters(master: Literal["categories", "vehicles", "transferTypes"], language: str = "en",
                  c: Container = Depends(container)):
    return _paged(await c.transfers.cache(f"masters/{master}", {"fields": "ALL", "language": language}))


@app.get(f"{P}/cache/currencies", tags=["Cache / Content"])
async def currencies(language: str = "en", c: Container = Depends(container)):
    return _paged(await c.transfers.cache("currencies", {"fields": "ALL", "language": language}))


# ── Helpers ──────────────────────────────────────────────────────────
@app.get(f"{P}/resolve/terminal", tags=["Helpers"],
         summary="Free text → transfer terminal (IATA from offline airport DB, PORT/STATION from cache)")
async def resolve_terminal(text: str = Query(..., examples=["Dubai International Airport"]),
                           mode: Literal["FLIGHT", "TRAIN", "FERRY", "CRUISE"] = "FLIGHT",
                           country: Optional[str] = None, c: Container = Depends(container)):
    return await LocationResolver(c).resolve_terminal(text, mode=mode, countries=[country] if country else [])


@app.post(f"{P}/cache/purge", tags=["Helpers"], summary="Purge cached Transfers content")
async def purge_cache(prefix: str = Body("transfers:", embed=True), c: Container = Depends(container)):
    return {"purged": await c.cache.purge(prefix)}
